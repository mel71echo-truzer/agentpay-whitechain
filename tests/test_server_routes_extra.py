"""Phase 3 — supplementary HTTP-route coverage for service_provider/server.py.

Targets the guard/error branches the main suite (test_server_routes.py) doesn't
hit: premium pricing, atomic-mode 402 fields, uninitialized 503s, body-size 413,
unsafe-name 404, and the verify_and_settle exception path. Deterministic:
in-process FastAPI TestClient, module globals toggled via monkeypatch.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import service_provider.server as server  # noqa: E402


@pytest.fixture
def client(facilitator_setup, monkeypatch):
    server.init_facilitator(facilitator_setup.facilitator)
    monkeypatch.setattr(config, "ADMIN_API_TOKEN", "")
    return TestClient(server.app)


# ---------- pricing branches ----------

def test_premium_photo_402_carries_premium_price_and_tier(client, monkeypatch):
    monkeypatch.setattr(config, "PREMIUM_MIN_REPUTATION_TIER", 1)
    r = client.get("/photo/kyiv-motherland-monument")  # the one premium resource
    assert r.status_code == 402
    accept = r.json()["accepts"][0]
    assert accept["price_wei"] == config.PREMIUM_RESOURCE_PRICE_WEI
    assert accept["min_reputation_tier"] == 1


def test_atomic_mode_402_includes_seller_and_fee_bps(client, monkeypatch):
    monkeypatch.setattr(config, "SETTLEMENT_MODE", "atomic")
    monkeypatch.setattr(config, "ROUTER_ADDRESS", "0x000000000000000000000000000000000000R0uter"[:42])
    r = client.get("/photo/kyiv-lavra")
    assert r.status_code == 402
    accept = r.json()["accepts"][0]
    assert accept["settlement_mode"] == "atomic"
    assert accept["seller"].lower() == config.SERVICE_PROVIDER_WALLET_ADDRESS.lower()
    assert accept["fee_bps"] == config.FACILITATOR_FEE_BPS


# ---------- unsafe / missing names ----------

def test_unsafe_photo_name_404(client):
    # Path-traversal-shaped name fails the whitelist -> _resolve_image None -> 404.
    assert client.get("/photo/..%2f..%2fetc%2fpasswd").status_code == 404
    assert client.post("/photo/bad!name", json={}).status_code == 404


# ---------- body-size guards (413-style, returned as 400) ----------

def test_register_body_too_large_rejected(client):
    big = b'{"x":"' + b"a" * (server._MAX_BODY_FIELD_LEN + 10) + b'"}'
    r = client.post("/registry/register", content=big)
    assert r.status_code == 400
    assert "завелике" in r.json()["error"]


def test_pay_body_too_large_rejected(client):
    big = b'{"x":"' + b"a" * (server._MAX_BODY_FIELD_LEN + 10) + b'"}'
    r = client.post("/photo/kyiv-lavra", content=big)
    assert r.status_code == 400
    assert "завелике" in r.json()["error"]


# ---------- malformed register bodies ----------

def test_register_non_dict_body_rejected(client):
    r = client.post("/registry/register", json=["not", "a", "dict"])
    assert r.status_code == 400


# ---------- bad pay bodies (missing auth fields) ----------

def test_pay_missing_auth_fields_400(client):
    r = client.post("/photo/kyiv-lavra", json={"authorization": {"from": "0x0"}, "resource": "/photo/kyiv-lavra"})
    assert r.status_code == 400


# ---------- uninitialized-service 503 guards ----------

def test_registry_capabilities_503_when_uninitialized(monkeypatch):
    monkeypatch.setattr(server, "capability_registry", None)
    assert TestClient(server.app).get("/registry/capabilities").status_code == 503


def test_registry_register_503_when_uninitialized(monkeypatch):
    monkeypatch.setattr(server, "capability_registry", None)
    r = TestClient(server.app).post("/registry/register", json={"id": "x"})
    assert r.status_code == 503


def test_held_settlements_503_when_facilitator_none(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_TOKEN", "s3cret")
    monkeypatch.setattr(server, "facilitator", None)
    r = TestClient(server.app).get("/admin/held-settlements", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 503


def test_pay_503_when_facilitator_none(monkeypatch):
    monkeypatch.setattr(server, "facilitator", None)
    payload = {
        "authorization": {k: "0x0" for k in ("from", "to", "value", "validAfter", "validBefore", "nonce", "v", "r", "s")},
        "resource": "/photo/kyiv-lavra",
        "resource_salt": "0x00",
    }
    r = TestClient(server.app).post("/photo/kyiv-lavra", json=payload)
    assert r.status_code == 503


# ---------- verify_and_settle raising -> 400 (never 500) ----------

def test_pay_verify_exception_returns_400(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected internal failure")

    monkeypatch.setattr(server.facilitator, "verify_and_settle", boom)
    payload = {
        "authorization": {k: "0x0" for k in ("from", "to", "value", "validAfter", "validBefore", "nonce", "v", "r", "s")},
        "resource": "/photo/kyiv-lavra",
        "resource_salt": "0x00",
    }
    r = client.post("/photo/kyiv-lavra", json=payload)
    assert r.status_code == 400  # sanitized, not a 500
