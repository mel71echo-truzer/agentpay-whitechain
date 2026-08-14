# AgentPay SDK

The buyer-side client for AgentPay on Whitechain. Discover a service, pay for a
resource, get the bytes — the x402 / EIP-3009 handshake, KYA/reputation gating,
replay protection and settlement all happen behind one call.

## Install

The SDK ships inside this repository (it reuses the audited reference client).
From a clone:

```bash
pip install -r requirements.txt
```

Then `from agentpay_sdk import AgentPayClient`.

## Integrate in 10 minutes

```python
from agentpay_sdk import AgentPayClient, SpendLedger

client = AgentPayClient(
    private_key="0x…",                    # your agent's key — signs, never pays gas
    registry_url="http://localhost:8000", # a Capability Registry to discover through
    chain_id=2625,                        # Whitechain testnet
)

# One call: discover a verified provider and buy a resource from it.
result = client.purchase("image-generation", "/photo/kyiv-lavra")

with open("kyiv-lavra.png", "wb") as f:
    f.write(result.content)

print("tier:", result.reputation_tier, "fee:", result.fee_teurc, "tx:", result.relay_tx_hash)
```

### Cap total spend

```python
ledger = SpendLedger(path=".spend.json", max_spend_wei=1_000_000)  # 1.0 tEURC
client = AgentPayClient(private_key="0x…", registry_url="http://localhost:8000",
                        chain_id=2625, spend_ledger=ledger)
# Each purchase checks the limit before signing; over-limit raises SpendLimitExceeded.
```

## API

| Call | Does |
|---|---|
| `AgentPayClient(private_key, registry_url, chain_id, spend_ledger=None)` | Bind one agent identity. |
| `.purchase(capability_type, resource_path, *, prefer_owner=None)` | Resolve a verified provider and buy `resource_path` (e.g. `"/photo/x"`). Pins the provider's signed `pay_to`. |
| `.discover(capability_type=None)` | Raw registry records (unverified). |
| `.resolve(capability_type, *, prefer_owner=None)` | One signature-verified provider record. |
| `.pay_and_fetch(url, *, expected_pay_to=None)` | Drive the 402→sign→settle flow against an explicit URL. |

Returns a `PurchaseResult`: `.content`, `.content_type`, `.reputation_tier`,
`.fee_teurc`, `.relay_tx_hash`, `.already_had_it`.

## What it does for you

1. `GET` the resource → the provider answers **402 Payment Required** with the
   price, the payee, and (atomic mode) the seller + fee split.
2. Signs an **EIP-3009** authorization off-chain — no transaction, no gas, no
   waiting for a block. The nonce binds the signature to *this* resource.
3. Re-sends with the signature. The **facilitator** verifies KYA (WB Soul),
   reputation tier, the signature, the amount and on-chain replay state, then
   relays the payment. You get the bytes back.

## Errors

- `PaymentFailed` — the facilitator rejected the authorization (not KYA-verified,
  insufficient reputation, bad signature, `payTo` mismatch, …).
- `CapabilityNotFound` — no signature-verified provider of that type.
- `SpendLimitExceeded` — the purchase would exceed the ledger's cap.

## Exact runnable example

See [`examples/quickstart.py`](../examples/quickstart.py) — it stands up a local
provider + facilitator and buys a photo end-to-end, no testnet needed.
