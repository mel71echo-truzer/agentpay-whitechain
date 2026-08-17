"""Phase 2 — integration proof: FacilitatorTrustGate against the REAL identity
reader (WB Soul mock on a local eth-tester chain, via the existing conftest
fixture). Proves the adapter wraps the actual on-chain KYA/reputation path, not a
stub — without changing any existing component.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified.adapters.trust import FacilitatorTrustGate  # noqa: E402
from unified.models import KYAStatus, Provider, Service  # noqa: E402


def _gate(fx):
    # Wrap the real IdentityReader the facilitator already built (reads WB Soul).
    return FacilitatorTrustGate(fx.facilitator.identity)


def _provider(addr):
    return Provider(provider_id=addr), Service(name="svc", category="image-generation")


def test_gate_allows_verified_agent_with_sbt(facilitator_setup):
    fx = facilitator_setup
    gate = _gate(fx)
    prov, svc = _provider(fx.verified_agent.address)  # Soul + IsVerified + 1 SBT (tier 1)
    d = gate.evaluate(prov, svc, requested_amount_units=fx.premium_price_wei, min_reputation_tier=1)
    assert d.allowed is True
    assert d.kya_status is KYAStatus.VERIFIED
    assert d.reputation_tier >= 1


def test_gate_denies_unverified_agent(facilitator_setup):
    fx = facilitator_setup
    gate = _gate(fx)
    prov, svc = _provider(fx.unverified_agent.address)  # no WB Soul at all
    d = gate.evaluate(prov, svc, requested_amount_units=fx.price_wei)
    assert d.allowed is False
    assert d.kya_status is KYAStatus.UNKNOWN
    assert d.policy_decision == "deny:not-kya"


def test_gate_denies_verified_but_below_premium_tier(facilitator_setup):
    fx = facilitator_setup
    gate = _gate(fx)
    # Verified, no SBT, no behavioral history -> tier 0; premium needs tier 1.
    prov, svc = _provider(fx.verified_no_sbt_agent.address)
    d = gate.evaluate(prov, svc, requested_amount_units=fx.premium_price_wei, min_reputation_tier=1)
    assert d.allowed is False
    assert d.kya_status is KYAStatus.VERIFIED
    assert d.policy_decision == "deny:below-tier"
