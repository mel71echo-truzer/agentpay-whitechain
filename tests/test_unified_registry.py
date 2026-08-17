"""Phase 3 — unified registry: seller-signed listing + registry-attested quality.

Proves the anti-forge rule: the seller signs the listing (identity/endpoint/price/
pay_to) and CANNOT sign or forge quality; the registry is the quality authority.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402

from unified.models import KYAStatus  # noqa: E402
from unified.registry import QualityMetrics, UnifiedRegistry, sign_listing  # noqa: E402

FACILITATOR = "0x000000000000000000000000000000000000dEaD"


def _listing(provider, *, price_wei=5000, min_tier=0, capability="weather"):
    return {
        "id": provider.address,
        "capability_type": capability,
        "provider_url": "http://provider:8000",
        "pay_to": FACILITATOR,
        "price_wei": price_wei,
        "min_reputation_tier": min_tier,
    }


def test_register_and_discover_verified_listing():
    prov = Account.create()
    listing = _listing(prov)
    sig = sign_listing(listing, prov.key.hex())
    reg = UnifiedRegistry()
    reg.register(listing, sig, QualityMetrics(rating=4.2, success_rate=0.96, latency_ms=180))

    services = reg.discover(category="weather", max_price_units=10000)
    assert len(services) == 1
    svc = services[0]
    assert svc.rating == 4.2 and svc.success_rate == 0.96 and svc.latency_ms == 180
    assert svc.provider.provider_id == prov.address
    assert svc.provider.signature_is_bound() is True     # signer recovered == id
    # Provider carries no on-chain trust yet; that is the TrustGate's job.
    assert svc.provider.kya_status is KYAStatus.UNKNOWN


def test_seller_cannot_forge_quality_registry_is_authority():
    prov = Account.create()
    listing = _listing(prov)
    sig = sign_listing(listing, prov.key.hex())
    # Seller smuggles an inflated rating into the submitted dict...
    tampered_submission = {**listing, "rating": 5.0, "success_rate": 1.0, "latency_ms": 1}
    reg = UnifiedRegistry()
    # ...but the signature only covers SIGNED_FIELDS, so registration still verifies,
    # and the registry attaches its OWN observed quality — the smuggled values vanish.
    reg.register(tampered_submission, sig, QualityMetrics(rating=4.2, success_rate=0.96, latency_ms=180))
    svc = reg.discover()[0]
    assert svc.rating == 4.2          # registry's number, NOT the seller's 5.0
    assert svc.latency_ms == 180


def test_tampered_signed_field_fails_verification():
    prov = Account.create()
    listing = _listing(prov)
    sig = sign_listing(listing, prov.key.hex())
    reg = UnifiedRegistry()
    # Change a SIGNED field (pay_to) but keep the old signature -> must reject.
    hijacked = {**listing, "pay_to": "0x0000000000000000000000000000000000001234"}
    with pytest.raises(ValueError):
        reg.register(hijacked, sig, QualityMetrics())


def test_wrong_signer_id_mismatch_rejected():
    prov, other = Account.create(), Account.create()
    listing = _listing(prov)
    sig = sign_listing(listing, other.key.hex())  # signed by someone who isn't `id`
    with pytest.raises(ValueError):
        UnifiedRegistry().register(listing, sig, QualityMetrics())


def test_registry_attest_quality_updates_authority():
    prov = Account.create()
    listing = _listing(prov)
    sig = sign_listing(listing, prov.key.hex())
    reg = UnifiedRegistry()
    reg.register(listing, sig, QualityMetrics(rating=3.0, success_rate=0.8, latency_ms=400))
    reg.attest_quality(prov.address, QualityMetrics(rating=4.8, success_rate=0.99, latency_ms=100))
    assert reg.discover()[0].rating == 4.8
