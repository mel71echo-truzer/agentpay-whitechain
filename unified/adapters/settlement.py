"""FacilitatorSettlementEngine — wraps facilitator.settlement / atomic_settlement.

Implements the canonical `SettlementEngine` by DELEGATING to an existing engine
(`facilitator.settlement.SettlementEngine` legacy relay+forward, or
`facilitator.atomic_settlement.AtomicSettlementEngine` router). Both expose the
same seam: `settle(message, authorization) -> dict` and raise
`SettlementForwardError` (funds held) / `SettlementError` (failed).

The adapter:
  - converts a canonical `PaymentAuthorization` into the `(message, authorization)`
    pair the wrapped engine expects (splitting the 65-byte signature into v/r/s);
  - maps the returned dict onto a `SettlementResult`;
  - upholds the canonical contract that `settle` NEVER raises for an on-chain
    rejection — a funds-held partial failure becomes `FUNDS_HELD`, a clean failure
    becomes `FAILED`, both with buyer-safe reasons (internal detail to logs only).

No settlement logic is reimplemented — token, router, fee split, confirmation all
stay in the wrapped engine. Phase 2 rule honoured: contracts/token untouched.
"""

from __future__ import annotations

from unified.models import PaymentAsset
from unified.payment import PaymentAuthorization
from unified.settlement import SettlementResult, SettlementStatus


def _split_signature(signature_hex: str) -> tuple[int, str, str]:
    """Split a 0x 65-byte EIP-712 signature into (v, r, s). Mirrors x402.common."""
    raw = bytes.fromhex(signature_hex[2:] if signature_hex.startswith("0x") else signature_hex)
    if len(raw) != 65:
        raise ValueError("signature must be 65 bytes")
    r, s, v = raw[0:32], raw[32:64], raw[64]
    if v < 27:
        v += 27
    return v, "0x" + r.hex(), "0x" + s.hex()


def authorization_to_engine_inputs(authorization: PaymentAuthorization) -> tuple[dict, dict]:
    """Convert a canonical PaymentAuthorization into (message, authorization) for
    the wrapped engine's `settle`. `message` carries the EIP-3009 fields; the
    authorization dict carries v/r/s (the engine reads only those from it)."""
    v, r, s = _split_signature(authorization.signature)
    message = {
        "from": authorization.from_address,
        "to": authorization.to_address,
        "value": int(authorization.value_units),
        "validAfter": int(authorization.valid_after),
        "validBefore": int(authorization.valid_before),
        "nonce": authorization.nonce,
    }
    auth_dict = {**message, "v": v, "r": r, "s": s}
    return message, auth_dict


class FacilitatorSettlementEngine:
    """Canonical SettlementEngine backed by a GitHub facilitator settlement engine.

    :param engine: an object exposing `settle(message, authorization) -> dict`
        (facilitator.settlement.SettlementEngine or AtomicSettlementEngine).
    :param asset: the settlement asset (canonical tEURC in production).
    """

    def __init__(self, engine, *, asset: PaymentAsset = PaymentAsset.TEURC):
        self._engine = engine
        self._asset = asset

    @property
    def supported_asset(self) -> PaymentAsset:
        return self._asset

    def settle(self, authorization: PaymentAuthorization) -> SettlementResult:
        # Lazy import so this module is importable without pulling web3 unless used.
        from facilitator.settlement import SettlementError, SettlementForwardError

        # Asset guard: never settle an authorization for a different asset than this
        # engine handles (keeps tEURC and apUSD paths from being confused).
        if authorization.asset is not self._asset:
            return SettlementResult(
                status=SettlementStatus.FAILED,
                asset=self._asset,
                amount_units=int(authorization.value_units),
                reason=f"Asset mismatch: authorization is {authorization.asset.value}, engine settles {self._asset.value}.",
            )

        message, auth_dict = authorization_to_engine_inputs(authorization)
        amount = message["value"]
        try:
            res = self._engine.settle(message, auth_dict)
        except SettlementForwardError as exc:
            # relay ok, forward failed -> reconcilable funds-held (no silent loss).
            return SettlementResult(
                status=SettlementStatus.FUNDS_HELD,
                asset=self._asset,
                amount_units=amount,
                net_units=getattr(exc, "net_wei", 0) or 0,
                relay_tx_hash=getattr(exc, "relay_tx_hash", None),
                reason="Funds held: relay confirmed but forward to the service failed; journaled for reconciliation.",
            )
        except SettlementError:
            # clean failure: nothing moved. Buyer-safe reason; detail only in engine logs.
            return SettlementResult(
                status=SettlementStatus.FAILED,
                asset=self._asset,
                amount_units=amount,
                reason="Internal settlement error, please retry later.",
            )
        except Exception:  # noqa: BLE001 — a raw chain error (e.g. an on-chain revert on a
            # replayed nonce during gas estimation) must never escape as an unhandled
            # exception. Surface it as FAILED; nothing moved. Detail stays in logs.
            return SettlementResult(
                status=SettlementStatus.FAILED,
                asset=self._asset,
                amount_units=amount,
                reason="Settlement rejected on-chain (e.g. used authorization).",
            )

        return SettlementResult(
            status=SettlementStatus.CONFIRMED if res.get("confirmed") else SettlementStatus.SUBMITTED,
            asset=self._asset,
            amount_units=amount,
            fee_units=int(res.get("fee_wei", 0)),
            net_units=int(res.get("net_wei", 0)),
            relay_tx_hash=res.get("relay_tx_hash"),
            forward_tx_hash=res.get("forward_tx_hash"),
            reason="Settled.",
            details={"status": res.get("status")},
        )


