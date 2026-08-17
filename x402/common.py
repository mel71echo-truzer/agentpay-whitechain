"""
Спільні помічники для x402-демо на Whitechain.
Тут — побудова та підпис EIP-3009 авторизації (TransferWithAuthorization),
щоб платник міг ПІДПИСАТИ переказ off-chain, а facilitator виконав його on-chain.
"""
import os
import json
import time
import base64
import secrets

from eth_account import Account
from eth_account.messages import encode_typed_data


def _load_dotenv():
    """Легкий парсер файлу .env (щоб не тягнути зайвих залежностей).
    Шукає .env у поточній теці і на рівень вище (де лежить проєкт)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (".env", os.path.join(here, "..", ".env"), os.path.join(here, ".env")):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except FileNotFoundError:
            continue


_load_dotenv()

# --- Налаштування мережі (читаються з .env проєкту або зі змінних оточення) ---
RPC_URL = os.getenv("RPC_URL", "https://rpc-testnet.whitechain.io")
CHAIN_ID = int(os.getenv("CHAIN_ID", "2625"))
TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", "")
TOKEN_NAME = os.getenv("TOKEN_NAME", "AgentPay USD")
TOKEN_VERSION = os.getenv("TOKEN_VERSION", "1")


def build_typed_data(token_address: str, chain_id: int, auth: dict) -> dict:
    """Формує EIP-712 структуру TransferWithAuthorization (точно як у контракті)."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "domain": {
            "name": TOKEN_NAME,
            "version": TOKEN_VERSION,
            "chainId": chain_id,
            "verifyingContract": token_address,
        },
        "primaryType": "TransferWithAuthorization",
        "message": auth,
    }


def new_authorization(sender: str, pay_to: str, value: int, valid_seconds: int = 600) -> dict:
    """Створює нову авторизацію переказу з випадковим nonce і терміном дії."""
    now = int(time.time())
    return {
        "from": sender,
        "to": pay_to,
        "value": int(value),
        "validAfter": 0,
        "validBefore": now + valid_seconds,
        "nonce": "0x" + secrets.token_hex(32),
    }


def sign_authorization(private_key: str, token_address: str, chain_id: int, auth: dict) -> dict:
    """Платник підписує авторизацію своїм приватним ключем (off-chain, без газу)."""
    typed = build_typed_data(token_address, chain_id, auth)
    signable = encode_typed_data(full_message=typed)
    signed = Account.sign_message(signable, private_key=private_key)
    return {
        "v": signed.v,
        "r": "0x" + signed.r.to_bytes(32, "big").hex(),
        "s": "0x" + signed.s.to_bytes(32, "big").hex(),
        "signature": signed.signature.hex(),
    }


def recover_signer(token_address: str, chain_id: int, auth: dict, signature_hex: str) -> str:
    """Відновлює адресу, яка підписала авторизацію (для off-chain перевірки у facilitator)."""
    typed = build_typed_data(token_address, chain_id, auth)
    signable = encode_typed_data(full_message=typed)
    return Account.recover_message(signable, signature=signature_hex)


def encode_payment_header(scheme: str, network: str, auth: dict, sig: dict) -> str:
    """Кодує оплату у base64 для HTTP-заголовка X-PAYMENT (стиль x402)."""
    payload = {
        "x402Version": 1,
        "scheme": scheme,
        "network": network,
        "payload": {"authorization": auth, "signature": sig["signature"]},
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def decode_payment_header(header: str) -> dict:
    return json.loads(base64.b64decode(header).decode())
