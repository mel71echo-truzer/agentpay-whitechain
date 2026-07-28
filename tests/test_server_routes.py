"""Фаза 2 (follow-up) — HTTP-маршрути сервера + контроль доступу.

Закриває дві знахідки:
  - /admin/* більше не відкритий (був би того ж класу, що й старий
    /registry/register): вимкнений без ADMIN_API_TOKEN, під Bearer-токеном якщо
    заданий;
  - service_provider/server.py раніше мав 0% покриття pytest — тут через
    FastAPI TestClient покриваються всі маршрути.

Мапа доступу (маршрут → хто може → де перевірка) — див. docs/audit/04-route-access.md.

Детерміновано: локальний eth-tester (conftest) + in-process TestClient.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import config  # noqa: E402
import registry_auth  # noqa: E402
import service_provider.server as server  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402
from web3 import Web3  # noqa: E402


def _sign_chain_time(fx, agent, resource, value_wei=None):
    """Підписує авторизацію з часовим вікном від ЧАСУ ЛАНЦЮГА (block.timestamp),
    а не від wall-clock. Спільний eth-tester у сесії дрейфує час уперед, тож
    wall-clock-вікно виглядало б простроченим для тестів, що біжать пізно. Це
    артефакт тест-інфри (не баг продукту): контракт звіряє саме block.timestamp."""
    value_wei = fx.price_wei if value_wei is None else value_wei
    chain_ts = fx.w3.eth.get_block("latest")["timestamp"]
    import os

    salt = os.urandom(32)
    nonce = Web3.keccak(resource.encode("utf-8") + salt)
    message = {
        "from": agent.address, "to": Web3.to_checksum_address(fx.facilitator_acct.address),
        "value": value_wei, "validAfter": 0, "validBefore": chain_ts + 3600, "nonce": nonce,
    }
    domain = {"name": "Test EURC", "version": "1", "chainId": fx.w3.eth.chain_id, "verifyingContract": fx.teurc.address}
    signed = Account.sign_message(encode_typed_data(domain, agent_client.TRANSFER_AUTH_TYPES, message), agent.key.hex())
    return {
        "authorization": {
            "from": message["from"], "to": message["to"], "value": message["value"],
            "validAfter": message["validAfter"], "validBefore": message["validBefore"],
            "nonce": "0x" + nonce.hex(), "v": signed.v,
            "r": Web3.to_hex(signed.r.to_bytes(32, "big")), "s": Web3.to_hex(signed.s.to_bytes(32, "big")),
        },
        "resource": resource, "resource_salt": "0x" + salt.hex(),
    }


@pytest.fixture
def client(facilitator_setup, monkeypatch):
    # init_facilitator сіє підписаний seed-запис реєстру (SP-ключ у conftest).
    server.init_facilitator(facilitator_setup.facilitator)
    monkeypatch.setattr(config, "ADMIN_API_TOKEN", "")  # дефолт: /admin вимкнено
    return TestClient(server.app)


# ---------- відкриті (публічні за задумом) читання ----------

def test_photos_public(client):
    r = client.get("/photos")
    assert r.status_code == 200
    assert isinstance(r.json()["price_wei"], int)


def test_registry_capabilities_public_and_signed(client):
    r = client.get("/registry/capabilities")
    assert r.status_code == 200
    caps = r.json()["capabilities"]
    assert len(caps) == 1
    cap = caps[0]
    # Запис підписаний і перевіряється (owner == id).
    assert registry_auth.verify_registration(cap, cap["signature"]).lower() == cap["id"].lower()
    assert cap["pay_to"].lower() == config.FACILITATOR_WALLET_ADDRESS.lower()


def test_balance_public_aggregate_only(client, monkeypatch):
    # Публічно — лише агрегат; детальний sales (адреси покупців) прихований.
    r = client.get("/balance")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["earned_wei"], int)
    assert "sales" not in body  # деталізація не публічна
    # Під адмін-токеном — деталізація доступна.
    monkeypatch.setattr(config, "ADMIN_API_TOKEN", "s3cret")
    admin = client.get("/balance", headers={"Authorization": "Bearer s3cret"}).json()
    assert "sales" in admin


def test_photo_get_always_402(client):
    r = client.get("/photo/kyiv-lavra")
    assert r.status_code == 402
    assert r.json()["accepts"][0]["price_wei"] > 0


def test_unknown_photo_404(client):
    assert client.get("/photo/no-such-photo").status_code == 404


# ---------- реєстрація: тільки підписана ----------

def test_register_unsigned_rejected(client):
    r = client.post("/registry/register", json={"id": "0x00", "capability_type": "t", "provider_url": "u", "pay_to": "0x00"})
    assert r.status_code == 400


def test_register_signed_accepted(client):
    from eth_account import Account

    provider = Account.create()
    record = {
        "id": provider.address,
        "capability_type": "translation",
        "provider_url": "http://p",
        "pay_to": "0x000000000000000000000000000000000000dEaD",
        "price_wei": 30_000,
        "min_reputation_tier": 0,
        "active": True,
    }
    sig = registry_auth.sign_registration(record, provider.key.hex())
    r = client.post("/registry/register", json={**record, "signature": sig})
    assert r.status_code == 200
    assert r.json()["registered"]["owner_address"].lower() == provider.address.lower()


def test_photo_post_malformed_body_400(client):
    r = client.post("/photo/kyiv-lavra", content=b"not json")
    assert r.status_code == 400


def test_photo_post_success_settles_and_updates_balance(client, facilitator_setup):
    # Наскрізний платіж через HTTP-шар: підписуємо авторизацію верифікованим
    # агентом і постимо на /photo — покриває шлях settlement у server.py.
    import agent_client

    fx = facilitator_setup
    payload = _sign_chain_time(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")
    r = client.post("/photo/kyiv-lavra", json=payload)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "X-Settlement" in r.headers
    # Заробіток (int wei) зріс на нетто.
    assert client.get("/balance").json()["earned_wei"] > 0


def test_photo_post_denied_returns_402(client, facilitator_setup):
    # Агент без Soul -> facilitator відхиляє -> 402 з причиною (deny-гілка server).
    import agent_client

    fx = facilitator_setup
    payload = _sign_chain_time(fx, fx.unverified_agent, "/photo/kyiv-lavra")
    r = client.post("/photo/kyiv-lavra", json=payload)
    assert r.status_code == 402
    assert "error" in r.json()


# ---------- /admin/* контроль доступу ----------

def test_admin_disabled_without_token(client):
    # ADMIN_API_TOKEN порожній -> 403 (вимкнено, НЕ відкрито).
    r = client.get("/admin/held-settlements")
    assert r.status_code == 403


def test_admin_requires_bearer_when_token_set(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_API_TOKEN", "s3cret")
    assert client.get("/admin/held-settlements").status_code == 401  # без заголовка
    assert client.get(
        "/admin/held-settlements", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401  # неправильний
    ok = client.get("/admin/held-settlements", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert ok.json()["held_count"] == 0
