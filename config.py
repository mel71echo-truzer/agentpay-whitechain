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

load_dotenv()


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
TEURC_ADDRESS = os.getenv("TEURC_ADDRESS", "")
TEURC_DECIMALS = 6

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

RESOURCE_PRICE_TEURC = _float_env("RESOURCE_PRICE_TEURC", 0.02)
PREMIUM_RESOURCE_PRICE_TEURC = _float_env("PREMIUM_RESOURCE_PRICE_TEURC", 0.10)
# Мінімальний reputation_tier (кількість SBT-бейджів), потрібний для преміум-ресурсу.
PREMIUM_MIN_REPUTATION_TIER = _int_env("PREMIUM_MIN_REPUTATION_TIER", 1)

# Комісія facilitator-а з кожного платежу, у базисних пунктах (50 = 0.5%).
FACILITATOR_FEE_BPS = _int_env("FACILITATOR_FEE_BPS", 50)

# --- Ліміт витрат агента-автора на завдання ---
AUTHOR_MAX_SPEND_TEURC = _float_env("AUTHOR_MAX_SPEND_TEURC", 1.0)
SPEND_LEDGER_PATH = os.getenv("SPEND_LEDGER_PATH", ".spend_ledger.json")