class AtomicFacilitatorSettlementEngine:
    """Canonical SettlementEngine for the ATOMIC path (H-3).

    Wraps `facilitator.atomic_settlement.AtomicSettlementEngine`, whose settle
    routes through `AgentPayRouter.settlePaymentAtomic` — where the ON-CHAIN
    security boundary lives: `isRelayer(msg.sender)`, `_requireKYA(from)`, the
    derived-nonce resource/seller/fee binding, and receive-with-authorization.

    C-1 preserved: `seller` and `fee_bps` come from THIS server's config (never the
    client), and `resource_hash` is derived from the CANONICAL resource + the
    client salt (`authorization.resource` is set by the pipeline to its own
    resource). The client cannot influence any of them.
    """

    def __init__(self, atomic_engine, *, seller: str, fee_bps: int, router_address: str,
                 asset: PaymentAsset = PaymentAsset.TEURC):
        self._engine = atomic_engine
        self._seller = seller
        self._fee_bps = int(fee_bps)
        self._router_address = router_address
        self._asset = asset

    @property
    def supported_asset(self) -> PaymentAsset:
        return self._asset

    def settle(self, authorization: PaymentAuthorization) -> SettlementResult:
        import router_binding
        from facilitator.settlement import SettlementError

        amount = int(authorization.value_units)

        if authorization.asset is not self._asset:
            return SettlementResult(SettlementStatus.FAILED, self._asset, amount,
                                    reason=f"Asset mismatch: {authorization.asset.value} vs {self._asset.value}.")
        if authorization.mode != "atomic" or not authorization.salt or not authorization.resource:
            return SettlementResult(SettlementStatus.FAILED, self._asset, amount,
                                    reason="Atomic settlement requires an atomic authorization with resource+salt.")
        if authorization.to_address.lower() != str(self._router_address).lower():
            return SettlementResult(SettlementStatus.FAILED, self._asset, amount,
                                    reason="Atomic authorization payee is not the router.")

        try:
            salt_bytes = bytes.fromhex(authorization.salt[2:] if authorization.salt.startswith("0x") else authorization.salt)
            resource_hash = router_binding.resource_hash(authorization.resource, salt_bytes)
            v, r, s = _split_signature(authorization.signature)
        except Exception:  # noqa: BLE001 — malformed inputs are a clean FAILED, nothing moves
            return SettlementResult(SettlementStatus.FAILED, self._asset, amount, reason="Malformed atomic authorization.")

        # seller / feeBps / resourceHash are SERVER-supplied (C-1); the router
        # recomputes the nonce from them on-chain and tEURC reverts if they don't
        # match what the buyer signed.
        message = {
            "from": authorization.from_address,
            "seller": self._seller,
            "value": amount,
            "feeBps": self._fee_bps,
            "validAfter": int(authorization.valid_after),
            "validBefore": int(authorization.valid_before),
            "resourceHash": resource_hash,
        }
        auth_dict = {"v": v, "r": r, "s": s}

        try:
            res = self._engine.settle(message, auth_dict)
        except SettlementError:
            # on-chain reject (non-KYA / not-relayer / bad binding / replay) — nothing moved.
            return SettlementResult(SettlementStatus.FAILED, self._asset, amount,
                                    reason="Atomic settlement rejected on-chain (KYA/relayer/binding/replay).")
        except Exception:  # noqa: BLE001 — any raw chain error must not escape
            return SettlementResult(SettlementStatus.FAILED, self._asset, amount,
                                    reason="Atomic settlement failed on-chain.")

        return SettlementResult(
            status=SettlementStatus.CONFIRMED if res.get("confirmed") else SettlementStatus.SUBMITTED,
            asset=self._asset,
            amount_units=amount,
            fee_units=int(res.get("fee_wei", 0)),
            net_units=int(res.get("net_wei", 0)),
            relay_tx_hash=res.get("relay_tx_hash"),
            forward_tx_hash=res.get("forward_tx_hash"),
            reason="Settled (atomic).",
            details={"status": res.get("status")},
        )
