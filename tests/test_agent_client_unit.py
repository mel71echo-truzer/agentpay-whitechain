"""Phase 3 — unit coverage for agent_client discovery/selection/pay branches.

Pure logic (provider selection) plus HTTP paths driven through a stubbed
`requests`, so no live server is needed. Complements test_agent_client_atomic.py
(signing) and test_agent_client_ledger.py (ledger path).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import registry_auth  # noqa: E402
from eth_account import Account  # noqa: E402


# ---------- select_provider (named criterion, never blind [0]) ----------

def test_select_provider_first_registered_default():
    cands = [{"id": "0xAAA", "provider_url": "http://a"}, {"id": "0xBBB", "provider_url": "http://b"}]
    chosen, criterion = agent_client.select_provider(cands)
    assert chosen["id"] == "0xAAA"
    assert criterion == agent_client.SELECTION_FIRST_REGISTERED


def test_select_provider_allowlist_owner_match():
    cands = [{"id": "0xAAA"}, {"id": "0xBBB"}]
    chosen, criterion = agent_client.select_provider(cands, prefer_owner="0xbbb")
    assert chosen["id"] == "0xBBB"
    assert criterion == agent_client.SELECTION_ALLOWLIST


def test_select_provider_allowlist_owner_miss_raises():
    with pytest.raises(agent_client.CapabilityNotFound):
        agent_client.select_provider([{"id": "0xAAA"}], prefer_owner="0xCCC")


# ---------- resolve_capability (filters unsigned, picks by criterion) ----------

def _signed_record(provider, capability_type="image-generation"):
    record = {
        "id": provider.address,
        "capability_type": capability_type,
        "provider_url": "http://p",
        "pay_to": "0x000000000000000000000000000000000000dEaD",
        "price_wei": 30_000,
        "min_reputation_tier": 0,
        "active": True,
    }
    record["signature"] = registry_auth.sign_registration(record, provider.key.hex())
    return record


def test_resolve_capability_skips_unsigned_and_returns_verified(monkeypatch):
    provider = Account.create()
    good = _signed_record(provider)
    bad = {"id": "0xdead", "capability_type": "image-generation", "provider_url": "http://x", "signature": "0xbad"}
    monkeypatch.setattr(agent_client, "discover_capabilities", lambda url, t=None: [bad, good])

    chosen = agent_client.resolve_capability("http://registry", "image-generation")
    assert chosen["id"] == provider.address
    assert chosen["_selected_by"] == agent_client.SELECTION_FIRST_REGISTERED


def test_resolve_capability_none_verified_raises(monkeypatch):
    bad = {"id": "0xdead", "signature": "0xbad"}
    monkeypatch.setattr(agent_client, "discover_capabilities", lambda url, t=None: [bad])
    with pytest.raises(agent_client.CapabilityNotFound):
        agent_client.resolve_capability("http://registry", "image-generation")


def test_resolve_provider_url_returns_url(monkeypatch):
    provider = Account.create()
    monkeypatch.setattr(agent_client, "discover_capabilities", lambda url, t=None: [_signed_record(provider)])
    assert agent_client.resolve_provider_url("http://registry", "image-generation") == "http://p"


# ---------- HTTP paths via a stubbed requests ----------

class _Resp:
    def __init__(self, status, *, content=b"", json_body=None, headers=None, text=""):
        self.status_code = status
        self.content = content
        self._json = json_body
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_discover_capabilities_hits_registry(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Resp(200, json_body={"capabilities": [{"id": "0x1"}]})

    monkeypatch.setattr(agent_client.requests, "get", fake_get)
    caps = agent_client.discover_capabilities("http://registry", "image-generation")
    assert caps == [{"id": "0x1"}]
    assert captured["url"].endswith("/registry/capabilities")
    assert captured["params"] == {"type": "image-generation"}


def test_pay_and_fetch_already_have_it_on_200(monkeypatch):
    monkeypatch.setattr(
        agent_client.requests, "get",
        lambda url, timeout=None: _Resp(200, content=b"IMG", headers={"content-type": "image/png"}),
    )
    result = agent_client.pay_and_fetch("http://sp/photo/x", private_key="0x" + "1" * 64)
    assert result.already_had_it is True
    assert result.content == b"IMG"


def test_pay_and_fetch_unexpected_status_raises(monkeypatch):
    monkeypatch.setattr(agent_client.requests, "get", lambda url, timeout=None: _Resp(500, text="boom"))
    with pytest.raises(agent_client.PaymentFailed):
        agent_client.pay_and_fetch("http://sp/photo/x", private_key="0x" + "1" * 64)


def test_pay_and_fetch_pay_to_mismatch_hard_fails(monkeypatch):
    # 402 whose payTo disagrees with the signed registry record -> refuse to pay.
    body = {"accepts": [{"payTo": "0xAAA", "price_wei": 20000, "resource": "/photo/x", "asset_address": "0xT", "price_teurc": "0.02"}]}
    monkeypatch.setattr(agent_client.requests, "get", lambda url, timeout=None: _Resp(402, json_body=body))
    with pytest.raises(agent_client.PaymentFailed):
        agent_client.pay_and_fetch(
            "http://sp/photo/x", private_key="0x" + "1" * 64, expected_pay_to="0xBBB"
        )


# ---------- SpendLedger ----------

def test_spend_ledger_enforces_and_records(tmp_path):
    ledger = agent_client.SpendLedger(path=str(tmp_path / "l.json"), max_spend_wei=100)
    ledger.ensure_can_spend(60)
    ledger.record(60, to="0xSeller", relay_tx_hash="0xabc")
    assert ledger.spent_wei == 60
    with pytest.raises(agent_client.SpendLimitExceeded):
        ledger.ensure_can_spend(50)  # 60 + 50 > 100
    ledger.reset()
    assert ledger.spent_wei == 0
