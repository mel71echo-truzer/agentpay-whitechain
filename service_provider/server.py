"""AI Service Provider — сервіс, що продає ресурси за x402/EIP-3009 (Фаза 1).

Раніше цей модуль називався "Фотобанк" — перейменовано, бо "Photobank"
звучить як одноразове демо, а "AI Service Provider" натякає на платформу:
той самий сервер міг би продавати будь-які дані/обчислення агентам, не
тільки фото. Фото лишились як конкретний демонстраційний товар.

Потік (Фаза 1, EIP-3009, без очікування майнінгу в циклі запиту):
- GET  /photo/{name}  без авторизації  -> 402 Payment Required
                       {payTo (= адреса facilitator-а), price_teurc,
                        resource, min_reputation_tier, asset_address}
- POST /photo/{name}  {authorization, resource, resource_salt}
                       -> facilitator.verify_and_settle(...):
                          KYA-гейт -> reputation-гейт -> прив'язка до
                          ресурсу -> офчейн-підпис -> anti-replay -> сума
                          -> релей у мережу (без очікування receipt)
                       -> 200 + фото, або 402 з причиною відмови
- GET  /photos        список фото, цін і яке з них преміум (потребує SBT)
- GET  /balance       скільки AI Service Provider заробив (нетто, після комісії)

Одне фото (PREMIUM_RESOURCE_NAMES) навмисно позначене преміум-ресурсом,
щоб демо могло показати reputation-шар у дії: агент з Soul, але без
потрібного SBT-tier, отримає відмову "insufficient reputation" саме на
ньому, і водночас вільно купує звичайні фото.
"""

import base64
import json
import logging
import re
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import money  # noqa: E402
from facilitator.capability import CapabilityRegistry  # noqa: E402
from facilitator.whitechain_facilitator import WhitechainFacilitator  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Service Provider — x402 seller agent")

IMAGES_DIR = Path(__file__).resolve().parent / "images"

# Суворий whitelist для {name}: тільки латиниця/цифри/дефіс/підкреслення
# (захист від path traversal — див. SECURITY_REVIEW.md).
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# Один преміум-ресурс для демонстрації reputation-гейту.
PREMIUM_RESOURCE_NAMES = {"kyiv-motherland-monument"}

_MAX_BODY_FIELD_LEN = 4096

# Facilitator НЕ створюється тут при простому імпорті модуля (потребує
# робочого RPC — див. Фазу 0 M-3). Реальний запуск ініціалізує його явно —
# `if __name__ == "__main__"` нижче, або scripts/demo.py.
facilitator: WhitechainFacilitator | None = None

# Capability Registry (service discovery) — ділить store з facilitator-ом.
# Ініціалізується разом із facilitator через init_facilitator().
capability_registry: CapabilityRegistry | None = None

# Стан AI Service Provider тримаємо в пам'яті процесу — для PoC-демо цього
# достатньо. Заробіток — int wei (рішення №4): без float-накопичення/дрейфу.
_ledger = {"earned_wei": 0, "sales": []}


def init_facilitator(fac: WhitechainFacilitator) -> None:
    """Прив'язує facilitator і піднімає Capability Registry на його store,
    засіваючи один запис — цей AI Service Provider. Викликається реальним
    запуском (`__main__`) і scripts/demo.py."""
    global facilitator, capability_registry
    facilitator = fac
    capability_registry = CapabilityRegistry(fac.store)
    capability_registry.register(
        {
            "id": "ai-service-provider-1",
            "capability_type": config.CAPABILITY_TYPE,
            "provider_url": config.SERVICE_PROVIDER_BASE_URL,
            "price_wei": config.RESOURCE_PRICE_WEI,
            "min_reputation_tier": 0,
            "active": True,
        }
    )


def _price_and_tier_for(name: str) -> tuple[int, int]:
    """Повертає (price_wei, min_reputation_tier). Ціна — int wei."""
    if name in PREMIUM_RESOURCE_NAMES:
        return config.PREMIUM_RESOURCE_PRICE_WEI, config.PREMIUM_MIN_REPUTATION_TIER
    return config.RESOURCE_PRICE_WEI, 0


def _payment_requirements(resource_path: str, price_wei: int, min_reputation_tier: int) -> dict:
    """Формує x402 payment requirements — тіло 402-відповіді.

    B3: авторитетна ціна — int `price_wei`; `price_teurc` (рядок) лишається
    для показу людині. Клієнт підписує суму саме за `price_wei`."""
    return {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": "exact-eip3009",
                "network": f"whitechain-{config.NETWORK}-{config.CHAIN_ID}",
                "payTo": config.FACILITATOR_WALLET_ADDRESS,
                "asset": "tEURC",
                "asset_address": config.TEURC_ADDRESS,
                "price_wei": price_wei,
                "price_teurc": money.wei_to_teurc_str(price_wei, config.TEURC_DECIMALS),
                "resource": resource_path,
                "min_reputation_tier": min_reputation_tier,
                "description": f"Resource: {resource_path}",
            }
        ],
    }


def _resolve_image(name: str) -> Path | None:
    if not _SAFE_NAME.fullmatch(name):
        return None
    image_path = IMAGES_DIR / f"{name}.png"
    return image_path if image_path.exists() else None


@app.get("/registry/capabilities")
def registry_capabilities(type: str | None = None):
    """Service discovery: список активних можливостей (опц. фільтр за type)."""
    if capability_registry is None:
        return JSONResponse(status_code=503, content={"error": "Реєстр ще не ініціалізований."})
    return {"capabilities": capability_registry.list(capability_type=type)}


