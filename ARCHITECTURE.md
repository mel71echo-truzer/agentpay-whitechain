# ARCHITECTURE.md — how AgentPay on Whitechain works

AgentPay is a **trust layer for agent-to-agent payments**: identity (KYA) +
reputation + policy gating a payment, with the payment itself as the first
service wired behind that gate. This document maps every component, the data
flow of a purchase, and the two settlement backends.

> Scope note: this is a testnet PoC with deliberately-documented limits (mock WB
> Soul on testnet, single-node reputation store, no external audit yet). See
> [`SECURITY.md`](SECURITY.md) for the threat model and what is / isn't covered.

---

## 1. The one-paragraph version

A buyer agent signs an **EIP-3009** payment authorization *off-chain* (no tx, no
gas, no waiting for a block). It sends that signature to a seller. Before any
money moves, a **facilitator** checks, on-chain, that the payer has a **verified
WB Soul** (Know-Your-Agent identity) and, for premium resources, a minimum
**reputation tier** (from soul-bound tokens). Only then does the facilitator
relay the payment on-chain (paying gas in WBT) and release the resource. Without
a verified WB Soul, the system refuses to move money — regardless of a correct
signature or amount. **That gate is the product.**

---

## 2. Component map

### On-chain (Solidity, `contracts/`)

| Contract | Responsibility |
|---|---|
| `tEURC.sol` | ERC-20 (6 dp) payment currency. EIP-2612 permit + full EIP-3009 (`transfer`/`receive`/`cancelWithAuthorization`). Owner-only faucet mint. Custom errors, `Ownable2Step`. |
| `AgentPayRouter.sol` | **Atomic** settlement: pull + fee-split + payout in one tx. Every fund-moving parameter (seller, amount, fee, resource) is bound into the buyer's signed nonce (finding **C-1** closed). Relayer allow-list, `ReentrancyGuard`, `SafeERC20`, fee cap, `Ownable2Step`. |
| `interfaces/` | `ISoulRegistry` / `ISoulAttributeRegistry` / `ISoulBoundTokenRegistry` — copied 1:1 from WhiteBIT's real WB Soul repo. The mock↔real swap seam. |
| `mocks/` | `MockSoulRegistry`, `MockSoulAttribute`, `MockSoulBoundTokenCollection`, `MockRouterKYA` — local WB Soul stand-ins (WhiteBIT hasn't published WB Soul on testnet). |

### Off-chain (Python)

```
                         ┌──────────────────────────────────────────────┐
   agentpay_sdk          │  service_provider/server.py (FastAPI)         │
   AgentPayClient  ──────▶  GET /photo/{n} → 402 ; POST → settle         │
   (buyer side)          │  /registry/* (discovery) ; /balance ; /admin/*│
                         └───────────────┬──────────────────────────────┘
                                         │ verify_and_settle()
                                         ▼
          facilitator/whitechain_facilitator.py  (thin orchestrator)
          identity → policy → payment → settlement → event → response
            │          │         │          │            │
   identity.py    policy.py  payment.py  settlement.py  events.py
   (WB Soul       (allow/    (EIP-712/   (legacy relay+ (payment-flow
    reader +       deny)      EIP-3009    forward)  OR   journal)
    reputation)              validation)  atomic_settlement.py
                                         (router, one tx)
                         store.py  — SQLite: agent_stats / events / capabilities
                         capability.py — service-discovery registry
                         router_kya_adapter.py — pluggable router KYA source
```

Supporting: `chain.py` (web3 deploy/connect via Hardhat artifacts), `config.py`
(all settings from env + `validate_startup`), `money.py` (integer-wei math —
**no float in money paths**), `router_binding.py` (shared client↔router nonce
derivation), `registry_auth.py` (signed capability records), `logging_setup.py`
(text/json structured logging), `agent_client.py` (the reference client the SDK
wraps), `author/agent.py` (a Claude tool-use buyer).

### Single-responsibility facilitator

`whitechain_facilitator.py` holds no business logic — it just sequences the
modules. Each module is independently testable and swappable; the orchestrator
doesn't know which settlement backend is active (both share the seam
`validate_authorization(...) → message` and `settle(message, auth) → result`).

---

## 3. Purchase data flow

