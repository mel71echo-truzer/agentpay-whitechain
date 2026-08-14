# PLAN.md — AgentPay on Whitechain → buyer-grade

> **Phase 0 deliverable.** Architecture map of the *current* codebase + an
> honest gap analysis of what stands between today's state and a state a large
> exchange (WhiteBIT) can buy / license: mainnet-grade, externally-auditable
> contracts, a live testnet demo, and hand-off documentation.
>
> **This document is a plan, not a claim of completion.** Where the code
> already meets a phase's bar, it says so and moves on — the project is far more
> mature than a "prototype" label implies. Where it does not, it says exactly
> what is missing and why.
>
> Baseline on this branch (`claude/agentpay-whitechain-mainnet-czlga0`),
> re-verified before writing this: **`npx hardhat test` → 23 passing**,
> **`python -m pytest` → 116 passed**, contracts compile under `solc 0.8.24`
> (cancun). Green.

---

## 1. Current state — architecture map

### 1.1 What the system does

Two AI agents trade a service (a photo). The buyer never sends its own
transaction: it signs an **EIP-3009** authorization off-chain; a **facilitator**
relays it on-chain (paying gas in WBT) — but only after an on-chain **KYA gate**
(WhiteBIT WB Soul identity) and a **reputation gate** (SBT-derived tier) pass.
This trust layer — identity + reputation + policy gating a payment — is the
product; the payment itself is one service wired behind it.

### 1.2 Contracts (`contracts/`)

| Contract | Role | State |
|---|---|---|
| `tEURC.sol` | ERC-20 (6 dp) + EIP-2612 permit + EIP-3009 transfer/receiveWithAuthorization. The payment currency. | Works, tested (23 Solidity tests). Uses `require`-strings, no custom errors, no `cancelAuthorization`. |
| `AgentPayRouter.sol` | Atomic settlement: receive + fee-split + payout in **one** tx. Every fund-moving param bound to the buyer's signature via a derived nonce (finding **C-1** closed). | Audit-shaped already: OZ `ReentrancyGuard` + `Ownable` + `SafeERC20`, custom errors, events, NatSpec, relayer allow-list, `MAX_FEE_BPS` cap. |
| `interfaces/` | `ISoulRegistry` / `ISoulAttributeRegistry` / `ISoulBoundTokenRegistry` etc., copied 1:1 from WhiteBIT's real `soul-ecosystem-contracts`. | The address-swap seam: mock ↔ real WB Soul is a config change, not a rewrite. |
| `mocks/` | `MockSoulRegistry`, `MockSoulAttribute`, `MockSoulBoundTokenCollection`, `MockRouterKYA`. Local WB Soul stand-ins. | Used because WhiteBIT has **not** published WB Soul on testnet (see §4). |

### 1.3 Off-chain services (Python)

```
agent_client.py ──(1) GET → 402 ──▶ service_provider/server.py  (FastAPI seller + registry)
   │  signs EIP-3009 off-chain                       │
   └──(2) POST auth+resource+salt ──────────────────▶│ facilitator.verify_and_settle()
                                                      ▼
                              facilitator/  (thin orchestrator + single-responsibility modules)
                              ├─ whitechain_facilitator.py  identity→policy→payment→settlement→event→response
                              ├─ identity.py     only reader of WB Soul (soulOf + IsVerified + SBT) + reputation
                              ├─ reputation.py   explicit score/tier formula (unit-tested)
                              ├─ policy.py       allow/deny from identity + resource reqs
                              ├─ payment.py      off-chain EIP-712/EIP-3009 validation (+ atomic binding)
                              ├─ settlement.py   legacy: relay + forward fee (custody window → journal)
                              ├─ atomic_settlement.py  atomic: settlePaymentAtomic (one tx, no custody window)
                              ├─ capability.py   service-discovery registry
                              ├─ events.py       payment-flow event journal
                              └─ store.py        SQLite: agent_stats / events / capabilities (versioned schema)
```

