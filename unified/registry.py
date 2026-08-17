"""Unified registry — seller-signed listing + registry-attested quality.

The anti-forge rule (Phase 3 constraint 4): a signed registry record carries
quality metrics, but the **seller cannot self-forge reputation/quality**. This is
enforced structurally by reusing `registry_auth`, whose signature covers ONLY the
listing fields:

    SIGNED_FIELDS = (id, capability_type, provider_url, pay_to, price_wei, min_reputation_tier)

Quality (`rating`, `success_rate`, `latency_ms`) is **outside** the seller's
signature — it is attested by the registry (an observer), never by the seller. So:
  - a seller can bind their identity, endpoint, price and pay_to (things they
    legitimately declare) — and tampering any of those breaks verification;
  - a seller CANNOT sign quality, so any quality value a seller submits is ignored;
    the registry's attestation is the authority, tagged FieldSource.REGISTRY_SUPPLIED.

This module does not fetch anything and does not change existing registry code —
it is a canonical layer over `registry_auth` + the `unified` model.
"""

from __future__ import annotations

from dataclasses import dataclass

import registry_auth
from unified.models import PaymentAsset, Provider, Service


@dataclass
class QualityMetrics:
    """Registry-attested quality. NOT part of the seller's signature."""

    rating: float = 0.0          # 0..5, observed
    success_rate: float = 0.0    # 0..1, observed
    latency_ms: float = 500.0    # observed


@dataclass
class RegistryRecord:
    """A verified listing + the registry's independent quality attestation."""

    listing: dict                # exactly registry_auth.SIGNED_FIELDS
    signature: str               # seller signature over `listing` only
    quality: QualityMetrics      # attached by the registry, not the seller
    owner: str = ""              # recovered signer (== listing['id']); set on verify


def sign_listing(listing: dict, private_key: str) -> str:
    """Seller signs the LISTING (registry_auth). Quality is not signable here."""
    return registry_auth.sign_registration(listing, private_key)


class UnifiedRegistry:
    """In-memory canonical registry: verifies seller signatures, attaches quality
    as the registry, and serves canonical Services for discovery."""

    def __init__(self):
        self._records: list[RegistryRecord] = []

    def register(self, listing: dict, signature: str, quality: QualityMetrics) -> RegistryRecord:
        """Verify the seller's signature over the listing, then store it with the
        registry's OWN quality attestation. Raises ValueError on a bad signature.
        Any quality-looking keys inside `listing` are irrelevant — they are not
        signed and are never read as quality."""
        owner = registry_auth.verify_registration(listing, signature)  # raises on tamper / id!=signer
        # Keep only the canonical signed fields; a seller can't smuggle quality in.
        clean_listing = {k: listing[k] for k in registry_auth.SIGNED_FIELDS}
        record = RegistryRecord(listing=clean_listing, signature=signature, quality=quality, owner=owner)
        self._records.append(record)
        return record

    def attest_quality(self, provider_id: str, quality: QualityMetrics) -> None:
        """Registry updates observed quality for a provider's records (observer
        authority — never the seller). Present so quality provenance stays with
        the registry, not the listing."""
        for r in self._records:
            if str(r.listing["id"]).lower() == provider_id.lower():
                r.quality = quality

    def to_service(self, record: RegistryRecord) -> Service:
        """Build a canonical Service: listing fields from the VERIFIED signature,
        quality from the registry attestation (REGISTRY_SUPPLIED)."""
        listing = record.listing
        min_tier = int(listing.get("min_reputation_tier", 0) or 0)
        provider = Provider(
            provider_id=str(listing["id"]),
            signer=record.owner or None,             # recovered signer (binds identity)
            pay_to=str(listing["pay_to"]),
            policy_tags=(f"min_reputation_tier={min_tier}",),
        )
        capability = str(listing["capability_type"])
        return Service(
            name=capability,
            description=capability,
            url=str(listing["provider_url"]),
            category=capability,
            price_units=int(listing["price_wei"]),
            currency=PaymentAsset.TEURC.value,
            network="whitechain-testnet",
            # quality strictly from the registry attestation, not the seller:
            rating=float(record.quality.rating),
            success_rate=float(record.quality.success_rate),
            latency_ms=float(record.quality.latency_ms),
            provider=provider,
        )

    def discover(self, *, category: str | None = None, max_price_units: int | None = None) -> list[Service]:
        """Return canonical Services (verified listings), optionally filtered by
        category and budget. Only signature-verified records are stored, so
        discovery never returns an unsigned/tampered listing."""
        out: list[Service] = []
        for r in self._records:
            if category and str(r.listing["capability_type"]).lower() != category.lower():
                continue
            if max_price_units is not None and int(r.listing["price_wei"]) > max_price_units:
                continue
            out.append(self.to_service(r))
        return out
