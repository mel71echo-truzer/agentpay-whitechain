"""Discovery mappers — existing registry records -> canonical Service/Provider.

Two sources, one canonical model:
  - the local marketplace registry (`registry/services.json` shape): rich quality
    metrics, no on-chain identity;
  - the GitHub capability registry (signed capability record): on-chain provider
    identity + pay_to, no quality metrics yet.

These are pure functions — they don't fetch anything, they translate a record a
caller already has. Trust fields (KYA/SBT) on a mapped Provider are left unset;
they get populated at gate time by the TrustGate reading WB Soul. Quality metrics
absent from GitHub records default to neutral values.
"""

from __future__ import annotations

from unified.models import PaymentAsset, Provider, Service


def service_from_local_registry(record: dict) -> Service:
    """Map a local `services.json` entry to a canonical Service.

    Local records carry observed quality metrics (rating/success_rate/latency_ms)
    but no on-chain provider identity, so the Provider is marketplace-only (no KYA).
    The local asset is apUSD (dev/compat); it is preserved, not rewritten.
    """
    provider = Provider(
        provider_id=str(record.get("provider_id") or record.get("name", "")),
        pay_to=str(record.get("pay_to", "")),
    )
    return Service(
        name=str(record["name"]),
        description=str(record.get("description", "")),
        url=str(record.get("url", "")),
        endpoint=str(record.get("endpoint", "")),
        method=str(record.get("method", "POST")),
        category=str(record.get("category", "")),
        price_units=int(record.get("price_units", 0)),
        currency=str(record.get("currency", PaymentAsset.APUSD.value)),
        network=str(record.get("network", "whitechain-testnet")),
        rating=float(record.get("rating", 0.0)),
        success_rate=float(record.get("success_rate", 0.0)),
        latency_ms=float(record.get("latency_ms", 500.0)),
        provider=provider,
    )


def service_from_capability_record(record: dict) -> Service:
    """Map a GitHub signed capability record to a canonical Service.

    Capability records carry on-chain provider identity (`id`/`owner_address`) and
    `pay_to`, but no quality metrics — those default to neutral until the registry
    starts observing them (Phase 3). The signer, when present, binds the record.
    `min_reputation_tier` is a per-resource requirement, not a provider attribute,
    so it is surfaced in policy_tags for the TrustGate call, not as a Service field.
    """
    signer = record.get("owner_address") or record.get("signer")
    min_tier = record.get("min_reputation_tier", 0)
    provider = Provider(
        provider_id=str(record.get("id", "")),
        signer=str(signer) if signer else None,
        pay_to=str(record.get("pay_to", "")),
        policy_tags=(f"min_reputation_tier={int(min_tier or 0)}",),
    )
    capability = str(record.get("capability_type", ""))
    return Service(
        name=capability or str(record.get("id", "")),
        description=str(record.get("description", capability)),
        url=str(record.get("provider_url", "")),
        endpoint=str(record.get("endpoint", "")),
        method=str(record.get("method", "POST")),
        category=capability,
        price_units=int(record.get("price_wei", record.get("price_units", 0))),
        currency=str(record.get("currency", PaymentAsset.TEURC.value)),
        network=str(record.get("network", "whitechain-testnet")),
        # Quality metrics not yet observed for GitHub providers — neutral defaults.
        rating=float(record.get("rating", 0.0)),
        success_rate=float(record.get("success_rate", 0.0)),
        latency_ms=float(record.get("latency_ms", 500.0)),
        provider=provider,
    )