```
Buyer (agent)                         Seller (AI Service Provider)         Facilitator
  │  GET /photo/kyiv-lavra                    │                                 │
  ├──────────────────────────────────────────▶                                 │
  │        402 Payment Required               │                                 │
  │  {payTo, price_wei, resource,             │                                 │
  │   min_reputation_tier, settlement_mode,   │                                 │
  │   (atomic: seller, fee_bps)}              │                                 │
  ◀──────────────────────────────────────────┤                                 │
  │                                                                             │
  │ sign EIP-3009 off-chain:                                                    │
  │   legacy → TransferWithAuthorization, to=facilitator,                       │
  │            nonce=keccak(resource‖salt)                                      │
  │   atomic → ReceiveWithAuthorization, to=router,                            │
  │            nonce=keccak(seller,feeBps,resourceHash)  (C-1 binding)          │
  │                                                                             │
  │  POST /photo/kyiv-lavra {authorization, resource, resource_salt}            │
  ├──────────────────────────────────────────▶  verify_and_settle() ──────────▶│
  │                                             1. identity: soulOf≠0 & IsVerified
  │                                             2. policy: reputation tier ≥ min
  │                                             3. payment: resource-binding, EIP-712
  │                                                recovery, validAfter/Before,
  │                                                on-chain replay, amount, payee
  │                                             4. settlement: relay on-chain ───▶ (WBT gas)
  │                                             5. event: journal; 6. record stats
  ◀──────────────────────────────────────────  200 + bytes + X-Settlement       │
  │   (or 402 + reason: "not KYA-verified" / "insufficient reputation" / …)      │
```

Resource release happens on **SettlementConfirmed** (after the relay receipt is
mined) when `WAIT_FOR_CONFIRMATION=true` — closing the off-chain race where
content could be handed out before the payment lands.

---

## 4. Two settlement backends (`SETTLEMENT_MODE`)

| | `atomic` (default) | `legacy` |
|---|---|---|
| Mechanism | `AgentPayRouter.settlePaymentAtomic` — receive + fee-split + payout in **one** tx | facilitator receives full amount, forwards net in a **second** tx |
| Funds-held window | **none** (all-or-nothing) | exists; a relay-ok/forward-failed case is journaled as *funds held* (`GET /admin/held-settlements`), no silent loss, no auto-retry |
| Fund destination integrity | seller/amount/fee/resource bound into the signed nonce; a relayer cannot redirect (C-1 closed) | facilitator custodies briefly, then forwards per config |
| Client signature | `ReceiveWithAuthorization` → router | `TransferWithAuthorization` → facilitator |

Both are exercised by tests; the orchestrator and the SDK are identical across
modes.

---

## 5. Identity & reputation

- **KYA gate** (`identity.py`): reads WB Soul — `soulOf(payer) ≠ 0` **and** the
  IsVerified attribute is true. On testnet this is `MockSoulRegistry`; the real
  swap is `USE_MOCK_SOUL=false` + real addresses (no code change).
- **Reputation** (`reputation.py`): an explicit, unit-tested score (0–100) from
  behavioral counters (completed payments, disputes, refunds, tenure, SBT bonus,
  fraud flags) → tier 0/1/2. A **cold-start guard** ignores the formula for a
  brand-new agent (which would otherwise score 45) and uses only its
  SBT-attested tier. Effective tier = `max(SBT-attested, behavioral)`.
- **Router KYA** (`router_kya_adapter.py`): the atomic router reads
  `isVerified(uint256)`, which the attribute-based WB Soul doesn't expose — so
  the router uses a pluggable KYA source (mock today; a real WB Soul adapter is
  a documented TODO against the confirmed schema).

Known limit: the behavioral counters live in a local SQLite table this process
writes, so reputation is trust-on-first-use and sybil-able. Trust is anchored by
the on-chain KYA+SBT layer. See [`SECURITY.md`](SECURITY.md) and
[`docs/audit/03-reputation-threat-model.md`](docs/audit/03-reputation-threat-model.md).

---

## 6. Money discipline

All money paths use **integer wei** (minimal tEURC units); there is no `float`
anywhere money is computed (`money.py`, decision documented across the code as
"рішення №4"). Human-readable `*_teurc` strings exist only for display. The fee
is floored and the sub-unit remainder favors the seller — identically in
`settlement.py` and `AgentPayRouter._split`.

---

## 7. Network & deployment

| | |
|---|---|
| Chain | Whitechain testnet, Chain ID `2625` (`0xa41`) |
| RPC | `https://rpc-testnet.whitechain.io` |
| Explorer | `https://testnet.whitechain.io` |
| Gas token | WBT (pays for transactions; tEURC is the payment currency) |
| Compiler | solc `0.8.24` (cancun), fetched + sha256-verified by `scripts/bootstrap_solc.sh` |

`NETWORK=local` runs everything against an in-memory EVM (demo + tests deploy
fresh each run). `NETWORK=whitechain_testnet` reads addresses/keys from `.env`.
See [`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md).

---

## 8. What's deliberately out of scope

Microservice split, an event bus, multiple provider adapters, an on-chain
capability registry, payment channels/batching, and — the big one — a **real WB
Soul integration on testnet** (WhiteBIT hasn't published testnet addresses; a
router KYA adapter over the real attribute schema is the concrete next
deliverable). See README "Out of scope" for the full list.
