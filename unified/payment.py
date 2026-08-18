"""Public payment protocol = standard x402 `X-PAYMENT` header, and the adapter
that translates it into the internal payment-authorization model.

Decision (product owner): the public interface is the **standard x402**
`X-PAYMENT` header (Coinbase-style: a 402 with `accepts[].maxAmountRequired`,
`payTo`, `asset`, `network`, and a base64 `X-PAYMENT` request header carrying the
EIP-3009 authorization + signature). This is more interoperable than the GitHub
layer's bespoke JSON body, so it becomes the canonical wire format.

`X402PaymentAdapter` is the seam between that public protocol and whatever the
trust/settlement layer wants internally. The buyer speaks standard x402; the
adapter yields a `PaymentAuthorization`; the SettlementEngine consumes that. The
canonical settlement asset is tEURC (apUSD retained as dev/compat).

Phase 1 defines the interface + the internal model only. The concrete adapter
(bridging to facilitator/payment.py's EIP-712 validation) is Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from unified.models import PaymentAsset


@dataclass
class PaymentAuthorization:
    """Internal, framework-agnostic representation of an EIP-3009 authorization.

    Money is integer minimal units — no float on any money path (a hard rule
    carried over from both layers). `nonce`/`signature` are 0x-hex strings.
    """

    from_address: str
    to_address: str                 # payTo (facilitator or router), pinned from the signed record
    value_units: int                # minimal units of `asset`
    valid_after: int
    valid_before: int
    nonce: str                      # 0x… bytes32
    signature: str                  # 0x… 65-byte sig
    asset: PaymentAsset = PaymentAsset.TEURC
    asset_address: Optional[str] = None
    network: str = "whitechain-testnet"
    resource: Optional[str] = None  # the resource this authorization is bound to
    # H-2: atomic resource-binding inputs carried in the X-PAYMENT payload (not the
    # envelope). `salt` lets the server re-derive keccak(resource‖salt) from the
    # CANONICAL resource; `mode` selects legacy vs atomic (ReceiveWithAuthorization
    # + derived nonce). Absent → legacy (backwards-compatible).
    salt: Optional[str] = None      # 0x… 32-byte client salt (atomic binding)
    mode: str = "legacy"            # "legacy" | "atomic"
    extra: dict = field(default_factory=dict)


@runtime_checkable
class X402PaymentAdapter(Protocol):
    """Translates the standard x402 protocol <-> the internal PaymentAuthorization.

    Three responsibilities, all pure/deterministic (no chain writes):
      - `build_payment_required` — produce the 402 body a resource server returns
        when payment is needed (standard x402 `accepts[...]`).
      - `parse_x_payment_header` — decode an inbound `X-PAYMENT` header into a
        `PaymentAuthorization`.
      - `encode_x_payment_header` — build the `X-PAYMENT` header from an authorization
        (buyer side), so buyer and server share one implementation.
    """

    def build_payment_required(
        self,
        *,
        resource: str,
        amount_units: int,
        pay_to: str,
        asset: PaymentAsset,
        asset_address: str,
        network: str,
        description: str = "",
    ) -> dict:
        """Return the standard x402 402 body: {x402Version, accepts:[{scheme,
        network, maxAmountRequired, resource, payTo, asset, ...}]}."""
        ...

    def parse_x_payment_header(self, header_value: str) -> PaymentAuthorization:
        """Decode a base64 `X-PAYMENT` header into a `PaymentAuthorization`.
        Raises ValueError on a malformed header (never returns a partial object)."""
        ...

    def encode_x_payment_header(self, authorization: PaymentAuthorization, *, scheme: str = "exact") -> str:
        """Encode an authorization back into a base64 `X-PAYMENT` header value."""
        ...
