"""Централізовані налаштування проєкту: RPC Whitechain, ціни, ліміти, гаманці.

Усі значення читаються з .env (див. .env.example). Нічого тут не хардкодиться.
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


# --- Claude API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Whitechain ---
WHITECHAIN_RPC_URL = os.getenv("WHITECHAIN_RPC_URL", "")
WHITECHAIN_CHAIN_ID = _int_env("WHITECHAIN_CHAIN_ID", 2625)
WHITECHAIN_EXPLORER_URL = os.getenv(
    "WHITECHAIN_EXPLORER_URL", "https://testnet.whitechain.io"
)

# --- Гаманці ---
AUTHOR_WALLET_ADDRESS = os.getenv("AUTHOR_WALLET_ADDRESS", "")
AUTHOR_WALLET_PRIVATE_KEY = os.getenv("AUTHOR_WALLET_PRIVATE_KEY", "")
PHOTOBANK_WALLET_ADDRESS = os.getenv("PHOTOBANK_WALLET_ADDRESS", "")
PHOTOBANK_WALLET_PRIVATE_KEY = os.getenv("PHOTOBANK_WALLET_PRIVATE_KEY", "")

# --- Сервіс Фотобанку ---
PHOTOBANK_HOST = os.getenv("PHOTOBANK_HOST", "127.0.0.1")
PHOTOBANK_PORT = _int_env("PHOTOBANK_PORT", 8000)
PHOTOBANK_BASE_URL = f"http://{PHOTOBANK_HOST}:{PHOTOBANK_PORT}"
PHOTO_PRICE_WBT = _float_env("PHOTO_PRICE_WBT", 0.02)

# --- Ліміти агента-автора ---
AUTHOR_MAX_SPEND_WBT = _float_env("AUTHOR_MAX_SPEND_WBT", 1.0)
SPEND_LEDGER_PATH = os.getenv("SPEND_LEDGER_PATH", ".spend_ledger.json")

# --- x402-схема, адаптована для Whitechain ---
# Ми платимо напряму в testnet WBT (нативна монета), а не в ERC-20-стейблкоїні,
# тому scheme називається "exact-native", а не стандартний x402 "exact" (EIP-3009).
X402_SCHEME = "exact-native"
X402_NETWORK = f"whitechain-testnet-{WHITECHAIN_CHAIN_ID}"
