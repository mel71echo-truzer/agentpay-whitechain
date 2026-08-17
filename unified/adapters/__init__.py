"""unified.adapters — Phase 2: wrap existing components into canonical interfaces.

Each adapter DELEGATES to the existing, working implementation (it does not
rewrite it) and exposes the canonical `unified` interface. This is the seam that
lets the local marketplace layer and the GitHub trust/settlement layer meet
without touching either's code, the token, the contracts, or the public wire
format.

  - StandardX402Adapter      (x402.py)       — standard x402 X-PAYMENT <-> PaymentAuthorization
  - service_from_* mappers   (discovery.py)  — local registry / GitHub capability record -> canonical Service
  - FacilitatorTrustGate     (trust.py)      — wraps facilitator.identity + facilitator.policy
  - FacilitatorSettlementEngine (settlement.py) — wraps facilitator.settlement / atomic_settlement

Import the chain-dependent adapters (trust, settlement) lazily from where you use
them, so importing `unified.adapters` for the pure ones (x402, discovery) never
drags in web3.
"""

from unified.adapters.x402 import StandardX402Adapter
from unified.adapters.discovery import (
    service_from_capability_record,
    service_from_local_registry,
)

__all__ = [
    "StandardX402Adapter",
    "service_from_local_registry",
    "service_from_capability_record",
]
