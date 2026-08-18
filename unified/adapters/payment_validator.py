"""UnifiedPaymentValidator — off-chain validation of a standard-x402 payment,
BEFORE the TrustGate and BEFORE any settlement.

This closes a real gap found in the Phase 5 audit: the unified resource server was
settling whatever the buyer signed, without checking that the amount, payee, asset,
time window and signature match what the service expects. Without this, a buyer
could sign a smaller amount or a different `to` and still be served (the seller
underpaid). None of this rewrites the facilitator — it validates the canonical
`PaymentAuthorization` at the unified layer, mirroring the checks
facilitator/payment.py already does for the GitHub wire format.

Enforcement split (source of truth):
  - amount / payTo / asset / window  → HERE, off-chain, so a bad request is
    rejected before money or gas moves;
  - signature authenticity           → HERE (EIP-712 recover) AND on-chain (tEURC
    reverts on a bad signature) — defence in depth;
  - nonce replay                     → on-chain, tEURC.authorizationState is the
    source of truth (transferWithAuthorization reverts on a used nonce). An
    optional off-chain pre-check is available when a token handle is supplied.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

import router_binding
from unified.models import PaymentAsset
from unified.payment import PaymentAuthorization

# EIP-712 type matching tEURC.TransferWithAuthorization (the standard x402 "exact").
TRANSFER_AUTH_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


@dataclass
class ValidationResult:
    ok: bool
    reason: str = "ok"


class UnifiedPaymentValidator:
    """Validates a `PaymentAuthorization` against what the service expects."""

    def __init__(
        self,
        *,
        chain_id: int,
        asset_address: str,
        expected_asset: PaymentAsset = PaymentAsset.TEURC,
        token_name: str = "Test EURC",
        token_version: str = "1",
        token=None,   # optional web3 contract handle for an off-chain replay pre-check
        settlement_mode: str = "legacy",
        seller: Optional[str] = None,
        fee_bps: int = 0,
        router_address: Optional[str] = None,
    ):
        self.chain_id = int(chain_id)
        self.asset_address = asset_address
        self.expected_asset = expected_asset
        self.token_name = token_name
        self.token_version = token_version
        self.token = token
        # H-2: atomic resource binding. In atomic mode the authorization is a
        # ReceiveWithAuthorization to the router with a DERIVED nonce that commits
        # to (seller, feeBps, keccak(resource‖salt)). The validator re-derives that
        # nonce from the CANONICAL resource + the server's OWN seller/fee_bps, so a
        # substituted resource/seller/feeBps/nonce fails.
        self.settlement_mode = settlement_mode.strip().lower()
        self.seller = seller
        self.fee_bps = int(fee_bps)
        self.router_address = router_address
        if self.settlement_mode == "atomic" and not (seller and router_address):
            raise ValueError("atomic settlement_mode requires seller and router_address.")

    def _domain(self) -> dict:
        return {
            "name": self.token_name,
            "version": self.token_version,
            "chainId": self.chain_id,
            "verifyingContract": Web3.to_checksum_address(self.asset_address),
        }

    def validate(
        self,
        auth: PaymentAuthorization,
        *,
        expected_amount_units: int,
        expected_pay_to: str,
        expected_network: Optional[str] = None,
        expected_resource: Optional[str] = None,
        now: Optional[int] = None,
    ) -> ValidationResult:
        now = int(now if now is not None else time.time())
        atomic = self.settlement_mode == "atomic"

        # mode consistency: an atomic server must get an atomic authorization, and
        # a legacy server must not be handed one (no silent downgrade/upgrade).
        if atomic and auth.mode != "atomic":
            return ValidationResult(False, "expected an atomic authorization")
        if not atomic and auth.mode == "atomic":
            return ValidationResult(False, "atomic authorization but server is not in atomic mode")

        # amount / payTo / asset / network — the request must match the service.
        if int(auth.value_units) != int(expected_amount_units):
            return ValidationResult(False, "amount mismatch")
        if auth.to_address.lower() != str(expected_pay_to).lower():
            return ValidationResult(False, "payTo mismatch")
        if auth.asset is not self.expected_asset:
            return ValidationResult(False, "asset mismatch")
        if expected_network is not None and auth.network != expected_network:
            return ValidationResult(False, "network mismatch")

        # time window (tEURC also enforces this on-chain; reject early to save gas).
        if not (int(auth.valid_after) < now < int(auth.valid_before)):
            return ValidationResult(False, "authorization expired or not yet valid")

        try:
            nonce_bytes = bytes.fromhex(auth.nonce[2:] if auth.nonce.startswith("0x") else auth.nonce)
        except (ValueError, AttributeError):
            return ValidationResult(False, "invalid nonce")

        if atomic:
            # H-2 RESOURCE BINDING: re-derive the nonce from the CANONICAL resource +
            # client salt + the SERVER's own seller/fee_bps, and require the signed
            # nonce to equal it. Any substituted resource/seller/feeBps/nonce fails
            # here (the resourceHash is server-derived, never trusted from the client).
            if expected_resource is None or not auth.salt:
                return ValidationResult(False, "atomic authorization missing resource/salt")
            if auth.to_address.lower() != str(self.router_address).lower():
                return ValidationResult(False, "atomic payTo must be the router")
            try:
                salt_bytes = bytes.fromhex(auth.salt[2:] if auth.salt.startswith("0x") else auth.salt)
                rhash = router_binding.resource_hash(expected_resource, salt_bytes)
                expected_nonce = router_binding.compute_router_nonce(self.seller, self.fee_bps, rhash)
            except Exception:  # noqa: BLE001 — malformed salt/resource is a clean reject
                return ValidationResult(False, "invalid resource binding inputs")
            if nonce_bytes != expected_nonce:
                return ValidationResult(False, "resource/binding mismatch (derived nonce)")
            auth_types = router_binding.RECEIVE_AUTH_TYPES
        else:
            auth_types = TRANSFER_AUTH_TYPES

        # signature authenticity (EIP-712 recover == from) over the correct typehash.
        try:
            message = {
                "from": Web3.to_checksum_address(auth.from_address),
                "to": Web3.to_checksum_address(auth.to_address),
                "value": int(auth.value_units),
                "validAfter": int(auth.valid_after),
                "validBefore": int(auth.valid_before),
                "nonce": nonce_bytes,
            }
            recovered = Account.recover_message(
                encode_typed_data(self._domain(), auth_types, message), signature=auth.signature
            )
        except Exception:  # noqa: BLE001 — any malformed input is a clean reject
            return ValidationResult(False, "invalid signature")
        if recovered.lower() != auth.from_address.lower():
            return ValidationResult(False, "invalid signature (signer mismatch)")

        # optional off-chain replay pre-check (on-chain remains the source of truth).
        if self.token is not None:
            try:
                if self.token.functions.authorizationState(
                    Web3.to_checksum_address(auth.from_address), nonce_bytes
                ).call():
                    return ValidationResult(False, "authorization already used (replay)")
            except Exception:  # noqa: BLE001 — if the pre-check can't run, on-chain still guards
                pass

        return ValidationResult(True, "ok")
