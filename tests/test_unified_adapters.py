"""Phase 2 — adapter tests (unit; fakes for wrapped components, no chain).

Verify each adapter faithfully translates between the existing components and the
canonical interfaces. The TrustGate is additionally proven against the REAL
identity reader in test_unified_trust_integration.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified import PaymentAsset, PaymentAuthorization, SettlementStatus  # noqa: E402
from unified.adapters import (  # noqa: E402
    StandardX402Adapter,
    service_from_capability_record,
    service_from_local_registry,
)
from unified.adapters.settlement import (  # noqa: E402
    FacilitatorSettlementEngine,
    authorization_to_engine_inputs,
)
from unified.adapters.trust import FacilitatorTrustGate  # noqa: E402
from unified.models import KYAStatus, Provider, Service  # noqa: E402

SIG = "0x" + "11" * 65  # a well-formed 65-byte signature (v byte = 0x11 -> +27)


# ---------- StandardX402Adapter ----------

def test_x402_round_trip_encode_parse():
    adapter = StandardX402Adapter()
    auth = PaymentAuthorization(
        from_address="0xBuyer", to_address="0xRouter", value_units=20000,
        valid_after=0, valid_before=1893456000, nonce="0x" + "ab" * 32, signature=SIG,
        network="whitechain-testnet",
    )
    header = adapter.encode_x_payment_header(auth)
    back = adapter.parse_x_payment_header(header)
    assert back.from_address == "0xBuyer"
    assert back.to_address == "0xRouter"
    assert back.value_units == 20000
    assert back.nonce == auth.nonce
    assert back.signature == SIG
    assert back.asset is PaymentAsset.TEURC


def test_x402_build_payment_required_standard_shape():
    body = StandardX402Adapter().build_payment_required(
        resource="/photo/x", amount_units=20000, pay_to="0xRouter",
        asset=PaymentAsset.TEURC, asset_address="0xToken", network="whitechain-testnet",
    )
    accept = body["accepts"][0]
    assert body["x402Version"] == 1
    assert accept["maxAmountRequired"] == "20000"   # standard x402 field, string
    assert accept["payTo"] == "0xRouter"
    assert accept["asset"] == "0xToken"


def test_x402_malformed_header_raises_valueerror():
    with pytest.raises(ValueError):
        StandardX402Adapter().parse_x_payment_header("not-base64-or-json")


# ---------- discovery mappers ----------

def test_service_from_local_registry_preserves_quality_and_apusd():
    rec = {"name": "Weather", "description": "Get weather information", "url": "http://x",
           "endpoint": "/weather", "method": "GET", "category": "weather", "price_units": 5000,
           "currency": "apUSD", "network": "whitechain-testnet", "rating": 4.2,
           "success_rate": 0.96, "latency_ms": 180}
    svc = service_from_local_registry(rec)
    assert isinstance(svc, Service)
    assert svc.rating == 4.2 and svc.latency_ms == 180
    assert svc.payment_asset is PaymentAsset.APUSD          # local asset preserved, not rewritten
    assert svc.provider.kya_status is KYAStatus.UNKNOWN     # no on-chain trust locally


def test_service_from_capability_record_maps_identity_and_teurc():
    rec = {"id": "0xProvider", "owner_address": "0xprovider", "capability_type": "image-generation",
           "provider_url": "http://sp:8000", "pay_to": "0xRouter", "price_wei": 100000,
           "min_reputation_tier": 1, "active": True}
    svc = service_from_capability_record(rec)
    assert svc.category == "image-generation"
    assert svc.price_units == 100000
    assert svc.payment_asset is PaymentAsset.TEURC
    assert svc.provider.provider_id == "0xProvider"
    assert svc.provider.signature_is_bound() is True       # owner_address == id (case-insensitive)
    assert "min_reputation_tier=1" in svc.provider.policy_tags


# ---------- FacilitatorSettlementEngine ----------

def test_authorization_to_engine_inputs_splits_signature():
    auth = PaymentAuthorization(from_address="0xa", to_address="0xb", value_units=20000,
                                valid_after=0, valid_before=1, nonce="0x00", signature=SIG)
    message, auth_dict = authorization_to_engine_inputs(auth)
    assert message["value"] == 20000 and message["from"] == "0xa"
    assert auth_dict["v"] == 0x11 + 27          # v normalized
    assert auth_dict["r"].startswith("0x") and len(auth_dict["r"]) == 66


class _FakeEngine:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc

    def settle(self, message, authorization):
        if self._exc:
            raise self._exc
        return self._result


def _auth():
    return PaymentAuthorization(from_address="0xa", to_address="0xb", value_units=20000,
                               valid_after=0, valid_before=1, nonce="0x00", signature=SIG)


def test_settlement_adapter_maps_confirmed():
    eng = FacilitatorSettlementEngine(_FakeEngine(result={
        "confirmed": True, "status": "confirmed", "relay_tx_hash": "0xrelay",
        "forward_tx_hash": "0xfwd", "fee_wei": 100, "net_wei": 19900}))
    res = eng.settle(_auth())
    assert res.status is SettlementStatus.CONFIRMED and res.ok
    assert res.fee_units == 100 and res.net_units == 19900
    assert res.asset is PaymentAsset.TEURC and res.relay_tx_hash == "0xrelay"


def test_settlement_adapter_maps_submitted():
    eng = FacilitatorSettlementEngine(_FakeEngine(result={
        "confirmed": False, "status": "submitted", "relay_tx_hash": "0xr",
        "forward_tx_hash": "0xr", "fee_wei": 0, "net_wei": 20000}))
    assert eng.settle(_auth()).status is SettlementStatus.SUBMITTED


def test_settlement_adapter_rejects_asset_mismatch():
    # A tEURC engine must refuse an apUSD authorization (no wrapped call happens).
    engine = FacilitatorSettlementEngine(_FakeEngine(result={"confirmed": True}), asset=PaymentAsset.TEURC)
    apusd_auth = PaymentAuthorization(from_address="0xa", to_address="0xb", value_units=5000,
                                      valid_after=0, valid_before=1, nonce="0x00", signature=SIG,
                                      asset=PaymentAsset.APUSD)
    res = engine.settle(apusd_auth)
    assert res.status is SettlementStatus.FAILED
    assert "mismatch" in res.reason.lower()


def test_settlement_adapter_maps_funds_held_and_failed():
    from facilitator.settlement import SettlementError, SettlementForwardError
    held = FacilitatorSettlementEngine(_FakeEngine(exc=SettlementForwardError(
        "x", relay_tx_hash="0xrelay", net_wei=19900)))
    r1 = held.settle(_auth())
    assert r1.status is SettlementStatus.FUNDS_HELD and r1.relay_tx_hash == "0xrelay"
    failed = FacilitatorSettlementEngine(_FakeEngine(exc=SettlementError("boom")))
    r2 = failed.settle(_auth())
    assert r2.status is SettlementStatus.FAILED and not r2.ok
    assert "boom" not in r2.reason      # buyer-safe: internal detail not surfaced


# ---------- FacilitatorTrustGate (fake identity + REAL policy) ----------

class _FakeIdentity:
    def __init__(self, identity):
        self._id = identity

    def get_agent_identity(self, address):
        return self._id


def _svc_and_provider(addr="0xAgent"):
    return Provider(provider_id=addr), Service(name="svc", category="weather")


def test_trust_gate_allows_verified_with_real_policy():
    ident = {"has_soul": True, "verified": True, "soul_id": 5, "sbt_count": 1,
             "reputation_tier": 1, "reputation_score": 60.0}
    gate = FacilitatorTrustGate(_FakeIdentity(ident))  # real facilitator.policy.check_policy
    prov, svc = _svc_and_provider()
    d = gate.evaluate(prov, svc, requested_amount_units=20000, min_reputation_tier=1)
    assert d.allowed is True and d.kya_status is KYAStatus.VERIFIED and d.reputation_tier == 1
    assert d.policy_decision == "allow"


def test_trust_gate_denies_no_soul():
    gate = FacilitatorTrustGate(_FakeIdentity(
        {"has_soul": False, "verified": False, "soul_id": 0, "sbt_count": 0, "reputation_tier": 0}))
    prov, svc = _svc_and_provider()
    d = gate.evaluate(prov, svc, requested_amount_units=20000)
    assert d.allowed is False and d.kya_status is KYAStatus.UNKNOWN
    assert d.policy_decision == "deny:not-kya"


def test_trust_gate_denies_below_tier():
    ident = {"has_soul": True, "verified": True, "soul_id": 5, "sbt_count": 0,
             "reputation_tier": 0, "reputation_score": 10.0}
    gate = FacilitatorTrustGate(_FakeIdentity(ident))
    prov, svc = _svc_and_provider()
    d = gate.evaluate(prov, svc, requested_amount_units=100000, min_reputation_tier=1)
    assert d.allowed is False and d.policy_decision == "deny:below-tier"
