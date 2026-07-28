"""Capability Registry — мінімальний service discovery (Фаза 2, Компонент 2).

НЕ маркетплейс і НЕ ончейн: проста таблиця store.capabilities. Агент питає
реєстр за capability_type ("image-generation") і отримує provider_url — без
хардкоду конкретного сервісу в клієнті. Ончейн-реєстр можливостей — Roadmap
(Phase 3).

Модель запису: {id, capability_type, provider_url, price_wei, min_reputation_tier, active}.
Ціна — int wei (метадані показу; сама оплата йде за 402, не за це поле).
"""

from __future__ import annotations

from web3 import Web3

import registry_auth


class CapabilityNotFound(Exception):
    """Немає активної можливості такого типу в реєстрі."""


class CapabilityRegistry:
    def __init__(self, store):
        self.store = store

    def register(self, record: dict, signature: str) -> dict:
        """Реєструє можливість ЛИШЕ з валідним підписом провайдера (F4 #A).

        Підпис доводить, що запис справді належить власнику: `id` примусово
        дорівнює адресі підписанта, а `pay_to` входить у підписане повідомлення,
        тож його не можна підмінити без ключа власника. Відкрита (без підпису)
        реєстрація більше не приймається."""
        required = {"id", "capability_type", "provider_url", "pay_to"}
        missing = required - set(record)
        if missing:
            raise ValueError(f"capability record бракує полів: {sorted(missing)}")
        if not signature:
            raise ValueError("Реєстрація має бути підписана ключем провайдера (bracує signature).")

        owner = registry_auth.verify_registration(record, signature)  # кидає ValueError при невалідності

        normalized = {
            "id": owner,  # канонічний checksummed = підписант (id прив'язаний до owner)
            "owner_address": owner,
            "capability_type": record["capability_type"],
            "provider_url": record["provider_url"],
            "pay_to": Web3.to_checksum_address(record["pay_to"]),
            "price_wei": int(record.get("price_wei", 0) or 0),
            "min_reputation_tier": int(record.get("min_reputation_tier", 0) or 0),
            "active": bool(record.get("active", True)),
            "signature": signature,
        }
        self.store.upsert_capability(normalized)
        return normalized

    def list(self, capability_type: str | None = None) -> list[dict]:
        return self.store.list_capabilities(capability_type=capability_type, active_only=True)

    def resolve(self, capability_type: str) -> list[dict]:
        """Повертає ВСІ активні провайдери типу (порядок — серверний created_at,
        не контрольований реєстрантом). Вибір конкретного — явне рішення
        викликача за його політикою, а не побічний ефект `matches[0]`."""
        matches = self.store.list_capabilities(capability_type=capability_type, active_only=True)
        if not matches:
            raise CapabilityNotFound(f"Немає активної можливості типу '{capability_type}'.")
        return matches
