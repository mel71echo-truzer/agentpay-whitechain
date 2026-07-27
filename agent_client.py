"""agent_client.py — агентський x402-клієнт для Фази 1 (EIP-3009, офчейн).

На відміну від Фази 0 (клієнт сам відправляв on-chain переказ і чекав
receipt), тут агент лише ПІДПИСУЄ EIP-712 authorization приватним ключем —
жодної власної транзакції, жодного очікування майнінгу. Підпис + докази
надсилаються сервісу; facilitator сам релеїть оплату в мережу (див.
facilitator/whitechain_facilitator.py).

Потік:
  1. GET ресурс -> 402 Payment Required {payTo, price_teurc, resource, ...}
  2. Клієнт генерує випадковий salt, рахує nonce = keccak256(resource || salt)
     (прив'язка авторизації до конкретного ресурсу — без цього замінити
     resource назад, отримавши чужий контент за той самий підпис,
     формально можливо: сервер саме тому й звіряє nonce з (resource, salt)).
  3. Підписує EIP-3009 TransferWithAuthorization офчейн (eth_account).
  4. Повторює запит з authorization+resource+salt у тілі — отримує контент
     миттєво (сервер не чекає підтвердження блоку).
"""

import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

TRANSFER_AUTH_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}

AUTH_VALID_SECONDS = 300


class PaymentFailed(Exception):
    """Сервіс відхилив авторизацію (KYA, reputation, підпис, тощо)."""


class CapabilityNotFound(Exception):
    """Реєстр не має активного провайдера потрібного типу."""


class SpendLimitExceeded(Exception):
    """Ліміт витрат на завдання вичерпано — агент відмовляється платити далі."""


def discover_capabilities(registry_url: str, capability_type: str | None = None) -> list[dict]:
    """Питає Capability Registry (service discovery) за списком можливостей.

    registry_url — базовий URL реєстру (у PoC той самий процес, що й сервіс).
    """
    params = {"type": capability_type} if capability_type else {}
    resp = requests.get(f"{registry_url}/registry/capabilities", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("capabilities", [])


def resolve_provider_url(registry_url: str, capability_type: str) -> str:
    """Знаходить provider_url для типу можливості через реєстр (не хардкод).

    Це і є суть Компонента 2: агент НЕ знає адресу сервісу заздалегідь —
    він її резолвить за capability_type.
    """
    caps = discover_capabilities(registry_url, capability_type)
    if not caps:
        raise CapabilityNotFound(f"Реєстр не має активного провайдера типу '{capability_type}'.")
    return caps[0]["provider_url"]


class SpendLedger:
    """Веде облік, скільки Автор уже витратив (у tEURC) у межах завдання.

    Зберігається в JSON-файлі (config.SPEND_LEDGER_PATH), щоб ліміт
    зберігався навіть якщо процес перезапустити всередині одного завдання.
    """

    def __init__(self, path: str | None = None, max_spend_teurc: float | None = None):
        self.path = Path(path or config.SPEND_LEDGER_PATH)
        self.max_spend_teurc = (
            max_spend_teurc if max_spend_teurc is not None else config.AUTHOR_MAX_SPEND_TEURC
        )
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"spent_teurc": 0.0, "payments": []}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    def reset(self) -> None:
        """Скидає лічильник — виклич на початку нового завдання."""
        self._state = {"spent_teurc": 0.0, "payments": []}
        self._save()

    @property
    def spent_teurc(self) -> float:
        return self._state["spent_teurc"]

    def ensure_can_spend(self, amount_teurc: float) -> None:
        if self.spent_teurc + amount_teurc > self.max_spend_teurc + 1e-12:
            raise SpendLimitExceeded(
                f"Ліміт вичерпано: вже витрачено {self.spent_teurc} tEURC, "
                f"ще {amount_teurc} tEURC перевищить максимум {self.max_spend_teurc} tEURC на завдання."
            )

    def record(self, amount_teurc: float, to: str, relay_tx_hash: str | None) -> None:
        self._state["spent_teurc"] += amount_teurc
        self._state["payments"].append({"amount_teurc": amount_teurc, "to": to, "relay_tx_hash": relay_tx_hash})
        self._save()


@dataclass
class PurchaseResult:
    content: bytes
    content_type: str
    already_had_it: bool = False
    reputation_tier: int | None = None
    fee_teurc: float | None = None
    relay_tx_hash: str | None = None
    forward_tx_hash: str | None = None
    log: list[str] | None = None


