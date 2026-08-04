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
import registry_auth  # noqa: E402

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


def verify_capability_record(record: dict) -> str:
    """Перевіряє підпис запису discovery (F4 #A): підпис валідний і owner==id.
    Повертає owner (checksummed) або кидає ValueError. Агент НЕ бере запис із
    discovery на віру — сам звіряє підпис, а не покладається лише на реєстр."""
    return registry_auth.verify_registration(record, record.get("signature", ""))


# НАЗВАНИЙ критерій вибору провайдера, коли їх кілька (F4). Прототипний дефолт —
# FIRST_REGISTERED: перший у СЕРВЕРНОМУ порядку реєстрації (created_at/rowid у
# store, не контрольований реєстрантом). Це свідомий вибір прототипу, НЕ
# ранжування за репутацією. Продакшн має передавати prefer_owner (allowlist
# довірених owner-адрес) АБО ранжувати за on-chain-репутацією власника — тоді
# критерій стане ALLOWLIST/REPUTATION відповідно. Головне: серед КІЛЬКОХ
# провайдерів вибір ніколи не має бути «сліпий [0]» без названого правила.
SELECTION_FIRST_REGISTERED = "first-registered"
SELECTION_ALLOWLIST = "allowlist-owner"


def select_provider(verified_candidates: list[dict], *, prefer_owner: str | None = None) -> tuple[dict, str]:
    """Обирає одного провайдера зі списку ПЕРЕВІРЕНИХ кандидатів за НАЗВАНИМ
    критерієм. Повертає (запис, назва_критерію) — критерій явний і придатний
    для логування/аудиту, а не побічний ефект індексації."""
    if prefer_owner is not None:
        for c in verified_candidates:
            if str(c.get("id", "")).lower() == prefer_owner.lower():
                return c, SELECTION_ALLOWLIST
        raise CapabilityNotFound(f"Немає перевіреного провайдера з owner={prefer_owner}.")
    # Прототипний дефолт: перший зареєстрований (серверний порядок).
    return verified_candidates[0], SELECTION_FIRST_REGISTERED


def resolve_capability(registry_url: str, capability_type: str, *, prefer_owner: str | None = None) -> dict:
    """Резолвить ПЕРЕВІРЕНИЙ запис можливості через реєстр.

    Реєстр повертає ВСІ збіги в серверному порядку. Агент (а) відкидає записи з
    невалідним підписом, (б) обирає одного за НАЗВАНИМ критерієм (`select_provider`),
    а не сліпим matches[0]. Обраний критерій кладемо у запис (`_selected_by`) для
    прозорості."""
    caps = discover_capabilities(registry_url, capability_type)
    verified = []
    for c in caps:
        try:
            verify_capability_record(c)
        except (ValueError, KeyError):
            continue  # непідписаний/підроблений запис ігноруємо
        verified.append(c)
    if not verified:
        raise CapabilityNotFound(f"Реєстр не має ПЕРЕВІРЕНОГО провайдера типу '{capability_type}'.")
    chosen, criterion = select_provider(verified, prefer_owner=prefer_owner)
    chosen = {**chosen, "_selected_by": criterion}
    return chosen


def resolve_provider_url(registry_url: str, capability_type: str, *, prefer_owner: str | None = None) -> str:
    """provider_url перевіреного провайдера (тонка обгортка resolve_capability)."""
    return resolve_capability(registry_url, capability_type, prefer_owner=prefer_owner)["provider_url"]


class SpendLedger:
    """Веде облік, скільки Автор уже витратив (у wei) у межах завдання.

    Гроші — int wei (рішення №4): жодного float у лічильнику, точний ліміт без
    накопичувального дрейфу. Зберігається в JSON-файлі (config.SPEND_LEDGER_PATH),
    щоб ліміт переживав перезапуск процесу всередині одного завдання.
    """

    def __init__(self, path: str | None = None, max_spend_wei: int | None = None):
        self.path = Path(path or config.SPEND_LEDGER_PATH)
        self.max_spend_wei = (
            max_spend_wei if max_spend_wei is not None else config.AUTHOR_MAX_SPEND_WEI
        )
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"spent_wei": 0, "payments": []}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    def reset(self) -> None:
        """Скидає лічильник — виклич на початку нового завдання."""
        self._state = {"spent_wei": 0, "payments": []}
        self._save()

    @property
    def spent_wei(self) -> int:
        return self._state["spent_wei"]

    def ensure_can_spend(self, amount_wei: int) -> None:
        # Ціла арифметика — точне порівняння, без float-епсилону.
        if self.spent_wei + amount_wei > self.max_spend_wei:
            raise SpendLimitExceeded(
                f"Ліміт вичерпано: вже витрачено {self.spent_wei} wei, "
                f"ще {amount_wei} wei перевищить максимум {self.max_spend_wei} wei на завдання."
            )

    def record(self, amount_wei: int, to: str, relay_tx_hash: str | None) -> None:
        self._state["spent_wei"] += amount_wei
        self._state["payments"].append({"amount_wei": amount_wei, "to": to, "relay_tx_hash": relay_tx_hash})
        self._save()


@dataclass
class PurchaseResult:
    content: bytes
    content_type: str
    already_had_it: bool = False
    reputation_tier: int | None = None
    fee_teurc: str | None = None  # людський рядок (B3), лише для показу
    relay_tx_hash: str | None = None
    forward_tx_hash: str | None = None
    log: list[str] | None = None


def build_and_sign_authorization(
    private_key: str,
    to_address: str,
    value_wei: int,
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
    # value_wei — уже int wei (конверсія на межі виклику). Валідуємо тип.
    value_wei = int(value_wei)

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
    expected_pay_to: str | None = None,
) -> PurchaseResult:
    """GET url; якщо 402 — підписує authorization офчейн і повторює запит.

    ledger — необов'язковий SpendLedger (див. author/agent.py) для
    перевірки ліміту витрат ПЕРЕД підписом (підпис ще не витрачає нічого
    сам собою, але немає сенсу підписувати те, що ліміт однаково відхилить).

    expected_pay_to — якщо передано (з ПІДПИСАНОГО запису реєстру), звіряємо
    його з `payTo` із 402: розбіжність = HARD-FAIL, відмова платити (F4 #A).
    Так агент не підписує авторизацію на адресу, якої немає в підписаному
    записі провайдера — навіть якщо provider_url скомпрометовано.
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
    # HARD-FAIL: payTo з 402 мусить збігтися з підписаним записом реєстру (F4 #A).
    if expected_pay_to is not None and str(pay_to).lower() != expected_pay_to.lower():
        raise PaymentFailed(
            f"payTo із 402 ({pay_to}) не збігається з підписаним записом реєстру "
            f"({expected_pay_to}) — відмова платити (можлива підміна provider_url)."
        )
    # Авторитетна сума з 402 — int wei (B3). price_teurc лишається для показу.
    price_wei = int(accept["price_wei"])
    resource = accept["resource"]
    teurc_address = accept["asset_address"]

    log.append(f"Сервер просить {accept.get('price_teurc')} tEURC ({price_wei} wei) на {pay_to} за {resource}")

    if ledger is not None:
        ledger.ensure_can_spend(price_wei)

    payload = build_and_sign_authorization(
        private_key,
        pay_to,
        price_wei,
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
        ledger.record(price_wei, pay_to, settlement.get("relay_tx_hash"))

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
