"""x402-клієнт для Агента-Автора — Частина 4 з ТЗ.

pay_and_fetch(url) робить запит; якщо у відповідь прилітає 402 Payment
Required, клієнт САМ (без людини) платить WBT на адресу з payment
requirements і повторює запит із доказом оплати. Перед оплатою перевіряє
ліміт витрат на завдання (AUTHOR_MAX_SPEND_WBT), який зберігається в JSON
(SpendLedger) — просто і без зовнішніх залежностей для MVP.
"""

import base64
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from wallets.setup_wallets import explorer_link as _explorer_link  # noqa: E402
from wallets.setup_wallets import send_wbt  # noqa: E402


class SpendLimitExceeded(Exception):
    """Ліміт витрат на завдання вичерпано — агент відмовляється платити далі."""


class PaymentFailed(Exception):
    """Оплата не пройшла (revert, RPC-помилка, тощо)."""


class SpendLedger:
    """Веде облік, скільки Автор уже витратив у межах поточного завдання.

    Зберігається в JSON-файлі (config.SPEND_LEDGER_PATH), щоб ліміт
    зберігався навіть якщо процес перезапустити всередині одного завдання.
    """

    def __init__(self, path: str | None = None, max_spend_wbt: float | None = None):
        self.path = Path(path or config.SPEND_LEDGER_PATH)
        self.max_spend_wbt = max_spend_wbt if max_spend_wbt is not None else config.AUTHOR_MAX_SPEND_WBT
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"spent_wbt": 0.0, "payments": []}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))

    def reset(self) -> None:
        """Скидає лічильник — виклич на початку нового завдання run_demo.py."""
        self._state = {"spent_wbt": 0.0, "payments": []}
        self._save()

    @property
    def spent_wbt(self) -> float:
        return self._state["spent_wbt"]

    def ensure_can_spend(self, amount_wbt: float) -> None:
        if self.spent_wbt + amount_wbt > self.max_spend_wbt + 1e-12:
            raise SpendLimitExceeded(
                f"Ліміт вичерпано: вже витрачено {self.spent_wbt} WBT, "
                f"ще {amount_wbt} WBT перевищить максимум {self.max_spend_wbt} WBT на завдання."
            )

    def record(self, amount_wbt: float, to: str, tx_hash: str) -> None:
        self._state["spent_wbt"] += amount_wbt
        self._state["payments"].append({"amount_wbt": amount_wbt, "to": to, "txHash": tx_hash})
        self._save()


@dataclass
class PurchaseResult:
    content: bytes
    content_type: str
    tx_hash: str | None
    amount_wbt: float
    explorer_link: str | None
    already_had_it: bool = False
    log: list[str] = field(default_factory=list)


def pay_and_fetch(
    url: str,
    private_key: str | None = None,
    ledger: SpendLedger | None = None,
    w3=None,
) -> PurchaseResult:
    """GET url; якщо 402 — платить автономно і повторює запит.

    url          — повний URL ресурсу (наприклад http://127.0.0.1:8000/photo/kyiv-lavra)
    private_key  — приватний ключ гаманця Автора (за замовчуванням з .env)
    ledger       — SpendLedger для перевірки ліміту (за замовчуванням новий/спільний)
    """
    private_key = private_key or config.AUTHOR_WALLET_PRIVATE_KEY
    ledger = ledger or SpendLedger()
    log: list[str] = []

    response = requests.get(url, timeout=30)

    if response.status_code == 200:
        return PurchaseResult(
            content=response.content,
            content_type=response.headers.get("content-type", ""),
            tx_hash=None,
            amount_wbt=0.0,
            explorer_link=None,
            already_had_it=True,
            log=["Ресурс уже доступний без оплати."],
        )

    if response.status_code != 402:
        raise PaymentFailed(f"Неочікувана відповідь сервера: {response.status_code} {response.text}")

    requirements = response.json()
    accept = requirements["accepts"][0]
    pay_to = accept["payTo"]
    amount_wbt = accept["amount_wbt"]

    log.append(f"Сервер просить оплату: {amount_wbt} WBT на {pay_to}")

    ledger.ensure_can_spend(amount_wbt)

    tx_hash = send_wbt(private_key, pay_to, amount_wbt, w3=w3)
    ledger.record(amount_wbt, pay_to, tx_hash)
    log.append(f"Оплачено. TX: {tx_hash}")

    payment_header = base64.b64encode(json.dumps({"txHash": tx_hash}).encode()).decode()
    paid_response = requests.get(url, headers={"X-PAYMENT": payment_header}, timeout=30)

    if paid_response.status_code != 200:
        raise PaymentFailed(
            f"Оплата пройшла (tx {tx_hash}), але сервер все одно відмовив: "
            f"{paid_response.status_code} {paid_response.text}"
        )

    log.append("Ресурс отримано після оплати.")

    return PurchaseResult(
        content=paid_response.content,
        content_type=paid_response.headers.get("content-type", ""),
        tx_hash=tx_hash,
        amount_wbt=amount_wbt,
        explorer_link=_explorer_link(tx_hash),
        log=log,
    )
