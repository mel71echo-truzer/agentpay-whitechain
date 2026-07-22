"""Сервіс Фотобанку (агент-продавець) — Частина 2 з ТЗ.

FastAPI-сервер, що віддає фото, але тільки після оплати:
- GET /photo/{name}   без оплати -> 402 Payment Required з інструкцією
                        з оплатою (заголовок X-PAYMENT)  -> фото
- GET /balance        скільки Фотобанк заробив
- GET /photos         список доступних фото і їх ціна

x402 (спрощена схема "exact-native", див. config.py): сервер описує вимогу
оплати в тілі 402-відповіді (payTo, сума, мережа). Клієнт сам робить
нативний WBT-переказ (wallets.send_wbt), отримує tx hash і повторює запит
із заголовком `X-PAYMENT: base64(json({"txHash": ...}))`. Сервер віддає
цей tx hash facilitator-у (facilitator/whitechain_facilitator.py), і той
каже — платили чи ні.
"""

import base64
import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from facilitator.whitechain_facilitator import WhitechainFacilitator  # noqa: E402

app = FastAPI(title="Photobank — x402 seller agent")

IMAGES_DIR = Path(__file__).resolve().parent / "images"

# Суворий whitelist для {name}: тільки латиниця/цифри/дефіс/підкреслення.
# FastAPI/Starlette і так не пускають "/" у {name}, але ми не покладаємось
# лише на це — явна перевірка тут не дає жодному майбутньому рефакторингу
# (наприклад заміні на {name:path}) випадково відкрити path traversal.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# X-PAYMENT — це недовірений вхід від клієнта; реальний payload крихітний
# (JSON з одним полем txHash), тож обмежуємо розмір з запасом.
_MAX_PAYMENT_HEADER_LEN = 4096

facilitator = WhitechainFacilitator()

# Стан Фотобанку тримаємо в пам'яті процесу — для MVP-демо цього достатньо
# (run_demo.py піднімає сервер і одразу з ним працює в межах одного запуску).
_ledger = {"earned_wbt": 0.0, "sales": []}


def _payment_requirements(photo_name: str) -> dict:
    """Формує x402 payment requirements — те, що йде в тілі 402-відповіді."""
    from web3 import Web3

    return {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": config.X402_SCHEME,
                "network": config.X402_NETWORK,
                "payTo": config.PHOTOBANK_WALLET_ADDRESS,
                "maxAmountRequired": str(Web3.to_wei(config.PHOTO_PRICE_WBT, "ether")),
                "amount_wbt": config.PHOTO_PRICE_WBT,
                "asset": "WBT",
                "resource": f"/photo/{photo_name}",
                "description": f"Photo: {photo_name}",
                "mimeType": "image/png",
            }
        ],
    }


def _decode_payment_header(x_payment: str) -> dict | None:
    """Декодує заголовок X-PAYMENT (base64 JSON) у dict з полем txHash (рядок).

    Повертає None для будь-якого некоректного/завеликого/неочікуваного
    вмісту — заголовок повністю недовірений вхід від клієнта.
    """
    if len(x_payment) > _MAX_PAYMENT_HEADER_LEN:
        return None
    try:
        raw = base64.b64decode(x_payment)
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001 — клієнт міг надіслати сміття
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("txHash"), str):
        return None
    return parsed


@app.get("/photos")
def list_photos():
    """Список доступних фото і ціна за штуку."""
    names = sorted(p.stem for p in IMAGES_DIR.glob("*.png"))
    return {"price_wbt": config.PHOTO_PRICE_WBT, "photos": names}


@app.get("/balance")
def balance():
    """Скільки Фотобанк заробив за цей запуск."""
    return {
        "address": config.PHOTOBANK_WALLET_ADDRESS,
        "earned_wbt": round(_ledger["earned_wbt"], 6),
        "sales_count": len(_ledger["sales"]),
        "sales": _ledger["sales"],
    }


@app.get("/photo/{name}")
def get_photo(name: str, request: Request, x_payment: str | None = Header(default=None)):
    """Віддає фото name.png — але тільки після оплати через x402."""
    if not _SAFE_NAME.fullmatch(name):
        return JSONResponse(status_code=404, content={"error": "Фото не знайдено."})

    image_path = IMAGES_DIR / f"{name}.png"
    if not image_path.exists():
        return JSONResponse(status_code=404, content={"error": f"Фото '{name}' не знайдено."})

    if x_payment is None:
        return JSONResponse(
            status_code=402,
            content=_payment_requirements(name),
        )

    payment = _decode_payment_header(x_payment)
    if payment is None:
        return JSONResponse(
            status_code=400,
            content={"error": "Заголовок X-PAYMENT некоректний. Очікується base64 JSON з полем txHash."},
        )

    result = facilitator.verify_payment(
        tx_hash=payment["txHash"],
        expected_to=config.PHOTOBANK_WALLET_ADDRESS,
        expected_amount_wbt=config.PHOTO_PRICE_WBT,
    )

    if not result["valid"]:
        return JSONResponse(
            status_code=402,
            content={**_payment_requirements(name), "error": result["reason"]},
        )

    _ledger["earned_wbt"] += result["amount_wbt"]
    _ledger["sales"].append(
        {
            "photo": name,
            "amount_wbt": result["amount_wbt"],
            "from": result["from"],
            "txHash": payment["txHash"],
        }
    )

    settlement = base64.b64encode(
        json.dumps({"txHash": payment["txHash"], "settled": True}).encode()
    ).decode()

    return FileResponse(
        image_path,
        media_type="image/png",
        headers={"X-PAYMENT-RESPONSE": settlement},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.PHOTOBANK_HOST, port=config.PHOTOBANK_PORT)
