"""StandardX402Adapter — the standard x402 `X-PAYMENT` header <-> internal model.

Implements the canonical `X402PaymentAdapter` (unified/payment.py) using the exact
standard-x402 wire format the local marketplace layer already used in real testnet
payments (`x402/common.py`, `x402/resource_server.py`): a 402 body with
`accepts[].maxAmountRequired`, and a base64 `X-PAYMENT` request header carrying
`{x402Version, scheme, network, payload:{authorization, signature}}`.

This adapter is standalone (base64/JSON only — no web3), and does NOT modify any
existing server or client. It is the single shared implementation both sides can
adopt in later phases. Phase 2 rule honoured: the public wire format is unchanged
— this simply exposes it behind the canonical interface.
"""

from __future__ import annotations

import base64
import json

from unified.models import PaymentAsset
from unified.payment import PaymentAuthorization


class StandardX402Adapter:
    """Canonical standard-x402 adapter. Implements `X402PaymentAdapter`."""

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
        """The standard x402 402 body a resource server returns when payment is due."""
        return {
            "x402Version": 1,
            "accepts": [
                {
                    "scheme": "exact",
                    "network": network,
                    "maxAmountRequired": str(int(amount_units)),
                    "resource": resource,
                    "description": description or f"Resource: {resource}",
                    "payTo": pay_to,
                    "asset": asset_address,
                    "extra": {"assetSymbol": asset.value},
                }
            ],
        }

    def encode_x_payment_header(self, authorization: PaymentAuthorization, *, scheme: str = "exact") -> str:
        """Encode a PaymentAuthorization into the base64 `X-PAYMENT` header value.
        Mirrors x402/common.encode_payment_header exactly."""
        inner = {
            "authorization": {
                "from": authorization.from_address,
                "to": authorization.to_address,
                "value": str(int(authorization.value_units)),
                "validAfter": authorization.valid_after,
                "validBefore": authorization.valid_before,
                "nonce": authorization.nonce,
            },
            "signature": authorization.signature,
        }
        # H-2: atomic binding travels IN the payload (the X-PAYMENT envelope format
        # is unchanged). Legacy authorizations carry neither key.
        if authorization.mode == "atomic":
            inner["mode"] = "atomic"
            if authorization.salt is not None:
                inner["salt"] = authorization.salt
        payload = {
            "x402Version": 1,
            "scheme": scheme,
            "network": authorization.network,
            "payload": inner,
        }
        return base64.b64encode(json.dumps(payload).encode()).decode()

    def parse_x_payment_header(self, header_value: str) -> PaymentAuthorization:
        """Decode a base64 `X-PAYMENT` header into a PaymentAuthorization.
        Raises ValueError on any malformed/missing field (never a partial object)."""
        try:
            decoded = json.loads(base64.b64decode(header_value).decode())
            inner = decoded["payload"]
            auth = inner["authorization"]
            signature = inner["signature"]
            network = decoded.get("network", "whitechain-testnet")
            return PaymentAuthorization(
                from_address=auth["from"],
                to_address=auth["to"],
                value_units=int(auth["value"]),
                valid_after=int(auth["validAfter"]),
                valid_before=int(auth["validBefore"]),
                nonce=auth["nonce"],
                signature=signature,
                asset=PaymentAsset.TEURC,  # canonical default; asset_address carried separately in the 402
                network=network,
                mode=inner.get("mode", "legacy"),   # H-2: atomic binding, if present
                salt=inner.get("salt"),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error) as exc:
            raise ValueError(f"Malformed X-PAYMENT header: {exc}") from exc
