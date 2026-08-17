import os
import httpx

from eth_account import Account
from x402 import common


TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", common.TOKEN_ADDRESS)
CHAIN_ID = common.CHAIN_ID

BUYER_PK = os.getenv(
    "BUYER_PRIVATE_KEY",
    os.getenv("PRIVATE_KEY", "")
)

REGISTRY_URL = os.getenv(
    "REGISTRY_URL",
    "http://127.0.0.1:8004"
)

CATEGORY = os.getenv(
    "CATEGORY",
    "weather"
)

TRANSLATE_TEXT = os.getenv(
    "TRANSLATE_TEXT",
    "Hello world"
)

TARGET_LANGUAGE = os.getenv(
    "TARGET_LANGUAGE",
    "uk"
)

BUDGET_UNITS = int(
    os.getenv("BUDGET_UNITS", "1000000")
)


def decide_with_claude(price_units: int, description: str) -> bool:
    key = os.getenv("ANTHROPIC_API_KEY")

    if not key:
        return price_units <= BUDGET_UNITS

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)

        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": (
                    f"Resource: {description}. "
                    f"Price: {price_units} apUSD units. "
                    f"Budget: {BUDGET_UNITS}. "
                    "Buy? Answer only YES or NO."
                )
            }],
        )

        return "YES" in msg.content[0].text.upper()

    except Exception:
        return price_units <= BUDGET_UNITS


def calculate_quality_score(service):
    rating = float(service.get("rating", 0))
    success_rate = float(service.get("success_rate", 0))
    latency_ms = float(service.get("latency_ms", 500))

    rating_score = rating / 5.0
    success_score = success_rate

    latency_score = 1 / (
        1 + latency_ms / 500
    )

    quality_score = (
        0.50 * rating_score
        + 0.30 * success_score
        + 0.20 * latency_score
    )

    return quality_score


def calculate_task_fit(service):
    description = service.get(
        "description",
        ""
    ).lower()

    category = service.get(
        "category",
        ""
    ).lower()

    task = CATEGORY.lower()

    score = 0.5

    if task in category:
        score += 0.3

    if task in description:
        score += 0.2

    return min(score, 1.0)


def calculate_final_score(service, minimum_price):
    price = int(service["price_units"])

    quality_score = calculate_quality_score(
        service
    )

    task_fit = calculate_task_fit(
        service
    )

    price_score = minimum_price / price

    final_score = (
        0.50 * quality_score
        + 0.30 * price_score
        + 0.20 * task_fit
    )

    return final_score


def buy():
    if not BUYER_PK:
        print("ERROR: BUYER_PRIVATE_KEY is not set.")
        return

    buyer = Account.from_key(BUYER_PK)

    print("Buyer agent:", buyer.address)
    print("Registry:", REGISTRY_URL)
    print("Category:", CATEGORY)
    print("Budget:", BUDGET_UNITS)
    print("Text:", TRANSLATE_TEXT)
    print()

    with httpx.Client(timeout=140) as client:

        registry_response = client.get(
            f"{REGISTRY_URL}/services",
            params={
                "category": CATEGORY,
                "max_price": BUDGET_UNITS,
            },
        )

        if registry_response.status_code != 200:
            print(
                "Service discovery failed:",
                registry_response.status_code,
                registry_response.text,
            )
            return

        services = registry_response.json()["services"]

        if not services:
            print("No suitable services found.")
            return

        print(
            f"Services found: {len(services)}"
        )
        print()

        minimum_price = min(
            int(service["price_units"])
            for service in services
        )

        scored_services = []

        for service in services:

            quality_score = calculate_quality_score(
                service
            )

            task_fit = calculate_task_fit(
                service
            )

            final_score = calculate_final_score(
                service,
                minimum_price
            )

            scored_services.append({
                "service": service,
                "quality_score": quality_score,
                "task_fit": task_fit,
                "final_score": final_score,
            })

        scored_services.sort(
            key=lambda x: x["final_score"],
            reverse=True
        )

        print("Candidates:")

        for item in scored_services:

            service = item["service"]

            price = int(
                service["price_units"]
            )

            price_score = (
                minimum_price / price
            )

            print(
                f"- {service['name']}: "
                f"{price} apUSD | "
                f"rating={service.get('rating', 0)} | "
                f"success={service.get('success_rate', 0) * 100:.1f}% | "
                f"latency={service.get('latency_ms', 0)}ms | "
                f"quality={item['quality_score']:.3f} | "
                f"price_score={price_score:.3f} | "
                f"task_fit={item['task_fit']:.3f} | "
                f"final={item['final_score']:.3f}"
            )

        print()

        selected = scored_services[0]["service"]

        selected_quality = scored_services[0][
            "quality_score"
        ]

        selected_task_fit = scored_services[0][
            "task_fit"
        ]

        selected_final = scored_services[0][
            "final_score"
        ]

        resource_url = selected["url"]
        endpoint = selected["endpoint"]

        print(
            "Selected service:",
            selected["name"]
        )

        print(
            "Quality score:",
            f"{selected_quality:.3f}"
        )

        print(
            "Task fit:",
            f"{selected_task_fit:.3f}"
        )

        print(
            "Final score:",
            f"{selected_final:.3f}"
        )

        print(
            "Price:",
            selected["price_units"],
            selected["currency"]
        )

        print(
            "Endpoint:",
            f"{resource_url}{endpoint}"
        )

        print()

        method = selected.get(
            "method",
            "POST"
        ).upper()

        if method == "GET":

            r = client.get(
                f"{resource_url}{endpoint}"
            )

        else:

            r = client.post(
                f"{resource_url}{endpoint}",
                json={
                    "text": TRANSLATE_TEXT,
                    "target_language": TARGET_LANGUAGE,
                },
            )

        if r.status_code != 402:
            print(
                "Expected HTTP 402, got:",
                r.status_code,
                r.text,
            )
            return

        req = r.json()["accepts"][0]

        price = int(
            req["maxAmountRequired"]
        )

        pay_to = req["payTo"]
        asset = req["asset"]
        network = req["network"]

        print(
            f"Payment required: "
            f"{price} units -> {pay_to}"
        )

        print("Token:", asset)
        print("Network:", network)

        if not decide_with_claude(
            price,
            req.get(
                "description",
                selected["description"]
            ),
        ):
            print(
                "Agent rejected payment: "
                "outside budget."
            )
            return

        auth = common.new_authorization(
            buyer.address,
            pay_to,
            price,
        )

        sig = common.sign_authorization(
            BUYER_PK,
            asset,
            CHAIN_ID,
            auth,
        )

        header = common.encode_payment_header(
            "exact",
            network,
            auth,
            sig,
        )

        print(
            "Payment authorization signed."
        )

        if method == "GET":

            r2 = client.get(
                f"{resource_url}{endpoint}",
                headers={
                    "X-PAYMENT": header,
                },
            )

        else:

            r2 = client.post(
                f"{resource_url}{endpoint}",
                json={
                    "text": TRANSLATE_TEXT,
                    "target_language": TARGET_LANGUAGE,
                },
                headers={
                    "X-PAYMENT": header,
                },
            )

        if r2.status_code == 200:

            data = r2.json()

            print()
            print("✅ PAYMENT SUCCESS")
            print("Response:", data)

        else:

            print()
            print(
                "❌ PAYMENT FAILED:",
                r2.status_code,
                r2.text,
            )


if __name__ == "__main__":
    buy()