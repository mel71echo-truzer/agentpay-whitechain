# INTEGRATION.md — connecting to AgentPay

Two audiences: a **buyer** (an agent that pays for resources) and a **seller/
provider** (a service that charges for them). Both sit either side of the
facilitator, which does the KYA/reputation gating and settlement.

- Prerequisites & environment: below.
- Buyer integration (the common case): [§2](#2-buyer-integration).
- Provider integration: [§3](#3-provider-integration).
- Running your own facilitator: [§4](#4-running-a-facilitator).
- Config reference: [§5](#5-configuration-reference).

---

## 1. Prerequisites

- Python 3.11+, Node 18+.
- `pip install -r requirements.txt` (runtime) and, for the contracts,
  `npm install` + `npm run compile` (the latter bootstraps a sha256-verified
  solc — works on restricted networks; see [`README`](README.md)).
- For testnet: a wallet funded with faucet **WBT** (gas) — see
  [`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md). Never commit keys; use `.env`.

Everything runs against a local in-memory chain by default
(`NETWORK=local`) — no testnet, no keys — so you can integrate and test before
touching Whitechain.

---

## 2. Buyer integration

Use the [`agentpay_sdk`](agentpay_sdk/README.md) package. The buyer's key only
ever **signs** authorizations off-chain — it never submits a transaction or pays
gas.

```python
from agentpay_sdk import AgentPayClient, PaymentFailed, SpendLedger

client = AgentPayClient(
    private_key="0x…",                     # buyer agent's key
    registry_url="http://provider:8000",   # a Capability Registry to discover through
    chain_id=2625,                         # Whitechain testnet (0xa41)
    spend_ledger=SpendLedger(max_spend_wei=1_000_000),  # optional per-task cap (1.0 tEURC)
)

try:
    result = client.purchase("image-generation", "/photo/kyiv-lavra")
    open("kyiv.png", "wb").write(result.content)
    print(result.reputation_tier, result.fee_teurc, result.relay_tx_hash)
except PaymentFailed as exc:
    print("refused:", exc)   # not KYA-verified / insufficient reputation / payTo mismatch / …
```

`purchase()` resolves a **signature-verified** provider from the registry, pins
that provider's signed `pay_to`, and runs the full 402 → EIP-3009 sign → settle
flow. If you already have the resource URL and payee, call
`client.pay_and_fetch(url, expected_pay_to=…)` directly.

**What you must handle:** `PaymentFailed` (rejection), `CapabilityNotFound` (no
verified provider), `SpendLimitExceeded` (over your cap). Runnable end-to-end
example: `python examples/quickstart.py`.

### To be payable, your agent needs

1. A **verified WB Soul** (KYA). On testnet with mocks, the contract owner seeds
   it; with real WB Soul, the wallet must pass WhiteBIT KYC.
2. Enough **tEURC** at the signing wallet.
3. For premium resources, the required **reputation tier** (SBT-attested or
   behavioral).

---

## 3. Provider integration

A provider advertises a capability in the registry with a **signed** record
(`registry_auth.sign_registration`) — the registry and buyers verify the
signature and that `id == signer`, and the buyer pins `pay_to`. So no one can
register or spoof a capability on your behalf.

```python
import registry_auth, requests

record = {
    "id": PROVIDER_ADDRESS,                 # must equal the signer
    "capability_type": "image-generation",
    "provider_url": "http://your-service:8000",
    "pay_to": FACILITATOR_OR_ROUTER_ADDRESS,# where funds actually land
    "price_wei": 20_000,                    # 0.02 tEURC
    "min_reputation_tier": 0,
    "active": True,
}
signature = registry_auth.sign_registration(record, PROVIDER_PRIVATE_KEY)
requests.post("http://registry:8000/registry/register", json={**record, "signature": signature})
```

Your HTTP surface (see `service_provider/server.py` for a reference
implementation) must:

- answer **402 Payment Required** for an unpaid resource, with the x402 body
  (`payTo`, `price_wei`, `resource`, `min_reputation_tier`, `settlement_mode`,
  and in atomic mode `seller` + `fee_bps`);
- on a paid **POST**, call `facilitator.verify_and_settle(authorization,
  resource, resource_salt, price_wei, min_reputation_tier)` and release the
  bytes only when it returns `valid: True`;
- never `500` on untrusted input (validate → `400`); whitelist resource names
  against path traversal.

---

## 4. Running a facilitator

The facilitator is the party that relays payments (pays WBT gas) and enforces
the gate. Configure it via `.env` and start the reference server:

```bash
cp .env.example .env    # fill in per §5
python -m service_provider.server
```

On startup it: configures logging (`LOG_FORMAT`/`LOG_LEVEL`), runs
`config.require_valid_startup()` (fails fast with a clear list of any missing
settings), and asserts the RPC really is the configured chain. Reconciliation of
any *funds-held* legacy settlements is operator-driven via
`GET /admin/held-settlements` (behind `ADMIN_API_TOKEN`).

---

## 5. Configuration reference

All settings come from `.env` (see [`.env.example`](.env.example) for the full,
commented list). The ones you'll set most:

| Key | Meaning |
|---|---|
| `NETWORK` | `local` (in-memory) or `whitechain_testnet` |
| `WHITECHAIN_TESTNET_RPC` / `CHAIN_ID` | `https://rpc-testnet.whitechain.io` / `2625` |
| `USE_MOCK_SOUL` | `true` (mock WB Soul) or `false` (real addresses) |
| `SETTLEMENT_MODE` | `atomic` (router, one tx) or `legacy` (relay+forward) |
| `TEURC_ADDRESS`, `SOUL_*`, `ROUTER_*` | contract addresses (deploy prints them) |
| `*_WALLET_PRIVATE_KEY` | keys — **env only, never committed** |
| `FACILITATOR_FEE_BPS` | fee in basis points (50 = 0.5%) |
| `RESOURCE_PRICE_TEURC`, `PREMIUM_*` | human prices → converted to wei at load |
| `WAIT_FOR_CONFIRMATION` | release on SettlementConfirmed (default true) |
| `ADMIN_API_TOKEN` | enables `/admin/*` (empty = disabled, not open) |
| `LOG_FORMAT` / `LOG_LEVEL` | `text`/`json`, `INFO`/… |

Switching from mocks to real WB Soul, or from testnet back to local, is a
`.env` edit — never a code change.
