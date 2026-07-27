"""Гаманці на Whitechain testnet.

Генерує EVM-гаманці через eth-account/web3.py напряму, вміє перевіряти
баланс нативного WBT (gas-токен) і відправляти WBT з одного гаманця на
інший. У Фазі 1 фактичні платежі йдуть у tEURC через EIP-3009
(facilitator.py, agent_client.py) — цей файл лишається для того, що досі
номіновано в нативному WBT: баланс на газ, faucet-перевірки.

Запуск напряму (`python wallets/setup_wallets.py`) або без адрес у .env —
генерує нову пару гаманців (Автор, Facilitator) і виводить їх, з адресами
і приватними ключами у .env, показує баланс WBT.
"""

import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def get_web3() -> Web3:
    """Створює з'єднання з Whitechain testnet через RPC з .env."""
    if not config.WHITECHAIN_TESTNET_RPC:
        raise RuntimeError(
            "WHITECHAIN_TESTNET_RPC не заданий у .env. "
            "Візьми публічний RPC на docs.whitechain.io."
        )
    return Web3(Web3.HTTPProvider(config.WHITECHAIN_TESTNET_RPC))


def generate_wallet() -> tuple[str, str]:
    """Генерує новий EVM-гаманець. Повертає (адреса, приватний_ключ)."""
    account = Account.create()
    return account.address, account.key.hex()


def check_balance(address: str, w3: Web3 | None = None) -> float:
    """Показує баланс WBT (нативної монети) заданої адреси, у WBT (не wei)."""
    w3 = w3 or get_web3()
    address = Web3.to_checksum_address(address)
    balance_wei = w3.eth.get_balance(address)
    return float(Web3.from_wei(balance_wei, "ether"))


def send_wbt(
    from_key: str,
    to_address: str,
    amount_wbt: float,
    w3: Web3 | None = None,
    data: bytes | None = None,
) -> str:
    """Відправляє WBT з одного гаманця на інший. Повертає хеш транзакції.

    from_key    — приватний ключ гаманця-відправника (з .env, ніколи не хардкодь)
    to_address  — адреса гаманця-отримувача
    amount_wbt  — сума у WBT (наприклад 0.02)
    data        — необов'язковий calldata (наприклад назва ресурсу, який
                  оплачується — див. author/x402_client.py). Прив'язує цю
                  конкретну транзакцію до конкретного ресурсу назавжди
                  (calldata незмінний після підпису), тож звичайних
                  21000 газу для чистого переказу вже не вистачить —
                  у цьому випадку рахуємо потрібний газ через estimate_gas.
    """
    w3 = w3 or get_web3()
    account = Account.from_key(from_key)
    to_address = Web3.to_checksum_address(to_address)

    tx = {
        "from": account.address,
        "to": to_address,
        "value": Web3.to_wei(amount_wbt, "ether"),
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": config.CHAIN_ID,
        "gasPrice": w3.eth.gas_price,
    }
    if data:
        tx["data"] = data
        tx["gas"] = w3.eth.estimate_gas(tx) + 10_000  # запас про всяк випадок
    else:
        tx["gas"] = 21000  # стандартна вартість чистого переказу без calldata

    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    # web3.py v7 повертає hex без префіксу "0x" — без нього посилання в
    # explorer ламаються, тож додаємо його тут один раз (без подвоєння).
    h = tx_hash.hex()
    return h if h.startswith("0x") else "0x" + h


def explorer_link(tx_hash: str) -> str:
    """Формує посилання на транзакцію в Whitechain explorer."""
    tx_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    return f"{config.WHITECHAIN_EXPLORER_URL}/tx/{tx_hash}"


if __name__ == "__main__":
    print("=== Гаманці на Whitechain testnet ===\n")

    author_addr = config.AUTHOR_WALLET_ADDRESS
    facilitator_addr = config.FACILITATOR_WALLET_ADDRESS

    if not author_addr or not facilitator_addr:
        print("В .env ще немає адрес гаманців — генерую нові пари.\n")
        a_addr, a_key = generate_wallet()
        f_addr, f_key = generate_wallet()
        print("Гаманець Автора (агент-покупець, підписує EIP-3009):")
        print(f"  AUTHOR_WALLET_ADDRESS={a_addr}")
        print(f"  AUTHOR_WALLET_PRIVATE_KEY={a_key}")
        print("\nГаманець Facilitator-а (релеїть транзакції, платить gas у WBT):")
        print(f"  FACILITATOR_WALLET_ADDRESS={f_addr}")
        print(f"  FACILITATOR_WALLET_PRIVATE_KEY={f_key}")
        print(
            "\nСкопіюй ці 4 рядки у свій .env, отримай тестові WBT з faucet на "
            "docs.whitechain.io для FACILITATOR_WALLET_ADDRESS (це він платить gas), "
            "і запусти цей файл ще раз, щоб побачити баланси."
        )
    else:
        w3 = get_web3()
        print(f"Підключено до Whitechain RPC. Chain ID очікується: {config.CHAIN_ID}")
        print(f"Фактичний chain ID мережі: {w3.eth.chain_id}\n")

        author_balance = check_balance(author_addr, w3)
        facilitator_balance = check_balance(facilitator_addr, w3)

        print(f"Автор        ({author_addr}): {author_balance} WBT")
        print(f"Facilitator  ({facilitator_addr}): {facilitator_balance} WBT")

        if facilitator_balance == 0:
            print(
                "\nБаланс Facilitator-а нульовий. Саме він платить gas за релей "
                "транзакцій — отримай тестові WBT з faucet на docs.whitechain.io "
                "на адресу вище і запусти файл знову."
            )
