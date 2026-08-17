"""Phase 4 — canonical config + asset unification tests.

Covers: default asset = tEURC; apUSD compatibility; invalid token address /
chain id rejected; amount conversion; fee/net; x402 payload asset; registry
listing asset; .env untracked; and a full economics walkthrough with no hidden
*10**decimals.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from eth_account import Account  # noqa: E402

from unified.adapters import StandardX402Adapter  # noqa: E402
from unified.config import (  # noqa: E402
    fee_split,
    from_minimal_units,
    load_unified_config,
    to_minimal_units,
    validate_chain_id,
    validate_token_address,
)
from unified.models import PaymentAsset  # noqa: E402
from unified.registry import QualityMetrics, UnifiedRegistry, sign_listing  # noqa: E402

GOOD_ADDR = "0x6aadCEc9E885BeeeB1B01924174a4Bb261caA579"


# ---------- 1. default asset = tEURC ----------

def test_default_asset_is_teurc():
    cfg = load_unified_config(env={})
    assert cfg.canonical_asset is PaymentAsset.TEURC
    assert cfg.canonical.symbol == "tEURC"
    assert cfg.canonical.is_canonical is True
    assert cfg.canonical.decimals == 6


# ---------- 2. apUSD compatibility works ----------

def test_apusd_is_supported_compatibility_asset():
    cfg = load_unified_config(env={"APUSD_TOKEN_ADDRESS": GOOD_ADDR})
    info = cfg.asset_info(PaymentAsset.APUSD)
    assert cfg.is_supported(PaymentAsset.APUSD) is True
    assert info.role == "compatibility" and info.is_canonical is False
    assert info.address == GOOD_ADDR


# ---------- 2b. canonical env names + fallbacks ----------

def test_canonical_names_and_fallbacks():
    # New canonical names win...
    cfg = load_unified_config(env={"WHITECHAIN_RPC_URL": "https://rpc", "WHITECHAIN_CHAIN_ID": "2625",
                                   "TEURC_TOKEN_ADDRESS": GOOD_ADDR})
    assert cfg.rpc_url == "https://rpc" and cfg.chain_id == 2625 and cfg.canonical.address == GOOD_ADDR
    # ...else fall back to the existing names (nothing breaks).
    cfg2 = load_unified_config(env={"WHITECHAIN_TESTNET_RPC": "https://old", "CHAIN_ID": "2625",
                                    "TEURC_ADDRESS": GOOD_ADDR})
    assert cfg2.rpc_url == "https://old" and cfg2.canonical.address == GOOD_ADDR


# ---------- 3. invalid token address rejected ----------

def test_invalid_token_address_rejected():
    assert validate_token_address(GOOD_ADDR) == GOOD_ADDR
    with pytest.raises(ValueError):
        validate_token_address("0xnothex")
    with pytest.raises(ValueError):
        validate_token_address("")                 # empty not allowed by default
    assert validate_token_address("", allow_empty=True) == ""


# ---------- 4. invalid chain id rejected ----------

def test_chain_id_mismatch_rejected():
    validate_chain_id(2625, 2625)                  # ok
    with pytest.raises(ValueError):
        validate_chain_id(1, 2625)


# ---------- 5. amount conversion correct (human <-> minimal units) ----------

def test_amount_conversion_roundtrip():
    cfg = load_unified_config(env={})
    teurc = cfg.canonical
    assert to_minimal_units("0.02", teurc) == 20_000        # 0.02 tEURC, 6 decimals
    assert to_minimal_units("1", teurc) == 1_000_000
    assert from_minimal_units(20_000, teurc) == "0.020000"  # fixed 6-decimal string
    assert from_minimal_units(19_900, teurc) == "0.019900"


# ---------- 6. fee / net calculation correct ----------

def test_fee_split_floors_and_conserves():
    fee, net = fee_split(20_000, 50)               # 0.5%
    assert (fee, net) == (100, 19_900)
    assert fee + net == 20_000                     # invariant: nothing lost
    # sub-bps remainder favours the seller (floor fee)
    fee2, net2 = fee_split(999, 50)
    assert fee2 == 4 and net2 == 995 and fee2 + net2 == 999
    with pytest.raises(ValueError):
        fee_split(1000, 20000)                     # fee_bps out of range


# ---------- 7. x402 payload contains the right asset ----------

def test_x402_payload_carries_asset():
    cfg = load_unified_config(env={"TEURC_TOKEN_ADDRESS": GOOD_ADDR})
    body = StandardX402Adapter().build_payment_required(
        resource="/weather", amount_units=20_000, pay_to="0xdead",
        asset=cfg.canonical_asset, asset_address=cfg.canonical.address, network="whitechain-testnet")
    accept = body["accepts"][0]
    assert accept["asset"] == GOOD_ADDR                    # token address
    assert accept["extra"]["assetSymbol"] == "tEURC"       # canonical symbol
    assert accept["maxAmountRequired"] == "20000"


# ---------- 8. registry listing carries the right asset (tEURC default, apUSD compat) ----------

def test_registry_listing_asset_default_and_compat():
    prov = Account.create()
    listing = {"id": prov.address, "capability_type": "weather", "provider_url": "http://p",
               "pay_to": GOOD_ADDR, "price_wei": 5000, "min_reputation_tier": 0}
    sig = sign_listing(listing, prov.key.hex())

    reg = UnifiedRegistry()
    reg.register(listing, sig, QualityMetrics(4.2, 0.96, 180))               # default tEURC
    assert reg.discover()[0].payment_asset is PaymentAsset.TEURC

    reg2 = UnifiedRegistry()
    reg2.register(listing, sig, QualityMetrics(4.2, 0.96, 180), asset=PaymentAsset.APUSD)  # compat
    assert reg2.discover()[0].payment_asset is PaymentAsset.APUSD            # apUSD preserved


# ---------- 9. .env is not tracked ----------

def test_env_not_tracked():
    out = subprocess.run(["git", "ls-files", ".env"], cwd=REPO, capture_output=True, text=True)
    assert out.stdout.strip() == "", ".env must never be tracked"


# ---------- 9b. full economics walkthrough (no hidden *10**decimals) ----------

def test_economics_walkthrough_20000_units():
    """price_units 20000 → 0.02 tEURC → fee → net → on-chain amount, all integer."""
    cfg = load_unified_config(env={})
    teurc = cfg.canonical                       # decimals = 6
    price_units = 20_000
    assert from_minimal_units(price_units, teurc) == "0.020000"  # human view
    fee, net = fee_split(price_units, 50)                        # 0.5%
    assert fee == 100 and net == 19_900                         # 0.0001 / 0.0199 tEURC
    assert from_minimal_units(fee, teurc) == "0.000100"
    assert from_minimal_units(net, teurc) == "0.019900"
    # the on-chain transfer moves exactly price_units minimal units, no rescaling
    assert fee + net == price_units
