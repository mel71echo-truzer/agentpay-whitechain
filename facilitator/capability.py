"""Capability Registry — мінімальний service discovery (Фаза 2, Компонент 2).

НЕ маркетплейс і НЕ ончейн: проста таблиця store.capabilities. Агент питає
реєстр за capability_type ("image-generation") і отримує provider_url — без
хардкоду конкретного сервісу в клієнті. Ончейн-реєстр можливостей — Roadmap
(Phase 3).

Модель запису: {id, capability_type, provider_url, price, min_reputation_tier, active}.
"""

from __future__ import annotations


class CapabilityNotFound(Exception):
    """Немає активної можливості такого типу в реєстрі."""


class CapabilityRegistry:
    def __init__(self, store):
        self.store = store

    def register(self, record: dict) -> dict:
        required = {"id", "capability_type", "provider_url"}
        missing = required - set(record)
        if missing:
            raise ValueError(f"capability record бракує полів: {sorted(missing)}")
        normalized = {
            "id": record["id"],
            "capability_type": record["capability_type"],
            "provider_url": record["provider_url"],
            "price": float(record.get("price", 0) or 0),
            "min_reputation_tier": int(record.get("min_reputation_tier", 0) or 0),
            "active": bool(record.get("active", True)),
        }
        self.store.upsert_capability(normalized)
        return normalized

    def list(self, capability_type: str | None = None) -> list[dict]:
        return self.store.list_capabilities(capability_type=capability_type, active_only=True)

    def resolve(self, capability_type: str) -> dict:
        """Повертає перший активний provider для типу; інакше CapabilityNotFound."""
        matches = self.store.list_capabilities(capability_type=capability_type, active_only=True)
        if not matches:
            raise CapabilityNotFound(f"Немає активної можливості типу '{capability_type}'.")
        return matches[0]
