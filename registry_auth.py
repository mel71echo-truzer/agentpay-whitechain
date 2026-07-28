"""Підписана реєстрація можливостей + перевірка (F4 #A).

Провайдер підписує свій запис реєстру власним ключем (EIP-191 personal_sign).
І сервер (на реєстрації), і агент (на discovery) перевіряють підпис. Що це
доводить:
  - запис СПРАВДІ належить власнику (owner) — `id` прив'язаний до підписанта;
  - `pay_to` НЕ підмінений — він у підписаному повідомленні.
Чого це НЕ доводить: що «саме цьому провайдеру слід платити». Вибір провайдера
— явне рішення викликача за його політикою (репутація/allowlist); реєстр лише
гарантує, що обраний провайдер криптографічно закріпив свій `pay_to`.

Порядок вибору провайдера НЕ спирається на жодне поле, яке контролює реєстрант
(див. store.list_capabilities: ORDER BY created_at, rowid — серверні значення).
"""

from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

# Поля, які провайдер підписує. Порядок фіксований — частина канонічного формату.
SIGNED_FIELDS = ("id", "capability_type", "provider_url", "pay_to", "price_wei", "min_reputation_tier")


def canonical_message(record: dict) -> str:
    """Детермінований рядок для підпису/перевірки. Адреси — checksum, числа —
    int, тож JSON-round-trip чи різний регістр не ламають підпис."""
    return "\n".join(
        [
            "AgentPay-Capability-Registration-v1",
            f"id={Web3.to_checksum_address(record['id'])}",
            f"capability_type={record['capability_type']}",
            f"provider_url={record['provider_url']}",
            f"pay_to={Web3.to_checksum_address(record['pay_to'])}",
            f"price_wei={int(record['price_wei'])}",
            f"min_reputation_tier={int(record.get('min_reputation_tier', 0) or 0)}",
        ]
    )


def sign_registration(record: dict, private_key: str) -> str:
    """Провайдер підписує свій запис. Повертає hex-підпис."""
    signed = Account.sign_message(encode_defunct(text=canonical_message(record)), private_key)
    return Web3.to_hex(signed.signature)


def recover_signer(record: dict, signature: str) -> str:
    """Відновлює адресу підписанта з запису+підпису (checksummed). Кидає при
    некоректному підписі/структурі."""
    return Account.recover_message(encode_defunct(text=canonical_message(record)), signature=signature)


def verify_registration(record: dict, signature: str) -> str:
    """Перевіряє, що підпис валідний і `id` == адреса підписанта. Повертає owner
    (checksummed) або кидає ValueError."""
    try:
        owner = recover_signer(record, signature)
    except Exception as exc:  # noqa: BLE001 — недовірений вхід
        raise ValueError(f"Невалідний підпис реєстрації: {exc}") from exc
    if owner.lower() != str(record.get("id", "")).lower():
        raise ValueError("`id` має дорівнювати адресі підписанта реєстрації (owner).")
    return owner
