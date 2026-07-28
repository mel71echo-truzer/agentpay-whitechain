"""Юніт-тести Capability Registry (Фаза 2, Компонент 2; F4 #A — підписана
реєстрація). In-memory store."""

import sys
from pathlib import Path

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import registry_auth  # noqa: E402
from facilitator.capability import CapabilityNotFound, CapabilityRegistry  # noqa: E402
from facilitator.store import Store  # noqa: E402

PAY_TO = "0x000000000000000000000000000000000000dEaD"


def _registry():
    return CapabilityRegistry(Store(":memory:"))


def _signed(provider, capability_type="image-generation", provider_url="http://sp:8000", price_wei=20_000, active=True):
    """Будує запис від імені provider і підписує його ключем provider (id==owner)."""
    record = {
        "id": provider.address,
        "capability_type": capability_type,
        "provider_url": provider_url,
        "pay_to": PAY_TO,
        "price_wei": price_wei,
        "min_reputation_tier": 0,
        "active": active,
    }
    sig = registry_auth.sign_registration(record, provider.key.hex())
    return record, sig


def test_resolve_returns_registered_provider():
    reg = _registry()
    provider = Account.create()
    record, sig = _signed(provider)
    reg.register(record, sig)
    resolved = reg.resolve("image-generation")
    assert isinstance(resolved, list) and len(resolved) == 1
    assert resolved[0]["provider_url"] == "http://sp:8000"
    assert resolved[0]["owner_address"].lower() == provider.address.lower()
    assert resolved[0]["price_wei"] == 20_000


def test_resolve_unknown_type_raises():
    reg = _registry()
    record, sig = _signed(Account.create())
    reg.register(record, sig)
    with pytest.raises(CapabilityNotFound):
        reg.resolve("translation")


def test_list_filters_by_type():
    reg = _registry()
    a, b = Account.create(), Account.create()
    ra, sa = _signed(a, capability_type="image-generation", provider_url="http://a")
    rb, sb = _signed(b, capability_type="translation", provider_url="http://b")
    reg.register(ra, sa)
    reg.register(rb, sb)
    assert {c["id"].lower() for c in reg.list("image-generation")} == {a.address.lower()}
    assert {c["id"].lower() for c in reg.list()} == {a.address.lower(), b.address.lower()}


def test_inactive_capability_not_resolved():
    reg = _registry()
    record, sig = _signed(Account.create(), provider_url="http://a", active=False)
    reg.register(record, sig)
    with pytest.raises(CapabilityNotFound):
        reg.resolve("image-generation")
    assert reg.list("image-generation") == []


def test_register_missing_fields_raises():
    reg = _registry()
    provider = Account.create()
    record, sig = _signed(provider)
    del record["provider_url"]
    with pytest.raises(ValueError):
        reg.register(record, sig)
