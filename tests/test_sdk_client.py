"""Phase 4 — AgentPay SDK facade (agentpay_sdk.AgentPayClient).

The facade delegates to the tested agent_client primitives; here we assert the
delegation and the constructor contract, driving HTTP through a stubbed requests
so no live server is needed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import registry_auth  # noqa: E402
from agentpay_sdk import AgentPayClient, PurchaseResult  # noqa: E402
from eth_account import Account  # noqa: E402

KEY = "0x" + "1" * 64


def test_constructor_requires_key_and_registry():
    with pytest.raises(ValueError):
        AgentPayClient(private_key="", registry_url="http://r", chain_id=2625)
    with pytest.raises(ValueError):
        AgentPayClient(private_key=KEY, registry_url="", chain_id=2625)
    c = AgentPayClient(private_key=KEY, registry_url="http://r/", chain_id=2625)
    assert c.registry_url == "http://r"  # trailing slash trimmed
    assert c.chain_id == 2625


def test_discover_delegates(monkeypatch):
    monkeypatch.setattr(agent_client, "discover_capabilities", lambda url, t=None: [{"id": "0x1"}])
    c = AgentPayClient(private_key=KEY, registry_url="http://r", chain_id=2625)
    assert c.discover("image-generation") == [{"id": "0x1"}]


def test_pay_and_fetch_delegates_with_bound_params(monkeypatch):
    captured = {}

    def fake_pay(url, private_key=None, ledger=None, chain_id=None, expected_pay_to=None):
        captured.update(url=url, private_key=private_key, chain_id=chain_id, expected_pay_to=expected_pay_to)
        return PurchaseResult(content=b"X", content_type="image/png")

    monkeypatch.setattr(agent_client, "pay_and_fetch", fake_pay)
    c = AgentPayClient(private_key=KEY, registry_url="http://r", chain_id=2625)
    result = c.pay_and_fetch("http://sp/photo/x", expected_pay_to="0xPay")
    assert result.content == b"X"
    assert captured == {"url": "http://sp/photo/x", "private_key": KEY, "chain_id": 2625, "expected_pay_to": "0xPay"}


def test_purchase_resolves_provider_and_pins_pay_to(monkeypatch):
    provider = Account.create()
    pay_to = "0x000000000000000000000000000000000000dEaD"
    record = {
        "id": provider.address,
        "capability_type": "image-generation",
        "provider_url": "http://provider:8000/",
        "pay_to": pay_to,
        "price_wei": 20_000,
        "min_reputation_tier": 0,
        "active": True,
    }
    record["signature"] = registry_auth.sign_registration(record, provider.key.hex())
    monkeypatch.setattr(agent_client, "discover_capabilities", lambda url, t=None: [record])

    seen = {}

    def fake_pay(url, private_key=None, ledger=None, chain_id=None, expected_pay_to=None):
        seen.update(url=url, expected_pay_to=expected_pay_to)
        return PurchaseResult(content=b"IMG", content_type="image/png")

    monkeypatch.setattr(agent_client, "pay_and_fetch", fake_pay)

    c = AgentPayClient(private_key=KEY, registry_url="http://r", chain_id=2625)
    result = c.purchase("image-generation", "/photo/kyiv-lavra")
    assert result.content == b"IMG"
    # provider_url joined with resource, pay_to pinned from the SIGNED record.
    assert seen["url"] == "http://provider:8000/photo/kyiv-lavra"
    assert seen["expected_pay_to"] == pay_to
