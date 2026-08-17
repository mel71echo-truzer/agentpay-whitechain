"""Unified pipeline — one path from marketplace intelligence to on-chain settlement.

    Agent → Discovery → Quality/Price/TaskFit → TrustGate → X-PAYMENT → SettlementEngine → Result

Two pieces:
  - `MarketplaceSelector` (buyer side): ranks discovered services by the marketplace
    score (quality/price/task-fit). Trust is NOT in this number — it is a separate
    hard gate applied later. No new trust formula is introduced.
  - `UnifiedResourceServer` (seller side): speaks standard x402 via
    `StandardX402Adapter`, enforces the `TrustGate` (KYA/SBT/policy) BEFORE any
    money moves, and settles via a `SettlementEngine`. `build_unified_resource_app`
    exposes it as a FastAPI app so the x402 flow runs over real HTTP.

This module wires the canonical interfaces + adapters from Phases 1–2 together. It
does not modify existing components, contracts, or the token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from unified.models import Provider, Service
from unified.payment import X402PaymentAdapter
from unified.scoring import ScoreBreakdown, compose_final_score
from unified.settlement import SettlementEngine
from unified.telemetry import PaymentTelemetry
from unified.trust_gate import TrustGate


# ---------------- Buyer side: marketplace selection ----------------

@dataclass
class Candidate:
    service: Service
    breakdown: ScoreBreakdown


class MarketplaceSelector:
    """Ranks services by the marketplace decision layer (quality/price/task-fit).
    Trust is applied by the TrustGate downstream, never folded in here."""

    def rank(self, services: list[Service], task_category: str, *, budget_units: Optional[int] = None) -> list[Candidate]:
        affordable = [s for s in services if budget_units is None or s.price_units <= budget_units]
        priced = [s for s in affordable if s.price_units > 0]
        if not priced:
            return []
        minimum = min(s.price_units for s in priced)
        scored = [Candidate(s, compose_final_score(s, task_category, minimum)) for s in priced]
        scored.sort(key=lambda c: c.breakdown.final_score, reverse=True)
        return scored

    def select(self, services: list[Service], task_category: str, *, budget_units: Optional[int] = None) -> Optional[Candidate]:
        ranked = self.rank(services, task_category, budget_units=budget_units)
        return ranked[0] if ranked else None


# ---------------- Seller side: standard-x402 resource server + trust gate + settlement ----------------

class UnifiedResourceServer:
    """Serves one resource behind standard x402, gated by TrustGate, settled by a
    SettlementEngine. Framework-agnostic core; `build_unified_resource_app` wraps
    it in FastAPI."""

    def __init__(
        self,
        *,
        adapter: X402PaymentAdapter,
        trust_gate: TrustGate,
        settlement: SettlementEngine,
        service: Service,
        resource: str,
        asset_address: str,
        min_reputation_tier: int = 0,
        payment_validator=None,
        on_telemetry: Optional[Callable[[PaymentTelemetry], None]] = None,
    ):
        self.adapter = adapter
        self.trust_gate = trust_gate
        self.settlement = settlement
        self.service = service
        self.resource = resource
        self.asset_address = asset_address
        self.min_reputation_tier = min_reputation_tier
        # Off-chain payment validation (amount/payTo/asset/window/signature) BEFORE
        # trust + settlement. Optional for backwards-compat, but STRONGLY recommended
        # (without it a buyer could underpay or mis-route; see payment_validator.py).
        self.payment_validator = payment_validator
        self.on_telemetry = on_telemetry

    def payment_required(self) -> dict:
        """The standard x402 402 body for this resource."""
        return self.adapter.build_payment_required(
            resource=self.resource,
            amount_units=self.service.price_units,
            pay_to=self.service.provider.pay_to,
            asset=self.service.payment_asset,
            asset_address=self.asset_address,
            network=self.service.network,
            description=self.service.description,
        )

    def _emit(self, tel: PaymentTelemetry) -> None:
        if self.on_telemetry is not None:
            self.on_telemetry(tel)

    def fulfill(self, x_payment_header: str) -> tuple[int, dict]:
        """Run the seller-side pipeline for a paid request:
        parse X-PAYMENT → payment validation → TrustGate → SettlementEngine → result.
        Returns (http_status, body). Never raises on untrusted input. Settlement is
        NEVER reached if any earlier stage rejects."""
        tel = PaymentTelemetry(service_id=self.service.name, provider_id=self.service.provider.provider_id,
                               asset=self.service.payment_asset.value, payment_amount=self.service.price_units,
                               settlement_status="not_attempted")

        # 0. parse the standard x402 header.
        try:
            auth = self.adapter.parse_x_payment_header(x_payment_header)
        except ValueError:
            tel.failure_reason = "malformed X-PAYMENT header"
            self._emit(tel)
            return 400, {"error": "Malformed X-PAYMENT header."}

        # 1. PAYMENT VALIDATION (amount / payTo / asset / window / signature) —
        #    off-chain, BEFORE trust + settlement. No money or gas moves on failure.
        if self.payment_validator is not None:
            vr = self.payment_validator.validate(
                auth,
                expected_amount_units=self.service.price_units,
                expected_pay_to=self.service.provider.pay_to,
                expected_network=self.service.network,
            )
            if not vr.ok:
                tel.failure_reason = f"payment invalid: {vr.reason}"
                self._emit(tel)
                return 402, {"error": vr.reason, "stage": "payment-validation"}

        # 2. TRUST GATE — separate, hard, before any money moves. Gates the PAYER's
        #    on-chain identity (KYA/SBT/policy). Not mixed with marketplace score.
        payer = Provider(provider_id=auth.from_address)
        decision = self.trust_gate.evaluate(
            payer, self.service,
            requested_amount_units=self.service.price_units,
            min_reputation_tier=self.min_reputation_tier,
        )
        tel.trust_decision = decision.policy_decision
        if not decision.allowed:
            tel.failure_reason = decision.reason
            self._emit(tel)
            return 402, {
                "error": decision.reason,
                "policy_decision": decision.policy_decision,
                "kya_status": decision.kya_status.value,
            }

        # 3. SETTLEMENT — hidden behind the engine (facilitator/router/token/atomic).
        result = self.settlement.settle(auth)
        tel.settlement_status = result.status.value
        tel.tx_hash = result.relay_tx_hash
        if not result.ok:
            tel.failure_reason = result.reason
            self._emit(tel)
            return 402, {"error": result.reason, "settlement_status": result.status.value}

        self._emit(tel)
        return 200, {
            "result": "SERVICE RESULT",
            "reputation_tier": decision.reputation_tier,
            "settlement": {
                "status": result.status.value,
                "asset": result.asset.value,
                "amount_units": result.amount_units,
                "fee_units": result.fee_units,
                "net_units": result.net_units,
                "relay_tx_hash": result.relay_tx_hash,
            },
        }


def build_unified_resource_app(server: UnifiedResourceServer):
    """Expose a UnifiedResourceServer as a FastAPI app speaking standard x402:
    GET <resource> with no X-PAYMENT → 402 + requirements; with X-PAYMENT → the
    trust+settlement pipeline."""
    from fastapi import FastAPI, Header
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Unified AgentPay resource server")

    @app.get(server.resource)
    def handle(x_payment: str | None = Header(default=None, alias="X-PAYMENT")):
        if not x_payment:
            return JSONResponse(status_code=402, content=server.payment_required())
        status, body = server.fulfill(x_payment)
        return JSONResponse(status_code=status, content=body)

    return app
