"""unified/ — canonical AgentPay interfaces (Phase 1: design, not integration).

This package defines the *canonical* models and interfaces that unify two
existing, working AgentPay layers WITHOUT changing either yet:

  - the local **marketplace / decision layer** (service discovery + multi-factor
    scoring: quality, price, task-fit, latency) — branch `local-agentpay-import`;
  - the GitHub **trust / on-chain layer** (WB Soul KYA + SBT reputation + atomic
    tEURC settlement) — the trust layer this branch is based on.

Nothing here imports web3, fastapi, or either concrete implementation. These are
pure Protocols / dataclasses / pure functions — the architectural skeleton that
both layers will later implement (Phase 2+). The design rule the product owner
set is enforced structurally here:

  * **Quality Score and Trust Score are separate.** Quality/Price/Task-Fit is the
    *marketplace decision layer*; KYA/SBT is the *trust layer*. The existing local
    scoring behaviour is preserved exactly; Trust is composable on top as a gate
    (default) or an experimental multiplier — never mixed into quality.
  * Public payment protocol = standard x402 `X-PAYMENT` header.
  * Canonical settlement asset = tEURC (production); apUSD kept as a dev/compat asset.
"""

from unified.models import (
    FieldSource,
    KYAStatus,
    PaymentAsset,
    Provider,
    Service,
)
from unified.scoring import (
    ScoreBreakdown,
    compose_final_score,
    marketplace_final_score,
    price_score,
    quality_score,
    task_fit,
    trust_score,
)
from unified.trust_gate import TrustDecision, TrustGate
from unified.payment import PaymentAuthorization, X402PaymentAdapter
from unified.settlement import SettlementEngine, SettlementResult, SettlementStatus

__all__ = [
    "FieldSource",
    "KYAStatus",
    "PaymentAsset",
    "Provider",
    "Service",
    "ScoreBreakdown",
    "quality_score",
    "price_score",
    "task_fit",
    "trust_score",
    "marketplace_final_score",
    "compose_final_score",
    "TrustGate",
    "TrustDecision",
    "X402PaymentAdapter",
    "PaymentAuthorization",
    "SettlementEngine",
    "SettlementResult",
    "SettlementStatus",
]