Supporting modules: `chain.py` (web3 deploy/connect via Hardhat artifacts),
`config.py` (all settings from `.env`), `money.py` (integer-wei arithmetic, no
float in money paths), `router_binding.py` (shared nonce derivation, client ↔
router), `registry_auth.py` (signed capability records), `wallets/`,
`author/agent.py` (Claude tool-use buyer), `scripts/demo.py` (end-to-end).

### 1.4 Two settlement backends (`SETTLEMENT_MODE`)

- **`atomic`** (current default): `AgentPayRouter.settlePaymentAtomic` — one tx,
  no funds-held window, seller/amount/fee/resource bound to signature.
- **`legacy`**: facilitator receives full payment, forwards net in a 2nd tx;
  partial failure → explicit *funds-held* journal + `/admin/held-settlements`
  (no silent loss, no auto-retry).

### 1.5 What already ships that most PoCs don't

Live testnet deployment with real addresses (`TESTNET_DEPLOYMENT.md`), a
multi-OS/multi-Python CI matrix, three internal audit passes
(`AUDIT_REPORT.md`, `SECURITY_AUDIT_2026-08.md`, `SECURITY_REVIEW.md`,
`docs/audit/`), integer-wei money discipline, replay/overpayment/spend-limit
protections with attack tests, signed service discovery, and admin-gated
reconciliation endpoints.

---

## 2. Gap analysis — what's missing for buyer-grade

Legend: **✅ done** · **◐ partial** · **❌ missing**

