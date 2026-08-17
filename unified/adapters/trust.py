"""FacilitatorTrustGate — wraps facilitator.identity + facilitator.policy.

Implements the canonical `TrustGate` by DELEGATING to the existing, tested
components:
  - `facilitator.identity.IdentityReader.get_agent_identity(addr)` — the one place
    that reads WB Soul (KYA) + reputation;
  - `facilitator.policy.check_policy(identity, resource)` — the allow/deny logic.

No logic is reimplemented here: the adapter maps the canonical inputs onto those
calls and maps their outputs onto a `TrustDecision`. So the trust behaviour (and
its tests) are unchanged; this just exposes it behind the unified interface.

The gate evaluates the on-chain identity of `provider.provider_id`. In the live
GitHub path the KYA check is on the payer — so when using this gate for the payer,
pass a Provider whose `provider_id` is the payer address. The adapter is agnostic:
it gates whatever address it is given.
"""

from __future__ import annotations

from unified.models import KYAStatus, Provider, Service
from unified.trust_gate import TrustDecision


def _classify(reason: str) -> str:
    """Map a policy reason string to a stable policy_decision code."""
    r = reason.lower()
    if "not kya-verified" in r or "не має верифікованого" in r:
        return "deny:not-kya"
    if "kya not active" in r or "не активний" in r:
        return "deny:kya-inactive"
    if "insufficient reputation" in r or "недостатня репутація" in r:
        return "deny:below-tier"
    if "spend limit" in r or "ліміт витрат" in r:
        return "deny:spend-limit"
    return "deny:other"


class FacilitatorTrustGate:
    """Canonical TrustGate backed by the GitHub facilitator's identity+policy.

    :param identity_reader: a `facilitator.identity.IdentityReader` (or anything
        exposing `get_agent_identity(address) -> dict`).
    :param policy_check: the allow/deny function; defaults to
        `facilitator.policy.check_policy`. Injectable for testing.
    """

    def __init__(self, identity_reader, policy_check=None):
        self._identity = identity_reader
        if policy_check is None:
            from facilitator import policy as _policy  # lazy: keeps import light
            policy_check = _policy.check_policy
        self._policy_check = policy_check

    def evaluate(
        self,
        provider: Provider,
        service: Service,
        *,
        requested_amount_units: int,
        min_reputation_tier: int = 0,
    ) -> TrustDecision:
        identity = self._identity.get_agent_identity(provider.provider_id)
        decision = self._policy_check(
            identity,
            {"min_reputation_tier": int(min_reputation_tier), "price_wei": int(requested_amount_units)},
        )

        if not identity.get("has_soul"):
            kya = KYAStatus.UNKNOWN
        elif not identity.get("verified"):
            kya = KYAStatus.UNVERIFIED
        else:
            kya = KYAStatus.VERIFIED

        allowed = bool(decision["allow"])
        return TrustDecision(
            allowed=allowed,
            reason=decision["reason"],
            kya_status=kya,
            reputation_tier=identity.get("reputation_tier"),
            policy_decision="allow" if allowed else _classify(decision["reason"]),
            details={"soul_id": identity.get("soul_id"), "sbt_count": identity.get("sbt_count")},
        )
