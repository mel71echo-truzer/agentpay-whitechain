"""Фаза 2, F4 #A (CRITICAL) — підписана реєстрація + payTo-binding + перевірка
на боці агента. Закриває отруєння discovery / перенаправлення коштів.

Що фіксуємо (падало б до патча — раніше register був відкритий, resolve брав
matches[0] за керованим id):
  1. реєстрація без валідного підпису — відмова;
  2. `id` мусить == адресі підписанта (не можна зайняти чужий/довільний id);
  3. підмінити pay_to без ключа власника не можна (recover != id -> відмова);
  4. порядок resolve() — за СЕРВЕРНИМ created_at/rowid, НЕ за id (адресу можна
     грайндити vanity-нулями); resolve повертає ВСІ збіги;
  5. агент HARD-FAIL, якщо payTo з 402 != підписаний pay_to.

Детерміновано: без мережі (агентський крок — monkeypatch requests).
"""

import sys
from pathlib import Path

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import registry_auth  # noqa: E402
from facilitator.capability import CapabilityRegistry  # noqa: E402
from facilitator.store import Store  # noqa: E402

PAY_TO = "0x000000000000000000000000000000000000dEaD"


def _record(provider, *, provider_url="http://sp", pay_to=PAY_TO, ctype="image-generation"):
    return {
        "id": provider.address,
        "capability_type": ctype,
        "provider_url": provider_url,
        "pay_to": pay_to,
        "price_wei": 20_000,
        "min_reputation_tier": 0,
        "active": True,
    }


def _reg():
    return CapabilityRegistry(Store(":memory:"))


def test_unsigned_registration_rejected():
    reg = _reg()
    with pytest.raises(ValueError):
        reg.register(_record(Account.create()), signature="")


def test_id_must_equal_signer():
    reg = _reg()
    provider = Account.create()
    attacker = Account.create()
    record = _record(provider)
    # Підписує attacker, але id стверджує provider -> owner != id -> відмова.
    sig = registry_auth.sign_registration(record, attacker.key.hex())
    with pytest.raises(ValueError):
        reg.register(record, sig)


def test_tampered_payto_rejected():
    reg = _reg()
    provider = Account.create()
    record = _record(provider, pay_to=PAY_TO)
    sig = registry_auth.sign_registration(record, provider.key.hex())
    # Підміняємо pay_to ПІСЛЯ підпису: канонічне повідомлення інше -> recover
    # дає іншу адресу -> owner != id -> відмова.
    record["pay_to"] = "0x000000000000000000000000000000000000BEEF"
    with pytest.raises(ValueError):
        reg.register(record, sig)


def test_valid_registration_binds_owner_and_payto():
    reg = _reg()
    provider = Account.create()
    record = _record(provider)
    sig = registry_auth.sign_registration(record, provider.key.hex())
    normalized = reg.register(record, sig)
    assert normalized["owner_address"].lower() == provider.address.lower()
    assert normalized["id"].lower() == provider.address.lower()
    assert normalized["pay_to"].lower() == PAY_TO.lower()


def test_resolve_order_is_server_side_not_by_id():
    # Два провайдери; реєструємо СПОЧАТКУ того, чия адреса БІЛЬША. Якби порядок
    # був ORDER BY id ASC, першим би був менший. Ми ж очікуємо порядок реєстрації
    # (created_at) — тобто першим лишається більший-id, зареєстрований раніше.
    a, b = Account.create(), Account.create()
    if int(a.address, 16) < int(b.address, 16):
        a, b = b, a  # a — з БІЛЬШОЮ адресою
    reg = _reg()
    for provider in (a, b):  # a (більший id) реєструється першим
        rec = _record(provider)
        reg.register(rec, registry_auth.sign_registration(rec, provider.key.hex()))
    resolved = reg.resolve("image-generation")
    assert [c["id"].lower() for c in resolved] == [a.address.lower(), b.address.lower()]
    # Тобто НЕ відсортовано за id (менший був би першим) — підтверджуємо:
    assert resolved[0]["id"].lower() != b.address.lower()


def test_agent_verifies_discovery_signature():
    provider = Account.create()
    record = _record(provider)
    sig = registry_auth.sign_registration(record, provider.key.hex())
    record["signature"] = sig
    assert agent_client.verify_capability_record(record).lower() == provider.address.lower()
    # Підроблений запис (підмінили provider_url після підпису) -> не верифікується.
    record["provider_url"] = "http://attacker"
    with pytest.raises(ValueError):
        agent_client.verify_capability_record(record)


def test_agent_hard_fails_on_payto_mismatch(monkeypatch):
    # 402 повертає payTo, що НЕ збігається з підписаним pay_to реєстру -> відмова.
    class _Resp:
        status_code = 402

        def json(self):
            return {
                "accepts": [
                    {
                        "payTo": "0x000000000000000000000000000000000000BEEF",  # чужий!
                        "price_wei": 20_000,
                        "resource": "/photo/x",
                        "asset_address": "0x0000000000000000000000000000000000000001",
                    }
                ]
            }

    monkeypatch.setattr(agent_client.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(agent_client.PaymentFailed) as exc:
        agent_client.pay_and_fetch(
            "http://sp/photo/x", private_key="0x" + "11" * 32, expected_pay_to=PAY_TO
        )
    assert "payTo" in str(exc.value)