| # | Area | Phase | Status | Gap to close |
|---|---|---|---|---|
| G1 | Whitechain testnet config + one-command deploy | 1 | ✅ | Config is env-driven; deploy script exists; **already deployed live**. Only real open item is external (WB Soul testnet addresses — §4). |
| G2 | Reproducible compile from a fresh clone | 1 | ◐ | `solc` download host is egress-blocked; today it needs a manual fetch-and-verify. Ship a **bootstrap script** that fetches solc from the mirror and checks its sha256, so `compile` works on a clean checkout. |
| G3 | `AgentPayRouter` audit hardening | 2 | ✅ | Reentrancy guard, CEI, access control, custom errors, events, NatSpec, fee cap — all present. Add `Ownable2Step` (see G4). |
| G4 | `tEURC` audit hardening | 2 | ❌ | Convert `require`-strings → **custom errors**; add EIP-3009 **`cancelAuthorization`** (spec completeness); expand **NatSpec**; adopt **`Ownable2Step`** (both contracts) so a fat-fingered ownership transfer can't brick the treasury/mint authority. |
| G5 | Static analysis (Slither) | 2 | ❌ | Not run. Install Slither, run it, fix real findings, document accepted ones with rationale. |
| G6 | Coverage report >90% | 3 | ◐ | 116 pytest + 23 Solidity + attack tests (double-pay, overpayment, replay) exist, but **no coverage tooling/report**. Add `pytest-cov` + Solidity coverage, generate reports, close gaps to >90%. |
| G7 | Clean SDK / client package | 4 | ◐ | Client logic lives in root `agent_client.py`. Extract a **packaged, installable SDK** (`agentpay_sdk/`) with a stable public API + a **10-minute integration example** a third-party dev can copy. |
| G8 | Facilitator production polish | 4 | ◐ | Config, error handling, input validation, admin auth all present. Improve **structured logging** (JSON/leveled, no secret leakage) and add **startup config validation** with clear failure messages. |
| G9 | Hand-off documentation set | 5 | ❌ | README is strong but PoC-framed. Add the four named docs: **ARCHITECTURE.md**, **INTEGRATION.md**, **SECURITY.md** (threat model + what's covered + audit status), and consolidate scattered audit notes into a coherent story. |
| G10 | Reproducible demo doc | 6 | ❌ | `scripts/demo.py` runs end-to-end, but there's no **DEMO.md** with exact copy-paste commands for a testnet recording. |

Nothing in this table requires reworking the core payment path — it is correct
and tested. The work is hardening, tooling, packaging, and documentation.

---

## 3. Execution plan (phases 1–6)

Each phase = its own commit(s) with a clear message; **all tests run and green
before moving on**; secrets only via `.env`/env vars.

- **Phase 1 — testnet portability (G1, G2).** Confirm env-driven network config;
  add `scripts/bootstrap_solc.sh` (fetch + sha256-verify solc from the mirror)
  so a clean clone compiles; document it. *Test gate: compile + both suites green.*
- **Phase 2 — contract hardening (G3, G4, G5).** `tEURC` custom errors +
  `cancelAuthorization` + NatSpec; `Ownable2Step` on both contracts; run Slither,
  fix/document. Update Solidity tests for new error selectors + cancel path.
  *Test gate: `hardhat test` green, Slither triaged.*
- **Phase 3 — coverage (G6).** Add `pytest-cov` + Solidity coverage; generate
  reports; raise coverage to >90%; add any missing attack tests surfaced by the
  report. *Test gate: both suites green + coverage report committed.*
- **Phase 4 — facilitator + SDK (G7, G8).** Extract `agentpay_sdk/` package with
  a clean public surface + 10-min example; structured logging + startup config
  validation in the facilitator. *Test gate: both suites green + SDK example runs.*
- **Phase 5 — docs (G9).** ARCHITECTURE.md, INTEGRATION.md, SECURITY.md; align
  README status framing with reality (honestly). *No code; test gate = still green.*
- **Phase 6 — demo (G10).** DEMO.md with exact commands for a testnet recording;
  verify `scripts/demo.py` runs locally end-to-end. *Test gate: demo green locally.*

---

## 4. What I need from you (manual substitution — I will not invent these)

Per your rule, anything I can't confirm is left as a config TODO, not guessed:

1. **Real WB Soul testnet addresses.** WhiteBIT publishes only *mainnet* WB Soul
   addresses; no testnet list is public. Until you provide them, the demo runs
   against `MockSoulRegistry` (`USE_MOCK_SOUL=true`). To switch to real WB Soul:
   set `USE_MOCK_SOUL=false` and fill `SOUL_REGISTRY_ADDRESS`,
   `SOUL_ATTRIBUTE_REGISTRY_ADDRESS`, `SOUL_BOUND_TOKEN_REGISTRY_ADDRESS`,
   `IS_VERIFIED_ATTRIBUTE_ADDRESS`, `SBT_COLLECTION_ADDRESS` in `.env`.
2. **Router KYA adapter (roadmap).** `AgentPayRouter` reads
   `ISoulRegistry.isVerified(uint256)`, which the *attribute-based* WB Soul does
   not expose. A thin adapter onto WhiteBIT's real attribute schema is needed
   before atomic-mode KYA runs against real WB Soul — I'll write it against the
   confirmed schema rather than guess. Testnet/local uses `MockRouterKYA`.
3. **Testnet secrets** (only ever testnet): `DEPLOYER_PRIVATE_KEY` and the four
   wallet keys, funded with faucet WBT. Never committed — `.env` only.
4. **Confirmed values I am *not* guessing but you should verify** before a real
   deploy: RPC `https://rpc-testnet.whitechain.io`, `CHAIN_ID=2625`, explorer
   `https://testnet.whitechain.io`, gas token WBT. These come from the existing
   `TESTNET_DEPLOYMENT.md` run; re-confirm they're still current.

Also decide: **external audit vendor & timing** (SECURITY.md will state "audit
status: internal review complete, external audit pending/scheduled" — tell me if
one is booked so I can name it).

---

## 5. Known limitations that stay documented, not silently "fixed"

- **Reputation is sybil-able**: behavioral counters live in a local SQLite table;
  self-dealing can inflate `completed_payments`. Trust is held by the on-chain
  layer (KYA + SBT). Production needs cross-node/on-chain provenance. Documented
  in `docs/audit/03-reputation-threat-model.md` — this is a design decision, not
  a bug to patch mechanically.
- **WB Soul is mocked on testnet** (item §4.1) — the single biggest "not-yet-real"
  piece, and it's an external dependency on WhiteBIT, not our code.
