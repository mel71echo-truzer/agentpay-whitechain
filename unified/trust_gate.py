"""TrustGate — the trust-layer decision interface, kept OUT of quality scoring.

The TrustGate answers a yes/no question the marketplace layer must never answer:
"is this provider allowed to be paid for this service at this amount?" — using
KYA (WB Soul), SBT reputation, and policy/allowlist. It is a hard gate: a service
can have a perfect quality score and still be denied here, and vice versa.

Phase 1 defines the interface only. The GitHub trust layer
(facilitator/identity.py + policy.py) will implement it in Phase 2; the local
marketplace layer never inspects these internals — it just calls `evaluate()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from unified.models import KYAStatus, Provider, Service


@dataclass
class TrustDecision:
    """The outcome of a TrustGate evaluation — an explicit, auditable verdict."""

    allowed: bool
    reason: str                                  # human-readable; safe to surface to the buyer
    kya_status: KYAStatus = KYAStatus.UNKNOWN
    reputation_tier: Optional[int] = None        # effective tier used for the decision
    policy_decision: str = "none"                # e.g. "allow", "deny:not-kya", "deny:below-tier", "deny:not-allowlisted"
    details: dict = field(default_factory=dict)  # optional structured context (never secrets)


@runtime_checkable
class TrustGate(Protocol):
    """Trust-layer gate. Implementations read WB Soul KYA + SBT + policy and return
    a `TrustDecision`. They MUST NOT compute or depend on marketplace quality.

    Contract:
      - `evaluate` is pure w.r.t. its inputs plus on-chain reads; no side effects
        that move money.
      - A `False` decision always carries a non-empty, buyer-safe `reason`.
      - `requested_amount_units` is integer minimal units (no float money).
    """

    def evaluate(
        self,
        provider: Provider,
        service: Service,
        *,
        requested_amount_units: int,
        min_reputation_tier: int = 0,
    ) -> TrustDecision:
        """Decide whether `provider` may be paid for `service` at
        `requested_amount_units`, requiring at least `min_reputation_tier`."""
        ...
