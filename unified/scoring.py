"""Canonical scoring — the marketplace decision layer, with Trust kept separate.

Product rule (do not violate): **Quality Score and Trust Score are different
things and must not be mixed.** Quality/Price/Task-Fit answers "is this a good,
well-priced, relevant service?"; Trust answers "is this provider an accountable,
KYA-verified, reputable counterparty?". A high rating never launders a missing
KYA, and a strong reputation never inflates a slow service's quality.

The existing, testnet-proven local behaviour is preserved EXACTLY:

    quality    = 0.5 * rating/5 + 0.3 * success_rate + 0.2 * latency_score
                 where latency_score = 1 / (1 + latency_ms / 500)
    price_score = minimum_price / price
    final       = 0.5 * quality + 0.3 * price_score + 0.2 * task_fit

`trust_score` is NEW and lives OUTSIDE that formula. `compose_final_score` shows
how Trust plugs in as a gate (default — no change to the ranking number) or, later
and experimentally, as a multiplier — WITHOUT rewriting the marketplace engine.
The weights for any Quality×Price×TaskFit×Trust model are deliberately left to be
derived experimentally (Phase 3+), not invented here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from unified.models import KYAStatus, Provider, Service

# --- marketplace weights (unchanged from the local implementation) ---
_Q_RATING_W, _Q_SUCCESS_W, _Q_LATENCY_W = 0.50, 0.30, 0.20
_F_QUALITY_W, _F_PRICE_W, _F_TASKFIT_W = 0.50, 0.30, 0.20
_LATENCY_HALF_MS = 500.0  # latency at which latency_score == 0.5


def quality_score(service: Service) -> float:
    """Marketplace QUALITY only — rating, success rate, latency. No trust here.
    Identical math to the local buyer_agent.calculate_quality_score."""
    rating_score = float(service.rating) / 5.0
    success = float(service.success_rate)
    latency_score = 1.0 / (1.0 + float(service.latency_ms) / _LATENCY_HALF_MS)
    return _Q_RATING_W * rating_score + _Q_SUCCESS_W * success + _Q_LATENCY_W * latency_score


def price_score(price_units: int, minimum_price_units: int) -> float:
    """Cheapest candidate scores 1.0; others scale down. (minimum/price.)"""
    price = int(price_units)
    if price <= 0:
        return 0.0
    return float(minimum_price_units) / float(price)


def task_fit(service: Service, task_category: str) -> float:
    """How well the service matches the requested task. Local behaviour: base 0.5,
    +0.3 if the task is in the category, +0.2 if in the description; capped at 1.0."""
    description = (service.description or "").lower()
    category = (service.category or "").lower()
    task = (task_category or "").lower()
    score = 0.5
    if task and task in category:
        score += 0.3
    if task and task in description:
        score += 0.2
    return min(score, 1.0)


def trust_score(provider: Optional[Provider]) -> float:
    """TRUST only — a 0..1 signal from the on-chain trust layer (KYA + SBT tier +
    signature binding). Deliberately SEPARATE from quality. This is a reference
    normalization for ranking/telemetry; the hard allow/deny stays in TrustGate.

    A provider with no verified KYA scores 0 (the gate will also deny). Otherwise
    the SBT/effective tier maps onto (0..1). Tiers are the current 0/1/2 model; the
    divisor is expressed as MAX_TIER so it survives a tier-scheme change.
    """
    if provider is None:
        return 0.0
    if provider.kya_status is not KYAStatus.VERIFIED or not provider.signature_is_bound():
        return 0.0
    MAX_TIER = 2  # current reputation tier ceiling (0..2)
    tier = provider.trust_tier
    if tier is None:
        tier = provider.sbt_reputation_tier or 0
    return min(max(tier, 0), MAX_TIER) / MAX_TIER


def marketplace_final_score(service: Service, task_category: str, minimum_price_units: int) -> float:
    """The existing local final score — quality/price/task-fit only, unchanged.
    Trust is NOT part of this number (that is the whole point)."""
    q = quality_score(service)
    p = price_score(service.price_units, minimum_price_units)
    t = task_fit(service, task_category)
    return _F_QUALITY_W * q + _F_PRICE_W * p + _F_TASKFIT_W * t


class TrustCompositionStrategy(enum.Enum):
    """How Trust combines with the marketplace score at ranking time."""

    MARKETPLACE_ONLY = "marketplace-only"   # DEFAULT: ranking = marketplace score; Trust enforced by TrustGate (hard allow/deny)
    TRUST_MULTIPLIER = "trust-multiplier"    # EXPERIMENTAL: ranking = marketplace * (a soft trust factor); weights TBD


@dataclass
class ScoreBreakdown:
    """Transparent, auditable breakdown — every sub-score kept distinct so a human
    can see WHY a provider was chosen (and Quality is never conflated with Trust)."""

    quality_score: float
    price_score: float
    task_fit: float
    trust_score: float
    marketplace_final: float
    final_score: float
    strategy: TrustCompositionStrategy


def compose_final_score(
    service: Service,
    task_category: str,
    minimum_price_units: int,
    *,
    strategy: TrustCompositionStrategy = TrustCompositionStrategy.MARKETPLACE_ONLY,
    trust_weight: float = 0.0,
) -> ScoreBreakdown:
    """Produce the full, separated score breakdown.

    - MARKETPLACE_ONLY (default): `final_score == marketplace_final`. Trust does
      NOT move the ranking number; the TrustGate decides allow/deny separately.
      This preserves the exact local behaviour that already ran real testnet
      payments.
    - TRUST_MULTIPLIER (experimental): `final = marketplace * (1 - w + w*trust)`,
      a soft factor with `w = trust_weight`. Provided as a plug point so Trust can
      later influence ranking WITHOUT rewriting the marketplace engine; the real
      weight must be derived experimentally, not hard-coded.
    """
    q = quality_score(service)
    p = price_score(service.price_units, minimum_price_units)
    tf = task_fit(service, task_category)
    ts = trust_score(service.provider)
    marketplace = _F_QUALITY_W * q + _F_PRICE_W * p + _F_TASKFIT_W * tf

    if strategy is TrustCompositionStrategy.TRUST_MULTIPLIER:
        w = max(0.0, min(1.0, trust_weight))
        final = marketplace * (1.0 - w + w * ts)
    else:
        final = marketplace  # Trust handled by the gate, not the ranking number

    return ScoreBreakdown(
        quality_score=q,
        price_score=p,
        task_fit=tf,
        trust_score=ts,
        marketplace_final=marketplace,
        final_score=final,
        strategy=strategy,
    )
