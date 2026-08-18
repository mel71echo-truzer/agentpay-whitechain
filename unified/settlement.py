"""SettlementEngine — abstraction that hides HOW a payment settles on-chain.

The buyer agent (and the marketplace layer) must not know whether settlement is a
plain facilitator relay, an atomic router split, which token, or how gas is paid.
They hand a validated `PaymentAuthorization` to a `SettlementEngine` and get back
a `SettlementResult`. This is the seam that lets the GitHub layer's real
implementations (facilitator/settlement.py legacy relay and
facilitator/atomic_settlement.py router) sit behind one interface, selectable by
config — exactly as `SETTLEMENT_MODE` already does internally today.

Phase 1 defines the interface only. Wiring the existing engines behind it (an
adapter, not a rewrite — "не переписуй facilitator") is Phase 2.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from unified.models import PaymentAsset
from unified.payment import PaymentAuthorization


class SettlementStatus(enum.Enum):
    """Outcome of a settlement attempt."""

    CONFIRMED = "confirmed"      # relay mined and, if applicable, funds fully split/forwarded
    SUBMITTED = "submitted"      # broadcast, not yet confirmed (fast path)
    FUNDS_HELD = "funds_held"    # legacy partial failure: received but not forwarded (reconcilable)
    FAILED = "failed"            # nothing moved


@dataclass
class SettlementResult:
    """Result of settling one payment. Money is integer minimal units."""

    status: SettlementStatus
    asset: PaymentAsset
    amount_units: int                    # total paid by the buyer
    fee_units: int = 0                   # facilitator/treasury fee
    net_units: int = 0                   # to the seller (amount - fee)
    relay_tx_hash: Optional[str] = None
    forward_tx_hash: Optional[str] = None  # legacy second tx, if any
    reason: str = ""                     # buyer-safe explanation on non-CONFIRMED
    details: dict = field(default_factory=dict)  # structured context (never secrets)

    @property
    def ok(self) -> bool:
        """Money moved or is in flight (not FAILED/FUNDS_HELD). NOT a release signal —
        use `is_confirmed` to decide whether to hand over the resource (M-2)."""
        return self.status in (SettlementStatus.CONFIRMED, SettlementStatus.SUBMITTED)

    @property
    def is_confirmed(self) -> bool:
        """M-2: the ONLY state in which the resource may be released — the relay is
        mined and, in atomic mode, the split is complete. SUBMITTED (broadcast, not
        yet mined) is NOT confirmed and must not release the resource."""
        return self.status is SettlementStatus.CONFIRMED


@runtime_checkable
class SettlementEngine(Protocol):
    """Settles an authorized payment on-chain, hiding facilitator/router/token
    details from the caller.

    Contract:
      - `settle` performs the on-chain move (relay and, in atomic mode, split) and
        returns a `SettlementResult`; it never raises for an on-chain rejection —
        it returns FAILED/FUNDS_HELD with a buyer-safe `reason` (internal RPC/revert
        detail goes to logs only, matching the existing error-sanitization rule).
      - `supported_asset` reports which asset this engine settles (canonical tEURC).
    """

    @property
    def supported_asset(self) -> PaymentAsset:
        """The settlement asset this engine handles (tEURC in production)."""
        ...

    def settle(self, authorization: PaymentAuthorization) -> SettlementResult:
        """Settle the authorized payment and return the result."""
        ...
