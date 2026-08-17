"""unified/config.py — one canonical place for network + asset configuration.

Centralizes what was scattered across config.py (GitHub), x402/common.py (local)
and the unified modules: network, chain id, RPC, the canonical settlement asset
and its address/decimals, compatibility assets, default settlement mode, and the
facilitator/registry URLs. Nothing else should hard-code these.

Asset model (the Phase 4 target):

        canonical = tEURC (production, default)
        compatibility = apUSD (kept working, never removed)

Both settle through the same SettlementEngine onto Whitechain testnet.

Env var names are the NEW canonical ones the operator asked for, but each falls
back to the EXISTING name so the current E2E and config.py keep working unchanged:

    WHITECHAIN_RPC_URL     ← WHITECHAIN_TESTNET_RPC
    WHITECHAIN_CHAIN_ID    ← CHAIN_ID            (default 2625)
    TEURC_TOKEN_ADDRESS    ← TEURC_ADDRESS
    APUSD_TOKEN_ADDRESS    (new)
    FACILITATOR_URL        (new)
    REGISTRY_URL           (new)
    SETTLEMENT_MODE        (existing)

Money is always integer MINIMAL UNITS on the settlement path. The only place
decimals appear is the human<->units boundary helpers below, which reuse the
single conversion in money.py (no second implementation, no hidden *10**decimals).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional

from web3 import Web3

import money
from unified.models import PaymentAsset

CANONICAL_ASSET = PaymentAsset.TEURC
COMPATIBILITY_ASSETS = (PaymentAsset.APUSD,)


@dataclass(frozen=True)
class PaymentAssetInfo:
    """Everything needed to price/settle one asset."""

    asset: PaymentAsset
    symbol: str
    address: str          # token contract address ("" if not configured)
    decimals: int
    role: str             # "canonical" | "compatibility"

    @property
    def is_canonical(self) -> bool:
        return self.role == "canonical"


@dataclass(frozen=True)
class UnifiedConfig:
    network: str
    chain_id: int
    rpc_url: str
    settlement_mode: str
    facilitator_url: str
    registry_url: str
    canonical_asset: PaymentAsset
    assets: Mapping[PaymentAsset, PaymentAssetInfo]

    @property
    def canonical(self) -> PaymentAssetInfo:
        return self.assets[self.canonical_asset]

    def asset_info(self, asset: Optional[PaymentAsset] = None) -> PaymentAssetInfo:
        """Info for `asset` (default: the canonical asset)."""
        return self.assets[asset or self.canonical_asset]

    def is_supported(self, asset: PaymentAsset) -> bool:
        return asset in self.assets


def _first(env: Mapping[str, str], *names: str, default: str = "") -> str:
    for n in names:
        v = env.get(n)
        if v not in (None, ""):
            return v
    return default


def load_unified_config(env: Optional[Mapping[str, str]] = None) -> UnifiedConfig:
    """Build the canonical config from env (canonical names, existing fallbacks)."""
    env = env if env is not None else os.environ

    rpc_url = _first(env, "WHITECHAIN_RPC_URL", "WHITECHAIN_TESTNET_RPC")
    chain_id = int(_first(env, "WHITECHAIN_CHAIN_ID", "CHAIN_ID", default="2625"))
    network = _first(env, "NETWORK", default="local")
    settlement_mode = _first(env, "SETTLEMENT_MODE", default="atomic").strip().lower()
    facilitator_url = _first(env, "FACILITATOR_URL")
    registry_url = _first(env, "REGISTRY_URL")

    teurc = PaymentAssetInfo(
        asset=PaymentAsset.TEURC, symbol=PaymentAsset.TEURC.value,
        address=_first(env, "TEURC_TOKEN_ADDRESS", "TEURC_ADDRESS"),
        decimals=int(_first(env, "TEURC_DECIMALS", default="6")),
        role="canonical",
    )
    apusd = PaymentAssetInfo(
        asset=PaymentAsset.APUSD, symbol=PaymentAsset.APUSD.value,
        address=_first(env, "APUSD_TOKEN_ADDRESS"),
        decimals=int(_first(env, "APUSD_DECIMALS", default="6")),
        role="compatibility",
    )
    return UnifiedConfig(
        network=network, chain_id=chain_id, rpc_url=rpc_url, settlement_mode=settlement_mode,
        facilitator_url=facilitator_url, registry_url=registry_url,
        canonical_asset=CANONICAL_ASSET,
        assets={PaymentAsset.TEURC: teurc, PaymentAsset.APUSD: apusd},
    )


# ---------------- validation ----------------

def validate_token_address(address: str, *, allow_empty: bool = False) -> str:
    """Return the checksummed address, or raise ValueError on a malformed one.
    An empty address raises unless `allow_empty` (e.g. apUSD not configured)."""
    if not address:
        if allow_empty:
            return ""
        raise ValueError("token address is empty")
    if not Web3.is_address(address):
        raise ValueError(f"invalid token address: {address!r}")
    return Web3.to_checksum_address(address)


def validate_chain_id(actual: int, expected: int) -> None:
    """Raise if the chain the RPC reports isn't the one we expect."""
    if int(actual) != int(expected):
        raise ValueError(f"chain id mismatch: RPC reports {actual}, expected {expected}")


# ---------------- human <-> minimal units (boundary only) ----------------

def to_minimal_units(human_amount: str | Decimal, asset: PaymentAssetInfo) -> int:
    """Human amount (e.g. '0.02') -> integer minimal units, via money.py. Used only
    at the human boundary; the settlement path never calls this."""
    return money.teurc_to_wei(str(human_amount), asset.decimals)


def from_minimal_units(units: int, asset: PaymentAssetInfo) -> str:
    """Integer minimal units -> human string (for display only)."""
    return money.wei_to_teurc_str(int(units), asset.decimals)


def fee_split(amount_units: int, fee_bps: int) -> tuple[int, int]:
    """(fee, net) in minimal units. Fee is floored; net = amount - fee, so any
    sub-bps remainder favours the seller. Mirrors facilitator.settlement exactly.
    Invariant: fee + net == amount (nothing is created or lost). All integer —
    no float, no hidden *10**decimals."""
    if not (0 <= int(fee_bps) <= 10000):
        raise ValueError(f"fee_bps out of range 0..10000: {fee_bps}")
    amount = int(amount_units)
    fee = (amount * int(fee_bps)) // 10000
    return fee, amount - fee
