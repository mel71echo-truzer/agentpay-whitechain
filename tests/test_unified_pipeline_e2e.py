"""Phase 3 — the unified E2E, over real HTTP, on a real (eth-tester) chain.

Proves the WHOLE pipeline runs as one:

    Agent → Discovery → Quality/Price/TaskFit → TrustGate → X-PAYMENT → SettlementEngine → Result

Real components: the GitHub facilitator's identity/policy (TrustGate) and legacy
settlement engine (SettlementEngine) from the shared conftest fixture, the unified
registry + marketplace selector, and the standard x402 `X-PAYMENT` wire format via
StandardX402Adapter over a FastAPI TestClient. Nothing existing is modified.
"""

import os
import sys
from pathlib import Path

from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from web3 import Web3  # noqa: E402

import agent_client  # noqa: E402
from unified.adapters import StandardX402Adapter  # noqa: E402
from unified.adapters.settlement import FacilitatorSettlementEngine  # noqa: E402
from unified.adapters.trust import FacilitatorTrustGate  # noqa: E402
from unified.payment import PaymentAuthorization  # noqa: E402
from unified.pipeline import MarketplaceSelector, UnifiedResourceServer, build_unified_resource_app  # noqa: E402
from unified.registry import QualityMetrics, UnifiedRegistry, sign_listing  # noqa: E402

RESOURCE = "/weather"


def _sign_x_payment(fx, agent, resource, value_units, pay_to) -> str:
    """Sign an EIP-3009 (legacy, to=pay_to) authorization using the CHAIN clock
    (eth-tester drifts vs wall-clock), then encode it as a standard X-PAYMENT
    header — the exact wire format the buyer would send."""
    chain_ts = fx.w3.eth.get_block("latest")["timestamp"]
    salt = os.urandom(32)
    nonce = Web3.keccak(resource.encode("utf-8") + salt)
    message = {
        "from": agent.address, "to": Web3.to_checksum_address(pay_to), "value": int(value_units),
        "validAfter": 0, "validBefore": chain_ts + 3600, "nonce": nonce,
    }
    domain = {"name": "Test EURC", "version": "1", "chainId": fx.w3.eth.chain_id,
              "verifyingContract": fx.teurc.address}
    signed = Account.sign_message(
        encode_typed_data(domain, agent_client.TRANSFER_AUTH_TYPES, message), agent.key.hex()
    )
    sig = signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([signed.v])
    auth = PaymentAuthorization(
        from_address=message["from"], to_address=message["to"], value_units=message["value"],
        valid_after=0, valid_before=message["validBefore"], nonce="0x" + nonce.hex(),
        signature="0x" + sig.hex(), network="whitechain-testnet",
    )
    return StandardX402Adapter().encode_x_payment_header(auth)


def _registry_with_two_weather_services(fx) -> UnifiedRegistry:
    """Register two signed weather services (a cheaper good one and a pricier
    premium one) so the marketplace selector has a real economic choice."""
    reg = UnifiedRegistry()
    sp_key = fx.service_provider_acct.key.hex()
    pay_to = fx.facilitator_acct.address  # legacy: buyer pays the facilitator
    base = {"id": fx.service_provider_acct.address, "capability_type": "weather",
            "provider_url": "http://sp:8000", "pay_to": pay_to, "min_reputation_tier": 0}
    cheap = {**base, "price_wei": fx.price_wei}
    premium = {**base, "price_wei": fx.price_wei * 3}
    reg.register(cheap, sign_listing(cheap, sp_key), QualityMetrics(4.2, 0.96, 180))
    reg.register(premium, sign_listing(premium, sp_key), QualityMetrics(4.9, 0.998, 90))
    return reg


def _server_for(fx, service) -> UnifiedResourceServer:
    return UnifiedResourceServer(
        adapter=StandardX402Adapter(),
        trust_gate=FacilitatorTrustGate(fx.facilitator.identity),
        settlement=FacilitatorSettlementEngine(fx.facilitator.settlement),
        service=service, resource=RESOURCE, asset_address=fx.teurc.address, min_reputation_tier=0,
    )


def test_unified_e2e_verified_agent_full_pipeline(facilitator_setup):
    fx = facilitator_setup

    # 1. DISCOVERY
    reg = _registry_with_two_weather_services(fx)
    services = reg.discover(category="weather", max_price_units=fx.price_wei * 5)
    assert len(services) == 2

    # 2. MARKETPLACE SCORING → SELECT (cheaper high-value wins; trust NOT in the score)
    chosen = MarketplaceSelector().select(services, "weather", budget_units=fx.price_wei * 5)
    assert chosen is not None
    assert chosen.service.price_units == fx.price_wei          # the cheaper one
    assert round(chosen.breakdown.final_score, 3) == 0.928     # exact local marketplace math
    assert chosen.breakdown.trust_score == 0.0                 # trust separate, not folded in

    # 3. resource server speaks standard x402
    client = TestClient(build_unified_resource_app(_server_for(fx, chosen.service)))
    r402 = client.get(RESOURCE)
    assert r402.status_code == 402
    accept = r402.json()["accepts"][0]
    assert accept["maxAmountRequired"] == str(chosen.service.price_units)

    # 4. X-PAYMENT (verified agent, tier 0, resource min_tier 0) → TrustGate → Settlement
    header = _sign_x_payment(fx, fx.verified_no_sbt_agent, RESOURCE, chosen.service.price_units, accept["payTo"])
    r = client.get(RESOURCE, headers={"X-PAYMENT": header})

    # 5. RESULT — real on-chain settlement happened
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "SERVICE RESULT"
    assert body["settlement"]["status"] in ("confirmed", "submitted")
    assert body["settlement"]["asset"] == "tEURC"
    assert body["settlement"]["net_units"] > 0
    assert body["settlement"]["relay_tx_hash"]


def test_unified_e2e_unverified_agent_denied_at_trust_gate(facilitator_setup):
    fx = facilitator_setup
    reg = _registry_with_two_weather_services(fx)
    chosen = MarketplaceSelector().select(reg.discover(category="weather"), "weather")
    server = _server_for(fx, chosen.service)
    client = TestClient(build_unified_resource_app(server))

    # An agent with NO WB Soul — good scoring can't buy without KYA (hard gate).
    header = _sign_x_payment(fx, fx.unverified_agent, RESOURCE, chosen.service.price_units,
                             server.service.provider.pay_to)
    r = client.get(RESOURCE, headers={"X-PAYMENT": header})
    assert r.status_code == 402
    assert r.json()["policy_decision"] == "deny:not-kya"      # denied BEFORE any money moved
