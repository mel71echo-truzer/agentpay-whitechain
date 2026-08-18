"""Phase 7 H-2 — atomic resource binding in the unified validator.

The X-PAYMENT authorization is cryptographically bound to (resource, seller,
feeBps) via the router's derived nonce (Variant 1: ReceiveWithAuthorization +
computeNonce). The server re-derives the nonce from the CANONICAL resource + its
OWN seller/feeBps, so any substitution is rejected. Chain-free: real EIP-712
signatures + router_binding (the same module the client, facilitator and Solidity
share).
"""

import os
import sys
import time
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import router_binding  # noqa: E402
from unified.adapters import StandardX402Adapter  # noqa: E402
from unified.adapters.payment_validator import UnifiedPaymentValidator  # noqa: E402
from unified.models import PaymentAsset  # noqa: E402
from unified.payment import PaymentAuthorization  # noqa: E402

CHAIN_ID = 2625
ASSET = "0x6aadCEc9E885BeeeB1B01924174a4Bb261caA579"
ROUTER = "0x1111111111111111111111111111111111111111"
SELLER = "0x2222222222222222222222222222222222222222"
FEE = 50
PRICE = 20_000
RES_A = "/weather"
RES_B = "/premium-weather"
KEY = "0x" + "5" * 64


def _atomic_validator(seller=SELLER, fee_bps=FEE, router=ROUTER):
    return UnifiedPaymentValidator(chain_id=CHAIN_ID, asset_address=ASSET, settlement_mode="atomic",
                                   seller=seller, fee_bps=fee_bps, router_address=router)


def _sign_atomic(*, resource=RES_A, salt=None, sign_seller=SELLER, sign_fee=FEE, sign_resource=None,
                 sign_salt=None, value=PRICE, valid_before=None, key=KEY):
    """Sign a ReceiveWithAuthorization with a derived nonce. `sign_*` are what the
    CLIENT bakes into the nonce (default = honest); `salt` is what the client SENDS."""
    acct = Account.from_key(key)
    salt = salt if salt is not None else os.urandom(32)
    vb = valid_before if valid_before is not None else int(time.time()) + 600
    s_res = sign_resource if sign_resource is not None else resource
    s_salt = sign_salt if sign_salt is not None else salt
    rhash = router_binding.resource_hash(s_res, s_salt)
    nonce = router_binding.compute_router_nonce(sign_seller, sign_fee, rhash)
    domain = {"name": "Test EURC", "version": "1", "chainId": CHAIN_ID, "verifyingContract": ASSET}
    msg = {"from": acct.address, "to": Web3.to_checksum_address(ROUTER), "value": value,
           "validAfter": 0, "validBefore": vb, "nonce": nonce}
    signed = Account.sign_message(encode_typed_data(domain, router_binding.RECEIVE_AUTH_TYPES, msg), key)
    sig = signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([signed.v])
    return PaymentAuthorization(from_address=acct.address, to_address=Web3.to_checksum_address(ROUTER),
                               value_units=value, valid_after=0, valid_before=vb, nonce="0x" + nonce.hex(),
                               signature="0x" + sig.hex(), asset=PaymentAsset.TEURC, network="whitechain-testnet",
                               mode="atomic", salt="0x" + salt.hex())


def _validate(auth, *, expected_resource=RES_A, v=None):
    v = v or _atomic_validator()
    return v.validate(auth, expected_amount_units=PRICE, expected_pay_to=ROUTER,
                      expected_network="whitechain-testnet", expected_resource=expected_resource)


# ---- valid atomic authorization passes ----
def test_valid_atomic_authorization_passes():
    assert _validate(_sign_atomic()).ok is True


# ---- Service A → Service B reject ----
def test_service_a_authorization_rejected_for_service_b():
    auth = _sign_atomic(resource=RES_A)                     # signed & sent for A
    r = _validate(auth, expected_resource=RES_B)            # server serves B
    assert r.ok is False and "binding" in r.reason.lower()


# ---- wrong resource hash reject (client sends a salt different from what it signed) ----
def test_wrong_resource_hash_rejected():
    honest_salt = os.urandom(32)
    other_salt = os.urandom(32)
    auth = _sign_atomic(salt=other_salt, sign_salt=honest_salt)  # signs with honest_salt, sends other_salt
    r = _validate(auth)
    assert r.ok is False and "binding" in r.reason.lower()


# ---- wrong seller reject ----
def test_wrong_seller_rejected():
    attacker = "0x3333333333333333333333333333333333333333"
    auth = _sign_atomic(sign_seller=attacker)              # baked attacker seller into the nonce
    r = _validate(auth)                                    # server re-derives with honest SELLER
    assert r.ok is False and "binding" in r.reason.lower()


# ---- wrong feeBps reject ----
def test_wrong_fee_bps_rejected():
    auth = _sign_atomic(sign_fee=100)                      # signed with feeBps=100
    r = _validate(auth)                                    # server fee_bps=50
    assert r.ok is False and "binding" in r.reason.lower()


# ---- wrong derived nonce reject ----
def test_wrong_derived_nonce_rejected():
    auth = _sign_atomic()
    auth.nonce = "0x" + os.urandom(32).hex()               # tamper the nonce
    r = _validate(auth)
    assert r.ok is False and "binding" in r.reason.lower()


# ---- mode consistency ----
def test_atomic_auth_rejected_by_legacy_server():
    legacy = UnifiedPaymentValidator(chain_id=CHAIN_ID, asset_address=ASSET)  # legacy
    r = legacy.validate(_sign_atomic(), expected_amount_units=PRICE, expected_pay_to=ROUTER)
    assert r.ok is False and "atomic" in r.reason.lower()


def test_legacy_auth_rejected_by_atomic_server():
    legacy_auth = PaymentAuthorization(from_address="0xa", to_address=ROUTER, value_units=PRICE,
                                       valid_after=0, valid_before=int(time.time()) + 600,
                                       nonce="0x" + "00" * 32, signature="0x" + "11" * 65)  # mode defaults legacy
    assert _validate(legacy_auth).ok is False


# ---- x402 payload round-trips the atomic binding (envelope unchanged) ----
def test_x402_payload_round_trips_salt_and_mode():
    auth = _sign_atomic()
    header = StandardX402Adapter().encode_x_payment_header(auth)
    back = StandardX402Adapter().parse_x_payment_header(header)
    assert back.mode == "atomic" and back.salt == auth.salt and back.nonce == auth.nonce
    # a legacy auth carries neither key
    legacy = PaymentAuthorization(from_address="0xa", to_address="0xb", value_units=1, valid_after=0,
                                  valid_before=1, nonce="0x00", signature="0x11")
    lb = StandardX402Adapter().parse_x_payment_header(StandardX402Adapter().encode_x_payment_header(legacy))
    assert lb.mode == "legacy" and lb.salt is None
