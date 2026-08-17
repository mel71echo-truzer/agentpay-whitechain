"""Phase 1 architecture-level tests for the canonical `unified` interfaces.

These assert the DESIGN, not integration: the unified Service/Provider model can
represent both a local and a GitHub provider; the scoring preserves the exact
local behaviour; Trust is separate from Quality; and the payment/settlement
seams are well-formed Protocols. No web3/fastapi, no chain — pure and fast.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unified import (  # noqa: E402
    KYAStatus,
    PaymentAsset,
    PaymentAuthorization,
    Provider,
    Service,
    SettlementEngine,
    SettlementResult,
    SettlementStatus,
    TrustGate,
    X402PaymentAdapter,
    compose_final_score,
    marketplace_final_score,
    quality_score,
    trust_score,
)
from unified.models import FieldSource, field_sources  # noqa: E402
from unified.scoring import TrustCompositionStrategy  # noqa: E402
from unified.trust_gate import TrustDecision  # noqa: E402


# ---------- 1. Service model represents both local and GitHub providers ----------

def _local_style_service() -> Service:
    """A local (marketplace) service: quality metrics, no on-chain trust."""
    return Service(
        name="Weather", description="Get weather information", url="http://127.0.0.1:8005",
        endpoint="/weather", method="GET", category="weather",
        price_units=5000, currency="apUSD", network="whitechain-testnet",
        rating=4.2, success_rate=0.96, latency_ms=180,
        provider=Provider(provider_id="0xLocalProviderNoKYA"),
    )


def _github_style_service() -> Service:
    """A GitHub (trust-layer) provider: KYA-verified, SBT tier, signature-bound."""
    prov = Provider(
        provider_id="0xTrustedProvider", signer="0xtrustedprovider", pay_to="0xRouter",
        kya_status=KYAStatus.VERIFIED, soul_id=7, sbt_reputation_tier=2, trust_tier=2,
        allowlisted=True,
    )
    return Service(
        name="PhotoBank", description="premium photo", url="http://sp:8000",
        endpoint="/photo/kyiv-lavra", method="POST", category="image-generation",
        price_units=100_000, currency="tEURC", network="whitechain-testnet",
        rating=4.9, success_rate=0.999, latency_ms=120, provider=prov,
    )


def test_model_represents_both_local_and_github_providers():
    local, gh = _local_style_service(), _github_style_service()
    # Local: no KYA, apUSD asset, quality metrics present.
    assert local.provider.kya_status is KYAStatus.UNKNOWN
    assert local.payment_asset is PaymentAsset.APUSD
    assert local.rating == 4.2
    # GitHub: KYA-verified, signature-bound, tEURC, SBT tier.
    assert gh.provider.kya_status is KYAStatus.VERIFIED
    assert gh.provider.signature_is_bound() is True
    assert gh.payment_asset is PaymentAsset.TEURC
    assert gh.provider.sbt_reputation_tier == 2


def test_field_source_classification():
    svc_sources = field_sources(Service)
    prov_sources = field_sources(Provider)
    assert svc_sources["name"] is FieldSource.PROVIDER_SUPPLIED
    assert svc_sources["rating"] is FieldSource.REGISTRY_SUPPLIED     # observed, not self-asserted
    assert prov_sources["kya_status"] is FieldSource.ONCHAIN_VERIFIED
    assert prov_sources["trust_tier"] is FieldSource.CALCULATED


# ---------- 2. Quality Score works (exact local behaviour) ----------

def test_quality_score_matches_local_numbers():
    weather = _local_style_service()
    premium = Service(name="WeatherPremium", description="Premium weather information",
                      category="weather", price_units=8000, currency="apUSD",
                      rating=4.9, success_rate=0.998, latency_ms=90)
    assert round(quality_score(weather), 3) == 0.855
    assert round(quality_score(premium), 3) == 0.959


def test_final_score_matches_local_number():
    # Among {Translator 10000, Weather 5000, WeatherPremium 8000} the min is 5000;
    # Weather at 5000 -> price_score 1.0, task_fit weather -> 1.0.
    weather = _local_style_service()
    final = marketplace_final_score(weather, task_category="weather", minimum_price_units=5000)
    assert round(final, 3) == 0.928


# ---------- 3. Trust does NOT change Quality ----------

def test_trust_does_not_affect_quality_or_marketplace_final():
    base = _local_style_service()                       # no KYA -> trust 0
    trusted = _github_style_service()
    trusted.rating, trusted.success_rate, trusted.latency_ms = 4.2, 0.96, 180  # same quality inputs as base
    trusted.category, trusted.description = "weather", "Get weather information"
    trusted.price_units = 5000

    # Quality is identical despite very different trust.
    assert quality_score(base) == quality_score(trusted)
    # Marketplace-only final is identical too (trust handled by the gate, not ranking).
    b = compose_final_score(base, "weather", 5000)
    t = compose_final_score(trusted, "weather", 5000)
    assert b.final_score == t.final_score == b.marketplace_final
    # But trust_score itself differs (separate signal).
    assert trust_score(base.provider) == 0.0
    assert trust_score(trusted.provider) == 1.0
    assert b.strategy is TrustCompositionStrategy.MARKETPLACE_ONLY


def test_trust_multiplier_is_optional_and_non_destructive():
    trusted = _github_style_service()
    market = compose_final_score(trusted, "image-generation", trusted.price_units)
    # Experimental multiplier lowers ranking for low trust, never touches quality.
    mult = compose_final_score(trusted, "image-generation", trusted.price_units,
                               strategy=TrustCompositionStrategy.TRUST_MULTIPLIER, trust_weight=0.5)
    assert mult.quality_score == market.quality_score       # quality untouched
    assert mult.final_score <= market.marketplace_final + 1e-9


# ---------- 4/5. Payment + Settlement seams are clear Protocols ----------

class _StubAdapter:
    def build_payment_required(self, *, resource, amount_units, pay_to, asset, asset_address, network, description=""):
        return {"x402Version": 1, "accepts": [{"scheme": "exact", "network": network,
                "maxAmountRequired": str(amount_units), "resource": resource, "payTo": pay_to,
                "asset": asset_address}]}

    def parse_x_payment_header(self, header_value: str) -> PaymentAuthorization:
        return PaymentAuthorization(from_address="0x0", to_address="0x0", value_units=0,
                                    valid_after=0, valid_before=0, nonce="0x00", signature="0x00")

    def encode_x_payment_header(self, authorization, *, scheme="exact") -> str:
        return "base64header"


class _StubSettlement:
    @property
    def supported_asset(self) -> PaymentAsset:
        return PaymentAsset.TEURC

    def settle(self, authorization: PaymentAuthorization) -> SettlementResult:
        return SettlementResult(status=SettlementStatus.CONFIRMED, asset=PaymentAsset.TEURC,
                                amount_units=authorization.value_units)


class _IncompleteAdapter:
    def parse_x_payment_header(self, header_value):  # missing the other two methods
        ...


def test_x402_adapter_interface_is_satisfiable_and_checkable():
    assert isinstance(_StubAdapter(), X402PaymentAdapter)
    assert not isinstance(_IncompleteAdapter(), X402PaymentAdapter)


def test_settlement_engine_interface_is_satisfiable():
    engine = _StubSettlement()
    assert isinstance(engine, SettlementEngine)
    assert engine.supported_asset is PaymentAsset.TEURC
    result = engine.settle(PaymentAuthorization(from_address="0xa", to_address="0xb", value_units=20000,
                                                valid_after=0, valid_before=1, nonce="0x00", signature="0x00"))
    assert result.ok and result.status is SettlementStatus.CONFIRMED


def test_trust_gate_interface_and_decision_shape():
    class _StubGate:
        def evaluate(self, provider, service, *, requested_amount_units, min_reputation_tier=0):
            if provider.kya_status is not KYAStatus.VERIFIED:
                return TrustDecision(allowed=False, reason="not KYA-verified",
                                     kya_status=provider.kya_status, policy_decision="deny:not-kya")
            return TrustDecision(allowed=True, reason="ok", kya_status=provider.kya_status,
                                 reputation_tier=provider.trust_tier, policy_decision="allow")

    gate = _StubGate()
    assert isinstance(gate, TrustGate)
    denied = gate.evaluate(_local_style_service().provider, _local_style_service(), requested_amount_units=5000)
    allowed = gate.evaluate(_github_style_service().provider, _github_style_service(), requested_amount_units=100000)
    assert denied.allowed is False and "KYA" in denied.reason
    assert allowed.allowed is True and allowed.reputation_tier == 2
