"""Phase 5 STEP 3/4 — failure-path tests for the unified pipeline.

Every rejection path asserts BOTH the response AND that settlement was not called
where it must not be. Chain-free: real EIP-712 signatures verified off-chain, a
spy SettlementEngine, and a configurable spy TrustGate.
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

from unified.adapters import StandardX402Adapter  # noqa: E402
from unified.adapters.payment_validator import TRANSFER_AUTH_TYPES, UnifiedPaymentValidator  # noqa: E402
from unified.models import KYAStatus, PaymentAsset, Provider, Service  # noqa: E402
from unified.payment import PaymentAuthorization  # noqa: E402
from unified.pipeline import MarketplaceSelector, UnifiedResourceServer  # noqa: E402
from unified.settlement import SettlementResult, SettlementStatus  # noqa: E402
from unified.trust_gate import TrustDecision  # noqa: E402

CHAIN_ID = 2625
ASSET = "0x6aadCEc9E885BeeeB1B01924174a4Bb261caA579"
PAY_TO = "0x000000000000000000000000000000000000dEaD"
PRICE = 20_000
RESOURCE = "/weather"
KEY = "0x" + "1" * 64


class SpySettlement:
    def __init__(self, result=None):
        self.called = False
        self._result = result or SettlementResult(SettlementStatus.CONFIRMED, PaymentAsset.TEURC, PRICE,
                                                  net_units=19_900, fee_units=100, relay_tx_hash="0xabc")

    @property
    def supported_asset(self):
        return PaymentAsset.TEURC

    def settle(self, authorization):
        self.called = True
        return self._result


class SpyGate:
    def __init__(self, decision: TrustDecision):
        self._d = decision

    def evaluate(self, provider, service, *, requested_amount_units, min_reputation_tier=0):
        return self._d


ALLOW = TrustDecision(allowed=True, reason="ok", kya_status=KYAStatus.VERIFIED, reputation_tier=1, policy_decision="allow")


def _service():
    return Service(name="weather", category="weather", price_units=PRICE, currency="tEURC",
                   network="whitechain-testnet", provider=Provider(provider_id="0xagent", pay_to=PAY_TO))


def _server(*, settlement=None, gate=None, service=None):
    return UnifiedResourceServer(
        adapter=StandardX402Adapter(),
        trust_gate=gate or SpyGate(ALLOW),
        settlement=settlement or SpySettlement(),
        payment_validator=UnifiedPaymentValidator(chain_id=CHAIN_ID, asset_address=ASSET),
        service=service or _service(), resource=RESOURCE, asset_address=ASSET, min_reputation_tier=0,
    )


def _header(*, to=PAY_TO, value=PRICE, valid_after=0, valid_before=None, asset=PaymentAsset.TEURC,
            network="whitechain-testnet", corrupt=False, key=KEY):
    acct = Account.from_key(key)
    vb = valid_before if valid_before is not None else int(time.time()) + 600
    nonce = "0x" + os.urandom(32).hex()
    domain = {"name": "Test EURC", "version": "1", "chainId": CHAIN_ID, "verifyingContract": ASSET}
    msg = {"from": acct.address, "to": Web3.to_checksum_address(to), "value": int(value),
           "validAfter": valid_after, "validBefore": vb, "nonce": bytes.fromhex(nonce[2:])}
    signed = Account.sign_message(encode_typed_data(domain, TRANSFER_AUTH_TYPES, msg), key)
    sig = signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([signed.v])
    if corrupt:
        sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    auth = PaymentAuthorization(from_address=acct.address, to_address=msg["to"], value_units=int(value),
                                valid_after=valid_after, valid_before=vb, nonce=nonce,
                                signature="0x" + sig.hex(), asset=asset, network=network)
    return StandardX402Adapter().encode_x_payment_header(auth)


# ---- 1. malformed X-PAYMENT ----
def test_malformed_header():
    spy = SpySettlement()
    status, _ = _server(settlement=spy).fulfill("@@not-base64@@")
    assert status == 400 and spy.called is False


# ---- 2. invalid signature ----
def test_invalid_signature():
    spy = SpySettlement()
    status, body = _server(settlement=spy).fulfill(_header(corrupt=True))
    assert status == 402 and spy.called is False
    assert "signature" in body["error"].lower()


# ---- 3. expired ----
def test_expired_authorization():
    spy = SpySettlement()
    status, _ = _server(settlement=spy).fulfill(_header(valid_before=1))  # long past
    assert status == 402 and spy.called is False


# ---- 4. future ----
def test_future_authorization():
    spy = SpySettlement()
    future = int(time.time()) + 10_000
    status, _ = _server(settlement=spy).fulfill(_header(valid_after=future, valid_before=future + 600))
    assert status == 402 and spy.called is False


# ---- 5. wrong amount ----
def test_wrong_amount():
    spy = SpySettlement()
    status, body = _server(settlement=spy).fulfill(_header(value=1))
    assert status == 402 and spy.called is False
    assert "amount" in body["error"].lower()


# ---- 6. wrong payTo ----
def test_wrong_pay_to():
    spy = SpySettlement()
    status, body = _server(settlement=spy).fulfill(_header(to="0x0000000000000000000000000000000000001234"))
    assert status == 402 and spy.called is False
    assert "payto" in body["error"].lower().replace(" ", "")


# ---- 7. wrong asset (validator-level, header can't carry a non-canonical asset) ----
def test_wrong_asset_rejected_by_validator():
    v = UnifiedPaymentValidator(chain_id=CHAIN_ID, asset_address=ASSET, expected_asset=PaymentAsset.TEURC)
    apusd_auth = PaymentAuthorization(from_address="0xa", to_address=PAY_TO, value_units=PRICE,
                                      valid_after=0, valid_before=int(time.time()) + 600, nonce="0x00",
                                      signature="0x" + "11" * 65, asset=PaymentAsset.APUSD)
    assert v.validate(apusd_auth, expected_amount_units=PRICE, expected_pay_to=PAY_TO).reason == "asset mismatch"


# ---- 8. wrong network ----
def test_wrong_network():
    spy = SpySettlement()
    status, body = _server(settlement=spy).fulfill(_header(network="ethereum-mainnet"))
    assert status == 402 and spy.called is False
    assert "network" in body["error"].lower()


# ---- 9/10. unknown service / provider (buyer side) ----
def test_unknown_service_or_provider_selects_nothing():
    assert MarketplaceSelector().select([], "weather") is None                    # nothing discovered
    svc = _service()
    assert MarketplaceSelector().select([svc], "weather", budget_units=1) is None  # all over budget


# ---- 11/12/13. trust denials (KYA / tier / policy) ----
@pytest.mark.parametrize("decision", [
    TrustDecision(allowed=False, reason="not KYA-verified", kya_status=KYAStatus.UNKNOWN, policy_decision="deny:not-kya"),
    TrustDecision(allowed=False, reason="insufficient reputation", kya_status=KYAStatus.VERIFIED, policy_decision="deny:below-tier"),
    TrustDecision(allowed=False, reason="policy denied", kya_status=KYAStatus.VERIFIED, policy_decision="deny:other"),
])
def test_trust_denials_never_settle(decision):
    spy = SpySettlement()
    status, body = _server(settlement=spy, gate=SpyGate(decision)).fulfill(_header())
    assert status == 402 and spy.called is False
    assert body["policy_decision"] == decision.policy_decision


# ---- 14. settlement failure (settle IS called; money did not move) ----
def test_settlement_failure_called_but_not_ok():
    spy = SpySettlement(SettlementResult(SettlementStatus.FAILED, PaymentAsset.TEURC, PRICE, reason="relay failed"))
    status, body = _server(settlement=spy).fulfill(_header())
    assert status == 402 and spy.called is True
    assert body["settlement_status"] == "failed"


# ---- 15. funds-held (relay ok, forward failed) ----
def test_funds_held_reported():
    spy = SpySettlement(SettlementResult(SettlementStatus.FUNDS_HELD, PaymentAsset.TEURC, PRICE,
                                         relay_tx_hash="0xr", reason="funds held"))
    status, body = _server(settlement=spy).fulfill(_header())
    assert status == 402 and spy.called is True
    assert body["settlement_status"] == "funds_held"


# ---- 16. duplicate nonce / replay (validator off-chain pre-check via token) ----
def test_duplicate_nonce_pre_checked():
    class _UsedToken:
        class functions:
            @staticmethod
            def authorizationState(addr, nonce):
                class _C:
                    def call(self_inner):
                        return True   # nonce already used
                return _C()

    v = UnifiedPaymentValidator(chain_id=CHAIN_ID, asset_address=ASSET, token=_UsedToken())
    server = UnifiedResourceServer(adapter=StandardX402Adapter(), trust_gate=SpyGate(ALLOW),
                                   settlement=SpySettlement(), payment_validator=v, service=_service(),
                                   resource=RESOURCE, asset_address=ASSET)
    spy = server.settlement
    status, body = server.fulfill(_header())
    assert status == 402 and spy.called is False
    assert "replay" in body["error"].lower()


# ---- 17. registry tampering (anti-forge invariant) ----
def test_registry_tampering_rejected():
    from unified.registry import QualityMetrics, UnifiedRegistry, sign_listing
    prov = Account.create()
    listing = {"id": prov.address, "capability_type": "weather", "provider_url": "http://p",
               "pay_to": PAY_TO, "price_wei": PRICE, "min_reputation_tier": 0}
    sig = sign_listing(listing, prov.key.hex())
    tampered = {**listing, "price_wei": 1}   # change a signed field, keep old signature
    with pytest.raises(ValueError):
        UnifiedRegistry().register(tampered, sig, QualityMetrics())
