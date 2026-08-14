"""AgentPay SDK — the client an external developer integrates against.

Ten-minute integration:

    from agentpay_sdk import AgentPayClient

    client = AgentPayClient(
        private_key="0x…",                       # the buyer agent's key (signs, never pays gas)
        registry_url="http://provider:8000",     # where to discover services
        chain_id=2625,                            # Whitechain testnet
    )

    # Discover a service and buy a resource from it — the SDK handles the whole
    # x402 / EIP-3009 dance (402 → off-chain signature → facilitator relay).
    result = client.purchase("image-generation", "/photo/kyiv-lavra")
    open("kyiv.png", "wb").write(result.content)

That's it. KYA, reputation, signature, replay-protection and settlement all
happen behind `purchase()`. See agentpay_sdk/README.md and examples/quickstart.py.
"""

from agentpay_sdk.client import AgentPayClient
from agent_client import (
    CapabilityNotFound,
    PaymentFailed,
    PurchaseResult,
    SpendLedger,
    SpendLimitExceeded,
)

__all__ = [
    "AgentPayClient",
    "PurchaseResult",
    "PaymentFailed",
    "CapabilityNotFound",
    "SpendLimitExceeded",
    "SpendLedger",
]

__version__ = "0.1.0"
