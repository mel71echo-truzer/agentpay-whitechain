"""Pytest-покриття KYA + reputation + анти-replay гілок facilitator.py.

Мета покриття (з завдання): усі гілки KYA + reputation + анти-replay —
- агент БЕЗ Soul -> reject "not KYA-verified"
- агент З Soul, валідний підпис -> контент виданий, комісія списана
- агент З Soul, але БЕЗ потрібного SBT-tier, на преміум-ресурс -> reject
  "insufficient reputation"
- агент З Soul + потрібний SBT -> преміум-ресурс виданий
- підроблений/протермінований підпис -> reject
- повторний nonce -> reject
- невідповідність resource binding -> reject

Усе проти локального EthereumTesterProvider (tests/conftest.py) — без
реального RPC чи ключів.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

import agent_client  # noqa: E402


def _sign(fixture, agent, resource, value_wei=None, valid_after=0, valid_before_delta=300):
    """Будує authorization вручну (не через agent_client) коли треба
    підсунути нетипові поля (протермінований підпис тощо)."""
    value_wei = fixture.price_wei if value_wei is None else value_wei
    return agent_client.build_and_sign_authorization(
        agent.key.hex(),
        fixture.facilitator_acct.address,
        value_wei,
        resource,
        fixture.teurc.address,
        fixture.w3.eth.chain_id,
    )


def test_no_soul_rejected(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.unverified_agent, "/photo/kyiv-lavra")

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )

    assert result["valid"] is False
    assert "not KYA-verified" in result["reason"] or "Soul" in result["reason"]


def test_verified_agent_success_and_fee_accounting(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    balance_before_agent = fx.teurc.functions.balanceOf(fx.verified_no_sbt_agent.address).call()

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )

    assert result["valid"] is True
    assert result["reputation_tier"] == 0
    assert result["relay_tx_hash"] is not None
    assert result["forward_tx_hash"] is not None

    price_wei = fx.price_wei
    expected_fee_wei = (price_wei * config_fee_bps()) // 10_000
    # Авторитетні суми — int wei (B3), звіряємо їх напряму.
    assert result["fee_wei"] == expected_fee_wei
    assert result["net_wei"] == price_wei - expected_fee_wei
    # Людські рядки узгоджені з wei.
    import money as _money
    assert result["fee_teurc"] == _money.wei_to_teurc_str(expected_fee_wei, 6)

    balance_after_agent = fx.teurc.functions.balanceOf(fx.verified_no_sbt_agent.address).call()
    assert balance_before_agent - balance_after_agent == price_wei

    service_provider_balance = fx.teurc.functions.balanceOf(fx.service_provider_acct.address).call()
    assert service_provider_balance == price_wei - expected_fee_wei

    facilitator_balance = fx.teurc.functions.balanceOf(fx.facilitator_acct.address).call()
    assert facilitator_balance == expected_fee_wei


def config_fee_bps():
    import config

    return config.FACILITATOR_FEE_BPS


def test_insufficient_reputation_rejected_on_premium(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-motherland-monument", value_wei=fx.premium_price_wei)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"],
        payload["resource"],
        payload["resource_salt"],
        fx.premium_price_wei,
        min_reputation_tier=fx.premium_min_reputation_tier,
    )

    assert result["valid"] is False
    assert "insufficient reputation" in result["reason"] or "репутація" in result["reason"]
    assert result["reputation_tier"] == 0


def test_verified_agent_with_sbt_can_access_premium(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_agent, "/photo/kyiv-motherland-monument", value_wei=fx.premium_price_wei)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"],
        payload["resource"],
        payload["resource_salt"],
        fx.premium_price_wei,
        min_reputation_tier=fx.premium_min_reputation_tier,
    )

    assert result["valid"] is True
    assert result["reputation_tier"] == 1


def test_forged_signature_rejected(facilitator_setup):
    fx = facilitator_setup
    impostor = Account.create()
    resource = "/photo/kyiv-lavra"

    payload = _sign(fx, impostor, resource)
    forged = dict(payload["authorization"])
    forged["from"] = fx.verified_no_sbt_agent.address  # claims to be a real verified agent

    result = fx.facilitator.verify_and_settle(forged, payload["resource"], payload["resource_salt"], fx.price_wei)

    assert result["valid"] is False
    assert "підпис" in result["reason"].lower() or "signature" in result["reason"].lower()


def test_expired_authorization_rejected(facilitator_setup):
    fx = facilitator_setup
    account = fx.verified_no_sbt_agent
    resource = "/photo/kyiv-lavra"

    # Побудувати authorization, що вже протерміноване (validBefore в минулому).
    import os

    salt = os.urandom(32)
    nonce = Web3.keccak(resource.encode("utf-8") + salt)
    now = int(time.time())
    message = {
        "from": account.address,
        "to": fx.facilitator_acct.address,
        "value": fx.price_wei,
        "validAfter": 0,
        "validBefore": now - 10,  # вже протерміновано
        "nonce": nonce,
    }
    domain = {
        "name": "Test EURC",
        "version": "1",
        "chainId": fx.w3.eth.chain_id,
        "verifyingContract": fx.teurc.address,
    }
    from eth_account.messages import encode_typed_data

    signable = encode_typed_data(domain, agent_client.TRANSFER_AUTH_TYPES, message)
    signed = Account.sign_message(signable, account.key.hex())
    authorization = {
        "from": message["from"],
        "to": message["to"],
        "value": message["value"],
        "validAfter": message["validAfter"],
        "validBefore": message["validBefore"],
        "nonce": "0x" + nonce.hex(),
        "v": signed.v,
        "r": Web3.to_hex(signed.r.to_bytes(32, "big")),
        "s": Web3.to_hex(signed.s.to_bytes(32, "big")),
    }

    result = fx.facilitator.verify_and_settle(authorization, resource, "0x" + salt.hex(), fx.price_wei)

    assert result["valid"] is False
    assert "протерм" in result["reason"] or "expired" in result["reason"].lower()


def test_nonce_replay_rejected(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    first = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert first["valid"] is True

    second = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert second["valid"] is False
    assert "replay" in second["reason"].lower() or "використ" in second["reason"].lower()


def test_resource_binding_mismatch_rejected(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    result = fx.facilitator.verify_and_settle(
        payload["authorization"],
        "/photo/kyiv-sofia-cathedral",  # інший ресурс, ніж підписано
        payload["resource_salt"],
        fx.price_wei,
    )

    assert result["valid"] is False
    assert "ресурс" in result["reason"].lower() or "resource" in result["reason"].lower()


def test_underpayment_rejected(facilitator_setup):
    fx = facilitator_setup
    # Підписує authorization на суму МЕНШУ за очікувану ціну ресурсу.
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=10_000)  # 0.01 < 0.02

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )

    assert result["valid"] is False
    assert "сума" in result["reason"].lower() or "amount" in result["reason"].lower()
