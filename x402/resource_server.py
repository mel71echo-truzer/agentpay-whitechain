"""
Resource server — "продавець" контенту за x402.
Віддає платний ресурс лише після оплати:
  1) без оплати -> HTTP 402 Payment Required + опис вимог (скільки і кому платити)
  2) з коректним заголовком X-PAYMENT -> звертається до facilitator /settle,
     і якщо переказ пройшов on-chain -> віддає контент.

Запуск:  uvicorn x402.resource_server:app --port 8002
Потрібно у .env: TOKEN_ADDRESS, PAY_TO_ADDRESS, FACILITATOR_URL
"""
import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from x402 import common

TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", common.TOKEN_ADDRESS)
PAY_TO = os.getenv("PAY_TO_ADDRESS", "")             # адреса, яка отримує оплату
PRICE = int(os.getenv("PRICE_UNITS", "10000"))       # 0.01 apUSD (6 знаків) за замовчуванням
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "http://127.0.0.1:8001")
NETWORK = "whitechain-testnet"

app = FastAPI(title="AgentPay Resource Server")


def payment_requirements() -> dict:
    """Опис умов оплати, що повертається у відповіді 402 (стиль x402)."""
    return {
        "x402Version": 1,
        "accepts": [{
            "scheme": "exact",
            "network": NETWORK,
            "maxAmountRequired": str(PRICE),
            "resource": "/premium",
            "description": "Одне преміум-фото з фотобанку AgentPay",
            "payTo": PAY_TO,
            "asset": TOKEN_ADDRESS,
            "extra": {"name": common.TOKEN_NAME, "version": common.TOKEN_VERSION},
        }],
    }


@app.get("/premium")
async def premium(request: Request):
    header = request.headers.get("X-PAYMENT")
    if not header:
        # Немає оплати — просимо заплатити.
        return JSONResponse(status_code=402, content=payment_requirements())

    payment = common.decode_payment_header(header)
    auth = payment["payload"]["authorization"]
    signature = payment["payload"]["signature"]

    # Проста перевірка, що платять куди і скільки треба.
    if auth["to"].lower() != PAY_TO.lower() or int(auth["value"]) < PRICE:
        return JSONResponse(status_code=402, content={"error": "wrong recipient or amount",
                                                      **payment_requirements()})

    # Просимо facilitator виконати переказ on-chain.
    async with httpx.AsyncClient(timeout=140) as client:
        r = await client.post(f"{FACILITATOR_URL}/settle",
                              json={"authorization": auth, "signature": signature})
        result = r.json()

    if not result.get("success"):
        return JSONResponse(status_code=402, content={"error": "settlement failed", "detail": result})

    # Оплата пройшла — віддаємо контент і квитанцію.
    return {
        "content": "🖼️  Ось твоє преміум-фото: https://picsum.photos/seed/agentpay/800/600",
        "paid": True,
        "txHash": result["txHash"],
        "explorer": result["explorer"],
    }


@app.get("/")
def root():
    return {"service": "AgentPay Resource Server", "price_units": PRICE, "payTo": PAY_TO}
