"""unified/telemetry.py — minimal structured telemetry for one payment request.

Lets an operator reconstruct a single request end-to-end without a tracing stack.
It carries ONLY non-sensitive fields — by construction there is no place to put a
private key, a signature, or a raw authorization secret, so those cannot leak
through it. Emit via the existing logging (logging_setup json/text); never print
secrets or `.env`.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


def new_request_id() -> str:
    return uuid.uuid4().hex


# Keys that must NEVER appear in telemetry, guarded by _assert_no_secrets.
# Substrings for long tokens; exact matches for the 1-letter EIP-712 sig parts
# (so they don't spuriously match legit fields like "ser[v]ice_id").
_FORBIDDEN_SUBSTR = ("signature", "private_key", "privatekey", "secret", "mnemonic", "authorization")
_FORBIDDEN_EXACT = {"v", "r", "s"}


@dataclass
class PaymentTelemetry:
    """One request's trace. All fields are safe to log."""

    request_id: str = field(default_factory=new_request_id)
    provider_id: Optional[str] = None
    service_id: Optional[str] = None
    selected_score: Optional[float] = None
    trust_decision: Optional[str] = None       # "allow" / "deny:not-kya" / ...
    payment_amount: Optional[int] = None        # minimal units
    asset: Optional[str] = None                 # "tEURC" / "apUSD"
    settlement_status: Optional[str] = None      # confirmed / submitted / funds_held / failed / not_attempted
    tx_hash: Optional[str] = None
    failure_reason: Optional[str] = None

    def to_log_dict(self) -> dict:
        """Dict for structured logging. Asserts no secret ever slipped in."""
        d = {k: v for k, v in asdict(self).items() if v is not None}
        _assert_no_secrets(d)
        return d


def _assert_no_secrets(d: dict) -> None:
    for key in d:
        k = key.lower()
        if k in _FORBIDDEN_EXACT or any(bad in k for bad in _FORBIDDEN_SUBSTR):
            raise AssertionError(f"telemetry must not carry a secret-like field: {key!r}")
