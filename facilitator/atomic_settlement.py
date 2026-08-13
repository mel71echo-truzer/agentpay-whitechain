"""AtomicSettlement — розрахунок однією транзакцією через AgentPayRouter (Фаза 2.5).

Той самий seam, що й SettlementEngine: `settle(message, authorization) -> dict`
з тими самими 6 ключами. Різниця — БЕКЕНД:

  legacy (SettlementEngine): дві послідовні tx (relay transferWithAuthorization,
    потім transfer нетто сервісу). Між ними є вікно часткового збою «списано,
    але не переслано» — його ловить SettlementForwardError + журнал F3.

  atomic (цей клас): ОДНА tx — router.settlePaymentAtomic робить
    receiveWithAuthorization (pull коштів) + split (комісія owner-у, нетто
    продавцю) атомарно. Тож стану funds-held НЕ ІСНУЄ: або пройшло все, або
    revert і НІЧОГО не рухалося. SettlementForwardError звідси не кидається
    ніколи — тільки SettlementError (чистий збій, кошти на місці).

Прив'язку продавця/суми/комісії/ресурсу до підпису покупця закрито в контракті
(C-1): роутер перераховує nonce з (seller, feeBps, resourceHash) і tEURC
відновлює підпис саме над ним — релеєр не може перенаправити кошти.
"""

from __future__ import annotations

import logging

import chain
from facilitator.settlement import SettlementRelayError

logger = logging.getLogger(__name__)


class AtomicSettlementEngine:
    def __init__(
        self,
        w3,
        teurc,
        router,
        *,
        facilitator_private_key: str,
        treasury_address: str,
        fee_bps: int,
        teurc_decimals: int,
        wait_for_confirmation: bool = True,
        confirmation_timeout: int = 120,
    ):
        self.w3 = w3
        self.teurc = teurc
        self.router = router
        self.facilitator_private_key = facilitator_private_key
        self.treasury_address = treasury_address  # owner() роутера = отримувач комісії
        self.fee_bps = fee_bps
        self.teurc_decimals = teurc_decimals
        self.wait_for_confirmation = wait_for_confirmation
        self.confirmation_timeout = confirmation_timeout

    def settle(self, message: dict, authorization: dict) -> dict:
        """Викликає router.settlePaymentAtomic однією транзакцією.

        Повертає ту саму форму, що й SettlementEngine.settle:
        {relay_tx_hash, forward_tx_hash, status, confirmed, fee_wei, net_wei}.
        forward_tx_hash == relay_tx_hash — pull і split сталися в ОДНІЙ tx.

        Кидає SettlementError, якщо tx відкотилась (revert = кошти не рухалися).
        SettlementForwardError неможливий: немає окремого форварду.
        """
        value = message["value"]
        # Комісія і нетто — цілочислово, floor у комісію, залишок продавцю
        # (той самий інваріант, що й у legacy й у контракті _split):
        # fee_wei + net_wei == value точно.
        fee_wei = (value * self.fee_bps) // 10_000
        net_wei = value - fee_wei

        try:
            tx_hash = chain.send_contract_tx(
                self.w3,
                self.facilitator_private_key,
                self.router.functions.settlePaymentAtomic(
                    message["from"],
                    message["seller"],
                    value,
                    message["feeBps"],
                    message["validAfter"],
                    message["validBefore"],
                    message["resourceHash"],
                    authorization["v"],
                    authorization["r"],
                    authorization["s"],
                ),
            )
        except Exception as exc:  # noqa: BLE001 — revert/RPC на етапі estimate/send
            # Атомарно: якщо не змогли навіть відправити (revert на estimate_gas),
            # кошти НЕ рухалися — чистий збій, компенсація не потрібна.
            raise SettlementRelayError(f"Атомарний розрахунок не пройшов (кошти не рухалися): {exc}") from exc

        confirmed = False
        if self.wait_for_confirmation:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.confirmation_timeout)
            if receipt.status != 1:
                # Атомарна tx відкотилась цілком — кошти не рухалися.
                raise SettlementRelayError(f"Атомарна tx {tx_hash} відкотилась (status != 1); кошти не рухалися.")
            confirmed = True

        return {
            "relay_tx_hash": tx_hash,
            "forward_tx_hash": tx_hash,  # атомарно: pull+split в одній tx
            "status": "confirmed" if confirmed else "submitted",
            "confirmed": confirmed,
            "fee_wei": fee_wei,
            "net_wei": net_wei,
        }
