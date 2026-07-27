"""Settlement — релей оплати on-chain + форвард комісії (Фаза 2, Компонент 1).

Єдина відповідальність: узяти вже провалідовану authorization і провести
розрахунок у мережі. Тримає ЧІТКИЙ seam: у Фазі 2.5 весь цей клас
замінюється на виклик атомарного router/escrow-контракту БЕЗ зміни
сигнатури settle() чи місць виклику.

Поточна (Фаза 2) реалізація — той самий "receive-full-then-forward"
підхід, що й Фаза 1 (facilitator отримує повну суму, лишає комісію,
форвардить решту сервісу двома послідовними транзакціями). Router з
атомарним split — Roadmap (Phase 2.5).

Race-fix (Компонент 4): якщо wait_for_confirmation=True (за замовчуванням),
settle() ДОЧІКУЄТЬСЯ receipt релею (і форварду) і повертає status="confirmed"
лише після підтвердження блоку. Оркестратор видає ресурс саме на цьому
підтвердженні, а не на broadcast — це закриває off-chain race (контент
роздано, а платіж потім відкотився). wait_for_confirmation=False лишає
старий швидкий шлях (broadcast, status="submitted") — тільки для швидкого
локального demo; trade-off задокументований у README.
"""

from __future__ import annotations

import logging

from web3 import Web3

import chain

logger = logging.getLogger(__name__)


class SettlementError(Exception):
    """Релей провалився on-chain (revert / RPC-помилка)."""


class SettlementEngine:
    def __init__(
        self,
        w3,
        teurc,
        *,
        facilitator_private_key: str,
        service_provider_address: str,
        fee_bps: int,
        teurc_decimals: int,
        wait_for_confirmation: bool = True,
        confirmation_timeout: int = 120,
    ):
        self.w3 = w3
        self.teurc = teurc
        self.facilitator_private_key = facilitator_private_key
        self.service_provider_address = service_provider_address
        self.fee_bps = fee_bps
        self.teurc_decimals = teurc_decimals
        self.wait_for_confirmation = wait_for_confirmation
        self.confirmation_timeout = confirmation_timeout

    def settle(self, message: dict, authorization: dict) -> dict:
        """Релеїть transferWithAuthorization і форвардить комісію.

        Повертає {
          "relay_tx_hash", "forward_tx_hash",
          "status": "confirmed"|"submitted",
          "confirmed": bool,
          "fee_wei", "net_wei",
        }.
        Кидає SettlementError, якщо (у режимі confirmation) релей відкотився.
        """
        value = message["value"]

        relay_tx_hash = chain.send_contract_tx(
            self.w3,
            self.facilitator_private_key,
            self.teurc.functions.transferWithAuthorization(
                message["from"],
                message["to"],
                value,
                message["validAfter"],
                message["validBefore"],
                message["nonce"],
                authorization["v"],
                authorization["r"],
                authorization["s"],
            ),
        )

        confirmed = False
        if self.wait_for_confirmation:
            receipt = self.w3.eth.wait_for_transaction_receipt(relay_tx_hash, timeout=self.confirmation_timeout)
            if receipt.status != 1:
                raise SettlementError(f"Релей транзакції {relay_tx_hash} відкотився (status != 1).")
            confirmed = True

        fee_wei = (value * self.fee_bps) // 10_000
        net_wei = value - fee_wei

        forward_tx_hash = None
        if net_wei > 0 and self.service_provider_address:
            forward_tx_hash = chain.send_contract_tx(
                self.w3,
                self.facilitator_private_key,
                self.teurc.functions.transfer(
                    Web3.to_checksum_address(self.service_provider_address), net_wei
                ),
            )
            if self.wait_for_confirmation:
                fwd_receipt = self.w3.eth.wait_for_transaction_receipt(
                    forward_tx_hash, timeout=self.confirmation_timeout
                )
                if fwd_receipt.status != 1:
                    raise SettlementError(f"Форвард комісії {forward_tx_hash} відкотився (status != 1).")

        return {
            "relay_tx_hash": relay_tx_hash,
            "forward_tx_hash": forward_tx_hash,
            "status": "confirmed" if confirmed else "submitted",
            "confirmed": confirmed,
            "fee_wei": fee_wei,
            "net_wei": net_wei,
        }
