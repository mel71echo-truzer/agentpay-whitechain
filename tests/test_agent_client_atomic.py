"""Клієнтський підпис для атомарного (Phase 2.5) шляху.

Дзеркалить C-1: у atomic-режимі клієнт має підписати ReceiveWithAuthorization
(to == router) з ПОХІДНИМ nonce = computeNonce(seller, feeBps, resourceHash),
байт-у-байт як Solidity AgentPayRouter.computeNonce. Ці тести — суто офчейн
(підпис + recovery), без ланцюжка: вони падали б до atomic-гілки в
build_and_sign_authorization і проходять після неї.
"""

import sys
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import router_binding  # noqa: E402

CHAIN_ID = 2625
TEURC = Web3.to_checksum_address("0x" + "ab" * 20)
ROUTER = Web3.to_checksum_address("0x" + "cd" * 20)
SELLER = Web3.to_checksum_address("0x" + "ef" * 20)
FEE_BPS = 50
PRICE_WEI = 20_000
RESOURCE = "/photo/kyiv-lavra"


def _sign(**overrides):
    kwargs = dict(
        private_key=Account.create().key.hex(),
        to_address=ROUTER,
        value_wei=PRICE_WEI,
        resource=RESOURCE,
        teurc_address=TEURC,
        chain_id=CHAIN_ID,
        settlement_mode="atomic",
        seller=SELLER,
        fee_bps=FEE_BPS,
    )
    kwargs.update(overrides)
    return kwargs["private_key"], agent_client.build_and_sign_authorization(**kwargs)


def test_atomic_signs_receive_to_router_with_derived_nonce():
    pk, payload = _sign()
    auth = payload["authorization"]

    # `to` — роутер (payee ReceiveWithAuthorization), не facilitator.
    assert auth["to"] == ROUTER

    # nonce — точне дзеркало Solidity computeNonce над (seller, feeBps, resourceHash).
    salt = bytes.fromhex(payload["resource_salt"].removeprefix("0x"))
    rhash = router_binding.resource_hash(RESOURCE, salt)
    expected_nonce = router_binding.compute_router_nonce(SELLER, FEE_BPS, rhash)
    assert auth["nonce"] == "0x" + expected_nonce.hex()


def test_atomic_signature_recovers_from_over_receive_typehash():
    pk, payload = _sign()
    auth = payload["authorization"]
    domain = {"name": "Test EURC", "version": "1", "chainId": CHAIN_ID, "verifyingContract": TEURC}
    message = {
        "from": auth["from"],
        "to": auth["to"],
        "value": auth["value"],
        "validAfter": auth["validAfter"],
        "validBefore": auth["validBefore"],
        "nonce": bytes.fromhex(auth["nonce"].removeprefix("0x")),
    }
    signable = encode_typed_data(domain, router_binding.RECEIVE_AUTH_TYPES, message)
    recovered = Account.recover_message(signable, vrs=(auth["v"], auth["r"], auth["s"]))
    assert recovered == auth["from"] == Account.from_key(pk).address


def test_changing_seller_changes_the_nonce():
    """Прив'язка продавця: інший seller -> інший nonce (той самий підпис уже
    не відновиться до `from` у tEURC — суть C-1)."""
    _, p_honest = _sign(seller=SELLER)
    salt = bytes.fromhex(p_honest["resource_salt"].removeprefix("0x"))
    rhash = router_binding.resource_hash(RESOURCE, salt)
    attacker = Web3.to_checksum_address("0x" + "99" * 20)
    assert router_binding.compute_router_nonce(SELLER, FEE_BPS, rhash) != router_binding.compute_router_nonce(
        attacker, FEE_BPS, rhash
    )


def test_legacy_mode_still_binds_nonce_to_resource_only():
    """Дефолтний legacy-режим не змінився: nonce = keccak(resource‖salt),
    to = переданий facilitator, тип Transfer."""
    payload = agent_client.build_and_sign_authorization(
        Account.create().key.hex(),
        Web3.to_checksum_address("0x" + "12" * 20),  # facilitator
        PRICE_WEI,
        RESOURCE,
        TEURC,
        CHAIN_ID,
    )
    auth = payload["authorization"]
    salt = bytes.fromhex(payload["resource_salt"].removeprefix("0x"))
    assert auth["nonce"] == "0x" + Web3.keccak(RESOURCE.encode("utf-8") + salt).hex()
