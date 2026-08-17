import os
import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from x402 import common

PAY_TO = os.getenv("PAY_TO_ADDRESS", "")
TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", common.TOKEN_ADDRESS)

FACILITATOR_URL = os.getenv(
    "FACILITATOR_URL",
    "http://127.0.0.1:8001"
)

PRICE = 10000
NETWORK = "whitechain-testnet"

app = FastAPI(title="AgentPay Translator")


@app.get("/")
def root():
    return {
        "service": "AgentPay Translator",
        "status": "online",
        "price": "0.01 apUSD",
    }


@app.post("/translate")
async def translate(request: Request):
    payment_header = request.headers.get("X-PAYMENT")

    # 1. Немає payment → HTTP 402
    if not payment_header:
        return JSONResponse(
            status_code=402,
            content={
                "x402Version": 1,
                "accepts": [{
                    "scheme": "exact",
                    "network": NETWORK,
                    "maxAmountRequired": str(PRICE),
                    "resource": "/translate",
                    "description": "Translate text to Ukrainian",
                    "payTo": PAY_TO,
                    "asset": TOKEN_ADDRESS,
                    "extra": {
                        "name": common.TOKEN_NAME,
                        "version": common.TOKEN_VERSION,
                    },
                }],
            },
        )

    # 2. Розбираємо X-PAYMENT
    try:
        payment = common.decode_payment_header(payment_header)

        auth = payment["payload"]["authorization"]
        signature = payment["payload"]["signature"]

    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid payment header",
                "detail": str(e),
            },
        )

    # 3. Перевіряємо базові параметри payment
    if auth["to"].lower() != PAY_TO.lower():
        return JSONResponse(
            status_code=402,
            content={"error": "wrong recipient"},
        )

    if int(auth["value"]) < PRICE:
        return JSONResponse(
            status_code=402,
            content={"error": "insufficient payment"},
        )

    # 4. Передаємо authorization Facilitator
    try:
        async with httpx.AsyncClient(timeout=140) as client:
            response = await client.post(
                f"{FACILITATOR_URL}/settle",
                json={
                    "authorization": auth,
                    "signature": signature,
                },
            )

            result = response.json()

    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={
                "error": "facilitator unavailable",
                "detail": str(e),
            },
        )

    # 5. Settlement не пройшов
    if not result.get("success"):
        return JSONResponse(
            status_code=402,
            content={
                "error": "settlement failed",
                "detail": result,
            },
        )

    # 6. Платіж реально підтверджений
    body = await request.json()

    text = body.get("text", "")
    target_language = body.get("target_language", "uk")

    # Тимчасовий mock translation.
    # Реальний translation engine підключимо після E2E payment.
    if target_language == "uk" and text == "Hello world":
        translated = "привіт, світ"
    else:
        translated = f"[translated to {target_language}] {text}"

    return {
        "original": text,
        "translated": translated,
        "target_language": target_language,
        "paid": True,
        "txHash": result["txHash"],
        "explorer": result["explorer"],
    }