def build_and_sign_authorization(
    private_key: str,
    to_address: str,
    value_teurc: float,
    resource: str,
    teurc_address: str,
    chain_id: int,
    teurc_name: str = "Test EURC",
) -> dict:
    """Будує й підписує EIP-3009 TransferWithAuthorization для `resource`.

    Повертає dict, готовий для відправки сервісу:
    {"authorization": {...}, "resource": ..., "resource_salt": "0x..."}
    """
    account = Account.from_key(private_key)
    salt = os.urandom(32)
    nonce = Web3.keccak(resource.encode("utf-8") + salt)

    now = int(time.time())
    value_wei = round(value_teurc * (10**config.TEURC_DECIMALS))

    message = {
        "from": account.address,
        "to": Web3.to_checksum_address(to_address),
        "value": value_wei,
        "validAfter": 0,
        "validBefore": now + AUTH_VALID_SECONDS,
        "nonce": nonce,
    }
    domain = {
        "name": teurc_name,
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(teurc_address),
    }
    signable = encode_typed_data(domain, TRANSFER_AUTH_TYPES, message)
    signed = Account.sign_message(signable, private_key)

    return {
        "authorization": {
            "from": message["from"],
            "to": message["to"],
            "value": message["value"],
            "validAfter": message["validAfter"],
            "validBefore": message["validBefore"],
            "nonce": "0x" + nonce.hex(),
            "v": signed.v,
            "r": Web3.to_hex(signed.r.to_bytes(32, "big")),
            "s": Web3.to_hex(signed.s.to_bytes(32, "big")),
        },
        "resource": resource,
        "resource_salt": "0x" + salt.hex(),
    }


def pay_and_fetch(
    url: str,
    private_key: str | None = None,
    ledger=None,
    chain_id: int | None = None,
) -> PurchaseResult:
    """GET url; якщо 402 — підписує authorization офчейн і повторює запит.

    ledger — необов'язковий SpendLedger (див. author/agent.py) для
    перевірки ліміту витрат ПЕРЕД підписом (підпис ще не витрачає нічого
    сам собою, але немає сенсу підписувати те, що ліміт однаково відхилить).
    """
    private_key = private_key or config.AUTHOR_WALLET_PRIVATE_KEY
    log: list[str] = []

    response = requests.get(url, timeout=30)

    if response.status_code == 200:
        return PurchaseResult(
            content=response.content,
            content_type=response.headers.get("content-type", ""),
            already_had_it=True,
            log=["Ресурс уже доступний без оплати."],
        )

    if response.status_code != 402:
        raise PaymentFailed(f"Неочікувана відповідь сервера: {response.status_code} {response.text}")

    requirements = response.json()
    accept = requirements["accepts"][0]
    pay_to = accept["payTo"]
    price_teurc = accept["price_teurc"]
    resource = accept["resource"]
    teurc_address = accept["asset_address"]

    log.append(f"Сервер просить {price_teurc} tEURC на {pay_to} за {resource}")

    if ledger is not None:
        ledger.ensure_can_spend(price_teurc)

    payload = build_and_sign_authorization(
        private_key,
        pay_to,
        price_teurc,
        resource,
        teurc_address,
        chain_id if chain_id is not None else config.CHAIN_ID,
    )

    paid_response = requests.post(url, json=payload, timeout=30)

    if paid_response.status_code != 200:
        raise PaymentFailed(
            f"Facilitator відхилив авторизацію: {paid_response.status_code} {paid_response.text}"
        )

    result_headers = paid_response.headers
    settlement = {}
    encoded_settlement = result_headers.get("X-Settlement")
    if encoded_settlement:
        settlement = json.loads(base64.b64decode(encoded_settlement))

    if ledger is not None:
        ledger.record(price_teurc, pay_to, settlement.get("relay_tx_hash"))

    log.append("Ресурс отримано; авторизацію прийнято офчейн.")

    return PurchaseResult(
        content=paid_response.content,
        content_type=result_headers.get("content-type", ""),
        reputation_tier=settlement.get("reputation_tier"),
        fee_teurc=settlement.get("fee_teurc"),
        relay_tx_hash=settlement.get("relay_tx_hash"),
        forward_tx_hash=settlement.get("forward_tx_hash"),
        log=log,
    )