@app.post("/registry/register")
async def registry_register(request: Request):
    """Провайдер публікує можливість: {id, capability_type, provider_url, price, min_reputation_tier, active}."""
    if capability_registry is None:
        return JSONResponse(status_code=503, content={"error": "Реєстр ще не ініціалізований."})
    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_FIELD_LEN:
        return JSONResponse(status_code=400, content={"error": "Тіло запиту завелике."})
    try:
        record = json.loads(raw_body)
        normalized = capability_registry.register(record)
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": f"Некоректний запис можливості: {exc}"})
    except Exception:  # noqa: BLE001 — недовірений вхід; ніколи не 500
        return JSONResponse(status_code=400, content={"error": "Не вдалося зареєструвати можливість."})
    return {"registered": normalized}


@app.get("/photos")
def list_photos():
    """Список доступних фото, ціни і які з них преміум."""
    names = sorted(p.stem for p in IMAGES_DIR.glob("*.png"))
    dec = config.TEURC_DECIMALS
    return {
        "price_wei": config.RESOURCE_PRICE_WEI,
        "price_teurc": money.wei_to_teurc_str(config.RESOURCE_PRICE_WEI, dec),
        "premium_price_wei": config.PREMIUM_RESOURCE_PRICE_WEI,
        "premium_price_teurc": money.wei_to_teurc_str(config.PREMIUM_RESOURCE_PRICE_WEI, dec),
        "premium_min_reputation_tier": config.PREMIUM_MIN_REPUTATION_TIER,
        "premium_resources": sorted(PREMIUM_RESOURCE_NAMES),
        "photos": names,
    }


@app.get("/balance")
def balance():
    """Скільки AI Service Provider заробив (нетто, після комісії facilitator-а)."""
    return {
        "address": config.SERVICE_PROVIDER_WALLET_ADDRESS,
        "earned_wei": _ledger["earned_wei"],
        "earned_teurc": money.wei_to_teurc_str(_ledger["earned_wei"], config.TEURC_DECIMALS),
        "sales_count": len(_ledger["sales"]),
        "sales": _ledger["sales"],
    }


@app.get("/photo/{name}")
def request_photo(name: str):
    """Без доказу оплати — завжди 402 (безкоштовного доступу немає)."""
    image_path = _resolve_image(name)
    if image_path is None:
        return JSONResponse(status_code=404, content={"error": "Фото не знайдено."})

    price, min_tier = _price_and_tier_for(name)
    return JSONResponse(status_code=402, content=_payment_requirements(f"/photo/{name}", price, min_tier))


@app.post("/photo/{name}")
async def pay_for_photo(name: str, request: Request):
    """{"authorization": {...}, "resource": "...", "resource_salt": "0x..."} -> фото або 402."""
    image_path = _resolve_image(name)
    if image_path is None:
        return JSONResponse(status_code=404, content={"error": "Фото не знайдено."})

    raw_body = await request.body()
    if len(raw_body) > _MAX_BODY_FIELD_LEN:
        return JSONResponse(status_code=400, content={"error": "Тіло запиту завелике."})
    try:
        body = json.loads(raw_body)
    except Exception:  # noqa: BLE001 — клієнт міг надіслати сміття
        return JSONResponse(status_code=400, content={"error": "Тіло запиту не є коректним JSON."})

    resource_path = f"/photo/{name}"
    authorization = body.get("authorization") if isinstance(body, dict) else None
    resource_salt = body.get("resource_salt") if isinstance(body, dict) else None
    resource_field = body.get("resource") if isinstance(body, dict) else None

    required_auth_fields = {"from", "to", "value", "validAfter", "validBefore", "nonce", "v", "r", "s"}
    if (
        not isinstance(authorization, dict)
        or not required_auth_fields.issubset(authorization)
        or not isinstance(resource_salt, str)
        or resource_field != resource_path
    ):
        return JSONResponse(
            status_code=400,
            content={"error": "Некоректне тіло запиту: очікується authorization + resource + resource_salt."},
        )

    if facilitator is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Сервер ще не готовий (facilitator не ініціалізований)."},
        )

    price, min_tier = _price_and_tier_for(name)

    try:
        result = facilitator.verify_and_settle(
            authorization, resource_path, resource_salt, price, min_reputation_tier=min_tier
        )
    except Exception as exc:  # noqa: BLE001 — недовірений вхід; ніколи не 500 клієнту
        logger.warning("verify_and_settle: unexpected error for resource=%s: %s", resource_path, exc)
        return JSONResponse(status_code=400, content={"error": "Не вдалося обробити авторизацію."})

    if not result["valid"]:
        return JSONResponse(
            status_code=402,
            content={
                **_payment_requirements(resource_path, price, min_tier),
                "error": result["reason"],
                "reputation_tier": result.get("reputation_tier"),
            },
        )

    # Заробіток рахуємо в int wei (авторитетне net_wei), без float-накопичення.
    _ledger["earned_wei"] += result["net_wei"]
    _ledger["sales"].append(
        {
            "photo": name,
            "amount_wei": result["amount_wei"],
            "fee_wei": result["fee_wei"],
            "amount_teurc": result["amount_teurc"],
            "fee_teurc": result["fee_teurc"],
            "from": authorization["from"],
            "reputation_tier": result["reputation_tier"],
            "relay_tx_hash": result["relay_tx_hash"],
        }
    )

    settlement_b64 = base64.b64encode(json.dumps(result).encode()).decode()

    return FileResponse(image_path, media_type="image/png", headers={"X-Settlement": settlement_b64})


if __name__ == "__main__":
    import uvicorn

    init_facilitator(WhitechainFacilitator())  # fail fast тут, якщо RPC/chain не той
    uvicorn.run(app, host=config.SERVICE_PROVIDER_HOST, port=config.SERVICE_PROVIDER_PORT)
