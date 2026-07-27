"""Юніт-тести Capability Registry (Фаза 2, Компонент 2). In-memory store."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facilitator.capability import CapabilityNotFound, CapabilityRegistry  # noqa: E402
from facilitator.store import Store  # noqa: E402


def _registry():
    return CapabilityRegistry(Store(":memory:"))


def test_resolve_returns_registered_provider():
    reg = _registry()
    reg.register(
        {"id": "c1", "capability_type": "image-generation", "provider_url": "http://sp:8000", "price": 0.02}
    )
    resolved = reg.resolve("image-generation")
    assert resolved["provider_url"] == "http://sp:8000"
    assert resolved["capability_type"] == "image-generation"


def test_resolve_unknown_type_raises():
    reg = _registry()
    reg.register({"id": "c1", "capability_type": "image-generation", "provider_url": "http://sp:8000"})
    with pytest.raises(CapabilityNotFound):
        reg.resolve("translation")


def test_list_filters_by_type():
    reg = _registry()
    reg.register({"id": "c1", "capability_type": "image-generation", "provider_url": "http://a"})
    reg.register({"id": "c2", "capability_type": "translation", "provider_url": "http://b"})
    assert {c["id"] for c in reg.list("image-generation")} == {"c1"}
    assert {c["id"] for c in reg.list()} == {"c1", "c2"}


def test_inactive_capability_not_resolved():
    reg = _registry()
    reg.register(
        {"id": "c1", "capability_type": "image-generation", "provider_url": "http://a", "active": False}
    )
    with pytest.raises(CapabilityNotFound):
        reg.resolve("image-generation")
    assert reg.list("image-generation") == []


def test_register_missing_fields_raises():
    reg = _registry()
    with pytest.raises(ValueError):
        reg.register({"id": "c1", "capability_type": "image-generation"})  # no provider_url
