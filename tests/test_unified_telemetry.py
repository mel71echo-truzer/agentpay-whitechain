"""Phase 5 STEP 7 — structured telemetry: correct fields, and NEVER secrets."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified.telemetry import PaymentTelemetry, new_request_id  # noqa: E402


def test_request_id_unique():
    assert new_request_id() != new_request_id()


def test_to_log_dict_carries_expected_fields():
    tel = PaymentTelemetry(provider_id="0xprov", service_id="weather", selected_score=0.928,
                           trust_decision="allow", payment_amount=20_000, asset="tEURC",
                           settlement_status="confirmed", tx_hash="0xabc")
    d = tel.to_log_dict()
    for k in ("request_id", "provider_id", "service_id", "selected_score", "trust_decision",
              "payment_amount", "asset", "settlement_status", "tx_hash"):
        assert k in d
    assert d["asset"] == "tEURC" and d["payment_amount"] == 20_000


def test_telemetry_guard_rejects_secret_fields():
    from unified.telemetry import _assert_no_secrets
    # legit fields (incl. names containing v/r/s) must pass
    _assert_no_secrets({"service_id": "weather", "provider_id": "0x", "trust_decision": "allow"})
    # secret-like keys must trip the guard
    for bad in ({"signature": "0x"}, {"private_key": "x"}, {"v": 27}, {"s": "x"}, {"authorization": {}}):
        with pytest.raises(AssertionError):
            _assert_no_secrets({"settlement_status": "ok", **bad})


def test_server_emits_telemetry_without_secrets():
    # Reuse the chain-free harness from the failure-path module.
    from tests.test_unified_failure_paths import ALLOW, SpyGate, SpySettlement, _header, _server  # noqa: E402

    captured = []
    server = _server(gate=SpyGate(ALLOW), settlement=SpySettlement())
    server.on_telemetry = captured.append
    status, _ = server.fulfill(_header())
    assert status == 200
    assert len(captured) == 1
    d = captured[0].to_log_dict()          # would raise if any secret slipped in
    assert d["trust_decision"] == "allow" and d["settlement_status"] == "confirmed"
    assert "signature" not in d and "v" not in d
