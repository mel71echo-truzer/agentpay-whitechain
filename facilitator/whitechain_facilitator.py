"""x402-facilitator для Whitechain — Частина 3 з ТЗ (серце проєкту).

Що таке facilitator: це "касир". Сервіс (Фотобанк) сам не хоче лізти в
блокчейн і перевіряти транзакції — натомість він питає facilitator:
"ось хеш транзакції, чи справді на неї заплатили $0.02 на мою адресу?".
Facilitator дивиться в мережу Whitechain і відповідає true/false.

Оригінальний x402-facilitator від Coinbase зроблений під Base і схему
"exact" (EIP-3009 transferWithAuthorization для USDC — платіж підписується
офчейн і сетлиться сервером). У Whitechain testnet немає готового USDC,
тому тут спрощена схема "exact-native" (див. config.X402_SCHEME): клієнт
просто відправляє нативний WBT-переказ сам (wallets/setup_wallets.send_wbt)
і як доказ платежу передає tx hash. Facilitator перевіряє цю транзакцію
напряму в мережі — той самий принцип (сервіс не довіряє клієнту на слово,
довіряє блокчейну), спрощений виклик до RPC.
"""

import json
import logging
import sys
import threading
from pathlib import Path

from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

logger = logging.getLogger(__name__)


class WhitechainFacilitator:
    """Перевіряє платежі в WBT на мережі Whitechain.

    used_tx_store — шлях до JSON-файлу, де facilitator запам'ятовує, які tx
    вже були зараховані як оплата. Це захист від повторного використання
    одного й того ж доказу платежу для отримання кількох товарів.
    """

    def __init__(self, w3: Web3 | None = None, used_tx_store: str = ".facilitator_used_tx.json"):
        self.w3 = w3 or Web3(Web3.HTTPProvider(config.WHITECHAIN_RPC_URL))
        self._store_path = Path(used_tx_store)
        self._lock = threading.Lock()
        self._used_tx = self._load_used_tx()

    def _load_used_tx(self) -> set[str]:
        if self._store_path.exists():
            return set(json.loads(self._store_path.read_text()))
        return set()

    def _save_used_tx(self) -> None:
        self._store_path.write_text(json.dumps(sorted(self._used_tx)))

    def verify_payment(
        self,
        tx_hash: str,
        expected_to: str,
        expected_amount_wbt: float,
        min_confirmations: int = 1,
    ) -> dict:
        """Перевіряє, що транзакція tx_hash — це справжня оплата.

        Повертає dict:
          {"valid": bool, "reason": str, "from": str | None, "amount_wbt": float | None}

        Умови, які мають виконатись, щоб valid=True:
        1. Транзакція існує і має receipt (уже потрапила в блок)
        2. Статус транзакції == 1 (успішна, не revert)
        3. Отримувач (to) == expected_to
        4. Сума (value) >= очікуваної суми (у wei)
        5. Транзакція має достатньо підтверджень (min_confirmations)
        6. Цей tx_hash ще не використовувався раніше (захист від повтору)
        """
        tx_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"

        with self._lock:
            if tx_hash in self._used_tx:
                return {
                    "valid": False,
                    "reason": "Ця транзакція вже була зарахована раніше (replay).",
                    "from": None,
                    "amount_wbt": None,
                }

            try:
                tx = self.w3.eth.get_transaction(tx_hash)
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            except Exception as exc:  # noqa: BLE001 — транзакція може ще не існувати
                # Деталі винятку (напр. ConnectionError) можуть містити повний
                # RPC URL — а в ньому часто зашитий секретний API-ключ провайдера
                # (типовий формат на кшталт /v2/<key>). Ніколи не віддаємо
                # сирий текст винятку клієнту — тільки в серверний лог.
                logger.warning("verify_payment: RPC error while looking up %s: %s", tx_hash, exc)
                return {
                    "valid": False,
                    "reason": "Транзакцію не знайдено в мережі (або мережа тимчасово недоступна).",
                    "from": None,
                    "amount_wbt": None,
                }

            if receipt.status != 1:
                return {
                    "valid": False,
                    "reason": "Транзакція є в мережі, але провалилась (status != 1).",
                    "from": tx["from"],
                    "amount_wbt": None,
                }

            actual_to = Web3.to_checksum_address(tx["to"]) if tx["to"] else None
            expected_to_checksum = Web3.to_checksum_address(expected_to)
            if actual_to != expected_to_checksum:
                return {
                    "valid": False,
                    "reason": f"Оплата пішла не на ту адресу: {actual_to} замість {expected_to_checksum}.",
                    "from": tx["from"],
                    "amount_wbt": float(Web3.from_wei(tx["value"], "ether")),
                }

            amount_wbt = float(Web3.from_wei(tx["value"], "ether"))
            if amount_wbt < expected_amount_wbt:
                return {
                    "valid": False,
                    "reason": f"Сума замала: {amount_wbt} WBT < {expected_amount_wbt} WBT.",
                    "from": tx["from"],
                    "amount_wbt": amount_wbt,
                }

            latest_block = self.w3.eth.block_number
            confirmations = latest_block - receipt.blockNumber + 1
            if confirmations < min_confirmations:
                return {
                    "valid": False,
                    "reason": f"Замало підтверджень: {confirmations} < {min_confirmations}.",
                    "from": tx["from"],
                    "amount_wbt": amount_wbt,
                }

            self._used_tx.add(tx_hash)
            self._save_used_tx()

            return {
                "valid": True,
                "reason": "Оплату підтверджено.",
                "from": tx["from"],
                "amount_wbt": amount_wbt,
            }


if __name__ == "__main__":
    # Тест: емулюємо реальну транзакцію з Тижня 1 (Частина 1) на локальному
    # тестовому ланцюжку, щоб перевірити логіку facilitator без реального RPC.
    from web3 import EthereumTesterProvider

    import wallets.setup_wallets as sw

    test_w3 = Web3(EthereumTesterProvider())
    faucet = test_w3.eth.accounts[0]

    author_addr, author_key = sw.generate_wallet()
    photobank_addr, _ = sw.generate_wallet()
    test_w3.eth.send_transaction(
        {"from": faucet, "to": author_addr, "value": Web3.to_wei(1, "ether")}
    )

    config.WHITECHAIN_CHAIN_ID = test_w3.eth.chain_id
    tx_hash = sw.send_wbt(author_key, photobank_addr, 0.02, test_w3)

    facilitator = WhitechainFacilitator(w3=test_w3, used_tx_store=".test_used_tx.json")

    print("=== Тест facilitator ===\n")

    result = facilitator.verify_payment(tx_hash, photobank_addr, 0.02)
    print(f"Перевірка справжньої оплати 0.02 WBT: {result}")
    assert result["valid"] is True

    replay_result = facilitator.verify_payment(tx_hash, photobank_addr, 0.02)
    print(f"\nПовторна перевірка того ж tx (replay-захист): {replay_result}")
    assert replay_result["valid"] is False

    wrong_amount = facilitator.verify_payment(
        sw.send_wbt(author_key, photobank_addr, 0.01, test_w3), photobank_addr, 0.02
    )
    print(f"\nПеревірка заниженої суми (0.01 замість 0.02): {wrong_amount}")
    assert wrong_amount["valid"] is False

    fake_tx = "0x" + "00" * 32
    fake_result = facilitator.verify_payment(fake_tx, photobank_addr, 0.02)
    print(f"\nПеревірка неіснуючої транзакції: {fake_result}")
    assert fake_result["valid"] is False

    Path(".test_used_tx.json").unlink(missing_ok=True)
    print("\nУсі перевірки пройшли ✅")
