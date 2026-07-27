"""Event log — структурований журнал платіжного потоку (Фаза 2, Компонент 4).

НЕ шина повідомлень (ані Kafka/Rabbit/черги — це поза межами Фази 2).
Просто: структурований лог + запис у таблицю store.events. Кожна подія
має ts, тож різниця між подіями = latency-метрики для README/пітчу.

Канонічні типи подій платіжного циклу:
  PaymentRequested -> AuthorizationValidated -> SettlementSubmitted
  -> SettlementConfirmed -> AccessGranted
"""

from __future__ import annotations

import logging

logger = logging.getLogger("agentpay.events")

PAYMENT_REQUESTED = "PaymentRequested"
AUTHORIZATION_VALIDATED = "AuthorizationValidated"
SETTLEMENT_SUBMITTED = "SettlementSubmitted"
SETTLEMENT_CONFIRMED = "SettlementConfirmed"
ACCESS_GRANTED = "AccessGranted"


class EventLog:
    def __init__(self, store):
        self.store = store

    def emit(self, event_type: str, *, agent: str | None = None, resource: str | None = None, tx_hash: str | None = None) -> dict:
        event = self.store.add_event(event_type, agent, resource, tx_hash)
        logger.info(
            "event=%s agent=%s resource=%s tx=%s ts=%.3f",
            event_type,
            agent,
            resource,
            tx_hash,
            event["ts"],
        )
        return event
