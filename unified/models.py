"""Canonical Service / Provider model unifying the local + GitHub field sets.

The unified model separates a **Provider** (an accountable, on-chain-anchored
identity) from a **Service** (a concrete capability that provider offers). This
mirrors reality: one provider (one WB Soul) can offer many services, and trust
attaches to the provider while quality attaches to the service.

Every field is tagged with its **source of truth** (`FieldSource`), because that
governs how much it can be trusted and who may set it:

  - PROVIDER_SUPPLIED  — declared by the provider (self-asserted; must be
                         signature-bound, never trusted blindly).
  - REGISTRY_SUPPLIED  — maintained by the registry (observed metrics, listing).
  - ONCHAIN_VERIFIED   — read from chain (WB Soul KYA, SBT tier); authoritative.
  - CALCULATED         — derived by the marketplace/trust engines (never stored
                         as ground truth).

Phase 1 is design only: this is a data model, not wired into any server.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, fields
from typing import Optional


class FieldSource(enum.Enum):
    """Where a field's value comes from — governs how far it can be trusted."""

    PROVIDER_SUPPLIED = "provider-supplied"
    REGISTRY_SUPPLIED = "registry-supplied"
    ONCHAIN_VERIFIED = "on-chain-verified"
    CALCULATED = "calculated"


class KYAStatus(enum.Enum):
    """Know-Your-Agent status from WB Soul (on-chain)."""

    UNKNOWN = "unknown"          # no soul / not looked up
    UNVERIFIED = "unverified"    # soul exists but IsVerified == false
    VERIFIED = "verified"        # soulOf != 0 AND IsVerified == true


class PaymentAsset(enum.Enum):
    """Settlement asset. tEURC is the canonical/default production asset; apUSD is
    retained as a dev/test / compatibility asset (see docs/architecture)."""

    TEURC = "tEURC"    # canonical, production default
    APUSD = "apUSD"    # dev/test + compatibility layer


def _src(source: FieldSource, **kw):
    """dataclass field() helper that records the canonical source of truth."""
    return field(metadata={"source": source}, **kw)


@dataclass
class Provider:
    """An accountable AgentPay provider identity (one per WB Soul).

    Trust attaches HERE, not to the individual service. The marketplace scoring
    layer must not read these fields as quality; the TrustGate reads them.
    """

    # --- identity (on-chain anchored) ---
    provider_id: str = _src(FieldSource.PROVIDER_SUPPLIED)          # address the provider claims
    signer: Optional[str] = _src(FieldSource.ONCHAIN_VERIFIED, default=None)   # recovered signer of the registry record; must == provider_id
    pay_to: str = _src(FieldSource.PROVIDER_SUPPLIED, default="")   # where funds land (facilitator/router); buyer pins this
    # --- trust (on-chain verified) ---
    kya_status: KYAStatus = _src(FieldSource.ONCHAIN_VERIFIED, default=KYAStatus.UNKNOWN)
    soul_id: Optional[int] = _src(FieldSource.ONCHAIN_VERIFIED, default=None)
    sbt_reputation_tier: Optional[int] = _src(FieldSource.ONCHAIN_VERIFIED, default=None)  # SBT-attested tier
    trust_tier: Optional[int] = _src(FieldSource.CALCULATED, default=None)      # effective tier = max(sbt, behavioral)
    # --- policy / allowlist ---
    allowlisted: bool = _src(FieldSource.REGISTRY_SUPPLIED, default=False)
    policy_tags: tuple[str, ...] = _src(FieldSource.REGISTRY_SUPPLIED, default=())

    def signature_is_bound(self) -> bool:
        """True when the registry record's recovered signer matches the claimed id
        (the GitHub 'id == signer' rule). A provider whose signature isn't bound
        must be treated as unverified regardless of other fields."""
        return self.signer is not None and self.signer.lower() == self.provider_id.lower()


@dataclass
class Service:
    """A concrete capability a Provider offers. Quality attaches HERE.

    Marketplace fields (rating/success_rate/latency_ms) come from the registry's
    observation, not the provider's self-assertion, precisely so quality can't be
    gamed by the seller the way self-declared claims could be.
    """

    # --- listing (provider-supplied, must be signature-bound) ---
    name: str = _src(FieldSource.PROVIDER_SUPPLIED)
    description: str = _src(FieldSource.PROVIDER_SUPPLIED, default="")
    url: str = _src(FieldSource.PROVIDER_SUPPLIED, default="")
    endpoint: str = _src(FieldSource.PROVIDER_SUPPLIED, default="")
    method: str = _src(FieldSource.PROVIDER_SUPPLIED, default="POST")
    category: str = _src(FieldSource.PROVIDER_SUPPLIED, default="")
    # --- price / asset (provider-supplied; asset canonicalized to tEURC) ---
    price_units: int = _src(FieldSource.PROVIDER_SUPPLIED, default=0)   # minimal units (integer, no float)
    currency: str = _src(FieldSource.PROVIDER_SUPPLIED, default=PaymentAsset.TEURC.value)
    network: str = _src(FieldSource.PROVIDER_SUPPLIED, default="whitechain-testnet")
    # --- quality metrics (registry-observed, NOT self-asserted) ---
    rating: float = _src(FieldSource.REGISTRY_SUPPLIED, default=0.0)        # 0..5
    success_rate: float = _src(FieldSource.REGISTRY_SUPPLIED, default=0.0)  # 0..1
    latency_ms: float = _src(FieldSource.REGISTRY_SUPPLIED, default=500.0)  # observed
    # --- the provider offering this service ---
    provider: Optional[Provider] = _src(FieldSource.REGISTRY_SUPPLIED, default=None)

    @property
    def payment_asset(self) -> PaymentAsset:
        """The canonical asset enum for this service's currency (defaults to tEURC
        if the currency string is unrecognised — production default)."""
        for asset in PaymentAsset:
            if asset.value.lower() == str(self.currency).lower():
                return asset
        return PaymentAsset.TEURC


def field_sources(model_cls) -> dict[str, FieldSource]:
    """Introspect a canonical model's per-field source of truth. Used by tests and
    tooling to assert the provider/registry/on-chain/calculated classification."""
    return {f.name: f.metadata["source"] for f in fields(model_cls) if "source" in f.metadata}
