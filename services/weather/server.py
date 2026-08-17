import httpx

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from x402 import common


app = FastAPI(title="AgentPay Weather Service")


FACILITATOR_URL = "http://127.0.0.1:8001"

PRICE = 5000

PAY_TO = "0xF1d51d170Eb5b492196b1E01d6885de9ece81255"

TOKEN_ADDRESS = common.TOKEN_ADDRESS

NETWORK = "whitechain-testnet"


def payment_requirements():
    return {
        "x402Version": 1,
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK,
                "maxAmountRequired": str(PRICE),
                "resource": "/weather",
                "description": "Get weather information",
                "payTo": PAY_TO,
                "asset": TOKEN_ADDRESS,
                "extra": {
                    "name": common.TOKEN_NAME,
                    "version": common.TOKEN_VERSION,
                },
            }
        ],
    }


@app.get("/")
def root():
    return {
        "service": "AgentPay Weather",
        "status": "online",
        "price": "0.005 apUSD",
    }


@app.get("/weather")
async def weather(request: Request):

    # ---------------------------------------------------------
    # 1. Перевіряємо X-PAYMENT
    # ---------------------------------------------------------

    header = request.headers.get("X-PAYMENT")

    if not header:
        return JSONResponse(
            status_code=402,
            content=payment_requirements(),
        )

    try:

        # -----------------------------------------------------
        # 2. Розбираємо платіж
        # -----------------------------------------------------

        payment = common.decode_payment_header(header)

        auth = payment["payload"]["authorization"]
        signature = payment["payload"]["signature"]

        # -----------------------------------------------------
        # 3. Перевіряємо отримувача
        # -----------------------------------------------------

        if auth["to"].lower() != PAY_TO.lower():
            return JSONResponse(
                status_code=402,
                content={
                    "error": "wrong recipient",
                    **payment_requirements(),
                },
            )

        # -----------------------------------------------------
        # 4. Перевіряємо суму
        # -----------------------------------------------------

        if int(auth["value"]) < PRICE:
            return JSONResponse(
                status_code=402,
                content={
                    "error": "wrong amount",
                    **payment_requirements(),
                },
            )

        # -----------------------------------------------------
        # 5. Передаємо authorization Facilitator
        # -----------------------------------------------------

        async with httpx.AsyncClient(timeout=140) as client:

            response = await client.post(
                f"{FACILITATOR_URL}/settle",
                json={
                    "authorization": auth,
                    "signature": signature,
                },
            )

        # -----------------------------------------------------
        # 6. Перевіряємо відповідь Facilitator
        # -----------------------------------------------------

        if response.status_code != 200:
            return JSONResponse(
                status_code=502,
                content={
                    "error": "facilitator unavailable",
                    "detail": response.text,
                },
            )

        result = response.json()

        if not result.get("success"):
            return JSONResponse(
                status_code=402,
                content={
                    "error": "settlement failed",
                    "detail": result,
                },
            )

        # -----------------------------------------------------
        # 7. Повертаємо платний ресурс
        # -----------------------------------------------------

        return {
            "city": "Kyiv",
            "temperature": 24,
            "condition": "partly cloudy",
            "paid": True,
            "txHash": result["txHash"],
            "explorer": result["explorer"],
        }

    except Exception as e:

        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid payment",
                "detail": str(e),
            },
        )