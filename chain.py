"""Робота з контрактами через Hardhat-артефакти (ABI + bytecode) — без
залежності від запущеного Node.js/Hardhat процесу під час роботи Python.

Hardhat компілює контракти в `artifacts/contracts/**/*.json` (ABI +
bytecode). Цей модуль читає ці файли напряму і розгортає/підключається до
контрактів через web3.py — той самий код працює і проти локального
EthereumTesterProvider (тести, scripts/demo.py локально), і проти
справжнього Whitechain testnet (той самий w3, просто інший провайдер).

Запусти `npx hardhat compile` перед першим використанням (і після кожної
зміни .sol-файлів) — інакше `load_artifact` підкаже саме це.
"""

import json
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.contract.contract import Contract

import config

_local_w3: Web3 | None = None


def get_w3() -> Web3:
    """Повертає Web3-з'єднання відповідно до config.NETWORK.

    "whitechain_testnet" — реальний RPC з .env (WHITECHAIN_TESTNET_RPC).
    "local" (за замовчуванням) — один спільний EthereumTesterProvider на
    весь процес (не новий щоразу!), щоб facilitator, agent_client і
    scripts/demo.py в одному запуску бачили ті самі контракти/транзакції.
    """
    global _local_w3

    if config.NETWORK == "whitechain_testnet":
        if not config.WHITECHAIN_TESTNET_RPC:
            raise RuntimeError(
                "NETWORK=whitechain_testnet, але WHITECHAIN_TESTNET_RPC не заданий у .env."
            )
        return Web3(Web3.HTTPProvider(config.WHITECHAIN_TESTNET_RPC))

    if _local_w3 is None:
        from web3 import EthereumTesterProvider

        _local_w3 = Web3(EthereumTesterProvider())
    return _local_w3

_REPO_ROOT = Path(__file__).resolve().parent
_ARTIFACTS_DIR = _REPO_ROOT / "artifacts" / "contracts"

_ARTIFACT_PATHS = {
    "tEURC": _ARTIFACTS_DIR / "tEURC.sol" / "tEURC.json",
    "MockSoulAttribute": _ARTIFACTS_DIR / "mocks" / "MockSoulAttribute.sol" / "MockSoulAttribute.json",
    "MockSoulBoundTokenCollection": (
        _ARTIFACTS_DIR / "mocks" / "MockSoulBoundTokenCollection.sol" / "MockSoulBoundTokenCollection.json"
    ),
    "MockSoulRegistry": _ARTIFACTS_DIR / "mocks" / "MockSoulRegistry.sol" / "MockSoulRegistry.json",
    "ISoulRegistry": _ARTIFACTS_DIR / "interfaces" / "ISoulRegistry.sol" / "ISoulRegistry.json",
    "ISoulAttributeRegistry": (
        _ARTIFACTS_DIR / "interfaces" / "ISoulAttributeRegistry.sol" / "ISoulAttributeRegistry.json"
    ),
    "ISoulBoundTokenRegistry": (
        _ARTIFACTS_DIR / "interfaces" / "ISoulBoundTokenRegistry.sol" / "ISoulBoundTokenRegistry.json"
    ),
}


class ArtifactNotFound(Exception):
    """Hardhat-артефакт не знайдено — забув `npx hardhat compile`?"""


def load_artifact(name: str) -> dict:
    """Читає {"abi": [...], "bytecode": "0x...", ...} для контракту `name`."""
    path = _ARTIFACT_PATHS.get(name)
    if path is None:
        raise ArtifactNotFound(f"Невідомий артефакт '{name}'. Доступні: {sorted(_ARTIFACT_PATHS)}")
    if not path.exists():
        raise ArtifactNotFound(f"Артефакт '{name}' не знайдено за {path}. Запусти `npx hardhat compile`.")
    return json.loads(path.read_text())


def get_abi(name: str) -> list:
    return load_artifact(name)["abi"]


def get_contract(w3: Web3, name: str, address: str) -> Contract:
    """Підключається до вже розгорнутого контракту `name` за адресою."""
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=get_abi(name))


def deploy_contract(w3: Web3, deployer_key: str, name: str, *constructor_args) -> str:
    """Розгортає контракт з Hardhat-артефакту `name`. Повертає адресу."""
    artifact = load_artifact(name)
    account = Account.from_key(deployer_key)
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])

    tx = factory.constructor(*constructor_args).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": w3.eth.chain_id,
            "gasPrice": w3.eth.gas_price,
        }
    )
    tx["gas"] = w3.eth.estimate_gas(tx)

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        raise RuntimeError(f"Деплой '{name}' провалився (tx {tx_hash.hex()}).")
    return receipt.contractAddress


def send_contract_tx(w3: Web3, sender_key: str, function_call, *, value_wei: int = 0):
    """Підписує і надсилає виклик контрактної функції (не чекає майнінгу).

    function_call — результат `contract.functions.foo(args)`, ще не
    відправлений. Повертає (tx_hash_hex, розсилку без очікування receipt) —
    навмисно: частина Фази 1 — не блокувати видачу контенту очікуванням
    підтвердження блоку (див. README/facilitator).
    """
    account = Account.from_key(sender_key)
    tx = function_call.build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": w3.eth.chain_id,
            "gasPrice": w3.eth.gas_price,
            "value": value_wei,
        }
    )
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    h = tx_hash.hex()
    return h if h.startswith("0x") else "0x" + h
