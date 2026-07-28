"""Централізовані налаштування Фази 1 PoC: мережа, контракти, KYA/reputation,
гаманці, ціни, ліміти. Усе з .env (див. .env.example) — нічого не хардкодиться.

NETWORK перемикає, з якою мережею працює весь код (facilitator.py,
agent_client.py, service_provider/server.py, scripts/demo.py):
  - "local"             (за замовчуванням) — локальний EthereumTesterProvider;
                          demo/тести самі розгортають tEURC (+ MockSoulRegistry,
                          якщо USE_MOCK_SOUL=true) щоразу заново, в пам'яті.
  - "whitechain_testnet" — реальний Whitechain testnet; RPC, ключі й адреси
                          контрактів беруться з .env (див. DEPLOY_WHITECHAIN.md).
Перехід з одного на інший — це редагування .env, а не коду.
"""

import os

from dotenv import load_dotenv

import money

load_dotenv()

TEURC_DECIMALS = 6


def _price_wei_env(name: str, default_teurc: str) -> int:
    """Ціна з .env (людський рядок tEURC) → int wei НА ЗАВАНТАЖЕННІ (рішення №4).

    Парсимо через Decimal і одразу конвертуємо в int wei; далі в грошових
    шляхах float не існує. Ціна з >TEURC_DECIMALS знаками падає тут, гучно,
    на старті — а не округлюється тихо."""
    value = os.getenv(name)
    return money.teurc_to_wei(value if value else default_teurc, TEURC_DECIMALS)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("false", "0", "no")


# --- Мережа ---
NETWORK = os.getenv("NETWORK", "local")  # "local" | "whitechain_testnet"
WHITECHAIN_TESTNET_RPC = os.getenv("WHITECHAIN_TESTNET_RPC", "")
CHAIN_ID = _int_env("CHAIN_ID", 2625)
DEPLOYER_PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY", "")
WHITECHAIN_EXPLORER_URL = os.getenv("WHITECHAIN_EXPLORER_URL", "https://testnet.whitechain.io")

# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- WB Soul (KYA-гейт + reputation) ---
# true  -> деплоїмо і використовуємо MockSoulRegistry (локально, зараз)
# false -> читаємо адреси РЕАЛЬНИХ WB Soul контрактів з .env (справжня мережа)
# facilitator.py читає Soul через ОДИН і той самий інтерфейс незалежно від прапорця.
USE_MOCK_SOUL = _bool_env("USE_MOCK_SOUL", True)
SOUL_REGISTRY_ADDRESS = os.getenv("SOUL_REGISTRY_ADDRESS", "")
SOUL_ATTRIBUTE_REGISTRY_ADDRESS = os.getenv("SOUL_ATTRIBUTE_REGISTRY_ADDRESS", "")
SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = os.getenv("SOUL_BOUND_TOKEN_REGISTRY_ADDRESS", "")
IS_VERIFIED_ATTRIBUTE_ADDRESS = os.getenv("IS_VERIFIED_ATTRIBUTE_ADDRESS", "")
SBT_COLLECTION_ADDRESS = os.getenv("SBT_COLLECTION_ADDRESS", "")

# --- tEURC (тестовий євро-стейблкоїн, EIP-3009) ---
# TEURC_DECIMALS визначено вгорі (потрібне для _price_wei_env).
TEURC_ADDRESS = os.getenv("TEURC_ADDRESS", "")

# --- Гаманці ---
# Автор (агент-покупець) — підписує EIP-3009 authorization, ніколи не платить gas сам.
AUTHOR_WALLET_ADDRESS = os.getenv("AUTHOR_WALLET_ADDRESS", "")
AUTHOR_WALLET_PRIVATE_KEY = os.getenv("AUTHOR_WALLET_PRIVATE_KEY", "")
# Facilitator — релеїть transferWithAuthorization у мережу (платить gas у WBT),
# отримує повну суму, лишає собі комісію, форвардить решту сервісу.
FACILITATOR_WALLET_ADDRESS = os.getenv("FACILITATOR_WALLET_ADDRESS", "")
FACILITATOR_WALLET_PRIVATE_KEY = os.getenv("FACILITATOR_WALLET_PRIVATE_KEY", "")
# AI Service Provider (продавець) — отримує ціну мінус комісія facilitator-а.
SERVICE_PROVIDER_WALLET_ADDRESS = os.getenv("SERVICE_PROVIDER_WALLET_ADDRESS", "")
SERVICE_PROVIDER_WALLET_PRIVATE_KEY = os.getenv("SERVICE_PROVIDER_WALLET_PRIVATE_KEY", "")

# --- AI Service Provider (сервіс, що продає ресурси) ---
SERVICE_PROVIDER_HOST = os.getenv("SERVICE_PROVIDER_HOST", "127.0.0.1")
SERVICE_PROVIDER_PORT = _int_env("SERVICE_PROVIDER_PORT", 8000)
SERVICE_PROVIDER_BASE_URL = f"http://{SERVICE_PROVIDER_HOST}:{SERVICE_PROVIDER_PORT}"

# Операторський токен для /admin/* (звірка утриманих коштів тощо). ПОРОЖНІЙ за
# замовчуванням = /admin вимкнено (403), НЕ відкрито. Заданий => потрібен
# заголовок `Authorization: Bearer <token>`. Ніколи не логуємо саме значення.
ADMIN_API_TOKEN = os.getenv("ADMIN_API_TOKEN", "")

# Ціни зберігаємо у int wei (мінімальні одиниці tEURC). Env-значення лишаються
# людськими (0.02), але конвертуються в wei на завантаженні (рішення №4).
RESOURCE_PRICE_WEI = _price_wei_env("RESOURCE_PRICE_TEURC", "0.02")
PREMIUM_RESOURCE_PRICE_WEI = _price_wei_env("PREMIUM_RESOURCE_PRICE_TEURC", "0.10")
# Мінімальний reputation_tier (кількість SBT-бейджів), потрібний для преміум-ресурсу.
PREMIUM_MIN_REPUTATION_TIER = _int_env("PREMIUM_MIN_REPUTATION_TIER", 1)

# Комісія facilitator-а з кожного платежу, у базисних пунктах (50 = 0.5%).
FACILITATOR_FEE_BPS = _int_env("FACILITATOR_FEE_BPS", 50)

# --- Ліміт витрат агента-автора на завдання ---
AUTHOR_MAX_SPEND_WEI = _price_wei_env("AUTHOR_MAX_SPEND_TEURC", "1.0")
SPEND_LEDGER_PATH = os.getenv("SPEND_LEDGER_PATH", ".spend_ledger.json")

# --- Фаза 2: settlement, reputation, event store ---
# Видавати ресурс лише ПІСЛЯ підтвердження блоку (SettlementConfirmed), а не
# на broadcast — закриває off-chain race. true за замовчуванням; false — лише
# для швидкого локального demo (trade-off задокументований у README).
WAIT_FOR_CONFIRMATION = _bool_env("WAIT_FOR_CONFIRMATION", True)
SETTLEMENT_CONFIRMATION_TIMEOUT = _int_env("SETTLEMENT_CONFIRMATION_TIMEOUT", 120)
# N_target для нормалізації completed_payments у формулі репутації.
REPUTATION_N_TARGET = _int_env("REPUTATION_N_TARGET", 20)
# Локальне SQLite-сховище (agent_stats / events / capabilities).
STORE_DB_PATH = os.getenv("STORE_DB_PATH", ".agentpay.db")
# Тип можливості, який публікує наш AI Service Provider у Capability Registry.
CAPABILITY_TYPE = os.getenv("CAPABILITY_TYPE", "image-generation")
