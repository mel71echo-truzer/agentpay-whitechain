"""Фаза 2, M3 (payment.py) — MAJOR: overpayment + типізація входу (F6).

Рішення автора №5: приймати value > price мовчки не можна. Шляху повернення
надлишку в архітектурі немає (facilitator релеїть повний підписаний value),
тож єдиний коректний варіант — ВІДХИЛЯТИ переплату. Плюс: нечислові поля
авторизації дають чисту відмову, а не необроблений виняток.

Тести падали б до патча: раніше value > price проходив (valid=True), бо
перевірялося лише value < price.

Детерміновано: локальний eth-tester (conftest), без мережі/часу.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402
from web3 import Web3  # noqa: E402

import agent_client  # noqa: E402


def _sign(fx, agent, resource, value_wei):
    return agent_client.build_and_sign_authorization(
        agent.key.hex(), fx.facilitator_acct.address, value_wei, resource, fx.teurc.address, fx.w3.eth.chain_id
    )


def _sign_custom(fx, agent, resource, *, to, valid_after, valid_before, value_wei=None):
    """Підписує authorization вручну з нетиповими to/validAfter/validBefore."""
    value_wei = fx.price_wei if value_wei is None else value_wei
    salt = os.urandom(32)
    nonce = Web3.keccak(resource.encode("utf-8") + salt)
    message = {
        "from": agent.address,
        "to": Web3.to_checksum_address(to),
        "value": value_wei,
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": nonce,
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
        "resource": resource,
        "resource_salt": "0x" + salt.hex(),
    }


def test_overpayment_rejected(facilitator_setup):
    fx = facilitator_setup
    # Підписує на суму БІЛЬШУ за ціну ресурсу (0.03 > 0.02).
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei + 10_000)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )

    assert result["valid"] is False
    assert "переплат" in result["reason"].lower() or "overpay" in result["reason"].lower()


def test_exact_payment_accepted(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei)
    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is True


def test_non_numeric_value_rejected_cleanly(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei)
    bad = dict(payload["authorization"])
    bad["value"] = "not-a-number"  # нечислове поле від недовіреного клієнта

    # Не має підняти виняток — чиста відмова.
    result = fx.facilitator.verify_and_settle(
        bad, payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "тип" in result["reason"].lower() or "підпис" in result["reason"].lower()


def test_wrong_recipient_rejected(facilitator_setup):
    # payTo != facilitator: кошти пішли б не на facilitator (payment.py:116).
    fx = facilitator_setup
    stranger = "0x000000000000000000000000000000000000dEaD"
    now = int(time.time())
    payload = _sign_custom(
        fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra",
        to=stranger, valid_after=0, valid_before=now + 300,
    )
    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "facilitator" in result["reason"].lower()


def test_negative_value_rejected(facilitator_setup):
    # value < 0 не піддається EIP-712-підпису (uint256), але недовірений клієнт
    # може підсунути -1 у dict авторизації. Валідатор має відхилити ДО ре-енкоду
    # (payment.py, гілка value<0), а не впасти винятком.
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei)
    bad = dict(payload["authorization"])
    bad["value"] = -1
    result = fx.facilitator.verify_and_settle(
        bad, payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "від'ємн" in result["reason"].lower()


def test_malformed_signature_rejected_cleanly(facilitator_setup):
    # Погані r/s -> recover кидає -> чиста відмова (payment.py recover-branch).
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei)
    bad = dict(payload["authorization"])
    bad["r"] = "0xzzzz"  # не hex -> recover кидає (не просто інша адреса)
    result = fx.facilitator.verify_and_settle(
        bad, payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "підпис" in result["reason"].lower()


def test_malformed_nonce_format_rejected(facilitator_setup):
    # nonce не hex -> _to_bytes32 падає -> чиста відмова (payment.py nonce-branch).
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei)
    bad = dict(payload["authorization"])
    bad["nonce"] = "zzzz-not-hex"
    result = fx.facilitator.verify_and_settle(
        bad, payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "nonce" in result["reason"].lower() or "формат" in result["reason"].lower()


def test_not_yet_valid_authorization_rejected(facilitator_setup):
    # validAfter у майбутньому: авторизація ще не дійсна (payment.py:106).
    fx = facilitator_setup
    now = int(time.time())
    payload = _sign_custom(
        fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra",
        to=fx.facilitator_acct.address, valid_after=now + 3600, valid_before=now + 7200,
    )
    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "ще не дійсна" in result["reason"] or "not" in result["reason"].lower()
