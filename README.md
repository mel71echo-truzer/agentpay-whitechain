# AgentPay on Whitechain

> **Status: proof-of-concept / MVP.** This is **not** a production payment
> network. No escrow, no dispute resolution, no mainnet settlement yet — see
> [Out of scope](#out-of-scope-phase-3) for what's deliberately not here.
> The payment core (tEURC/EIP-3009 + the KYA/reputation gate + the modular
> facilitator) has been verified end-to-end locally (`scripts/demo.py`,
> Solidity + pytest suites) but **not yet run against real Whitechain
> testnet** — see [Step 0](#step-0-network--wb-soul-recon) for why, and
> [`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md) for how to do that deploy
> yourself.

**AgentPay is a trust layer for the AI-agent economy; payments are one
service.** The hard part of agents transacting autonomously isn't moving a
stablecoin — that's a solved, copy-pasteable primitive. The hard part is
*trust*: knowing the counterparty is a verified, accountable agent with a
track record, and enforcing policy on that before any money moves. This PoC
implements that trust layer — identity, reputation, and policy gating a
payment — and payment is just the first service wired behind it.

**KYA-native payments — WB Soul identity + SBT reputation — on a
MiCA-aligned chain. The one thing that cannot be copy-pasted to Base or
Polygon.**

x402-style agent-to-agent payments are an open standard; a service running
that flow on any EVM chain is not, by itself, a moat — three independent
technical reviews of this project's earlier version confirmed as much: the
original code could run on any EVM chain unchanged. What Whitechain
specifically offers, and what this PoC is actually built to demonstrate, is
**WB Soul**: on-chain identity (KYA — Know Your Agent) and reputation
(soul-bound tokens) that a payment can be gated on. Without a verified WB
Soul, this system refuses to move money — full stop, regardless of a
correct signature or a correct amount. That gate is the point.

---

## The Problem

AI agents can reason, plan, and execute tasks across the internet. But they
can't pay, and even where they technically can (x402, wallets), nothing
stops a payment from an anonymous, unaccountable piece of software — which
is exactly the kind of counterparty a regulated business cannot deal with.

## The Solution

Two independent AI agents trade a service (a photo). Payment happens in
**tEURC**, a testnet euro-stablecoin, authorized **off-chain** via
[EIP-3009](https://eips.ethereum.org/EIPS/eip-3009) (no waiting for the
buyer's own transaction to mine) and relayed on-chain by a facilitator —
but only after the facilitator confirms, on-chain, that the payer has a
**verified WB Soul** (Whitechain's KYA identity primitive) and, for
higher-value resources, a **minimum reputation tier** derived from
soul-bound tokens (SBTs) attached to that identity.

## Architecture

```
Agent (buyer)                          AI Service Provider
     │  GET /photo/{name}                     │
     ├────────────────────────────────────────▶
     │       402 Payment Required              │
     │  { payTo: facilitator, price_teurc,     │
     │    resource, min_reputation_tier }      │
     ◀────────────────────────────────────────┤
     │
     │  signs EIP-3009 TransferWithAuthorization
     │  off-chain (no tx, no gas, no waiting)
     │  nonce = keccak256(resource || salt)
     │  → binds this signature to THIS resource
     │
     │  POST /photo/{name}
     │  { authorization, resource, resource_salt }
     ├────────────────────────────────────────▶  AI Service Provider
                                                        │
                                                        ▼
                                              facilitator.verify_and_settle()
                                              1. KYA gate:  soulOf(from) != 0
                                                            AND IsVerified == true
                                              2. Reputation gate: SBT count
                                                 → tier, checked against
                                                 the resource's minimum
                                              3. Resource binding: nonce
                                                 matches (resource, salt)
                                              4. EIP-712 signature recovery
                                                 (off-chain, no RPC call)
                                              5. validAfter/validBefore window
                                              6. On-chain replay check
                                                 (tEURC.authorizationState)
                                              7. Amount + payee address
                                                        │
                                              all pass? relay
                                              transferWithAuthorization
                                              on-chain (broadcast only —
                                              not wait_for_receipt)
                                                        │
     ◀────────────────────────────────────── 200 + photo + settlement
       (or 402 + rejection reason,             (reputation_tier, fee,
        e.g. "not KYA-verified" /                relay tx hash)
        "insufficient reputation")
```

The facilitator receives the full payment, keeps a configurable fee
(`FACILITATOR_FEE_BPS`, 0.5% by default), and forwards the rest to the AI
Service Provider in a second transfer — the simpler of two documented
designs (see [Trade-offs](#trade-offs-worth-knowing-about)), not an atomic
router-contract split.

The facilitator itself is **not** a god object: it's decomposed into
single-responsibility modules (`facilitator/identity.py`, `reputation.py`,
`policy.py`, `payment.py`, `settlement.py`, `capability.py`, `events.py`,
`store.py`), and `whitechain_facilitator.py` is a thin orchestrator that
just sequences them `identity → policy → payment → settlement → event →
response`. The agent finds the service through a **Capability Registry**
(`GET /registry/capabilities?type=…`) rather than a hardcoded URL, and the
resource is released on **SettlementConfirmed** (after the relay's receipt),
not on broadcast — closing the off-chain race where content could be handed
out before the payment actually landed (`WAIT_FOR_CONFIRMATION`, default
true; `false` is a fast local-demo path with the obvious trade-off).

## North-star architecture (vision)

The layers below are the **target architecture**, not all built today. This
PoC implements the shaded core (Identity, Capability, Policy, Payment,
Settlement, Reputation, one Provider) as an in-process modular monolith on
Whitechain. Everything else is directional — see
[Out of scope](#out-of-scope-phase-3).

```
                          target architecture (vision)
┌─────────────────────────────────────────────────────────────────────┐
│  Agent SDK            client libs: sign auth, discover, pay           │   (partial: agent_client.py)
├─────────────────────────────────────────────────────────────────────┤
│  Identity      ✔  WB Soul: KYA (soulOf + IsVerified)                  │   ← built
│  Capability    ✔  service discovery registry (by capability_type)    │   ← built
│  Policy        ✔  allow/deny: verified + reputation tier + limits    │   ← built
│  Payment       ✔  off-chain EIP-3009/EIP-712 validation              │   ← built
│  Settlement    ✔  relay + fee; confirmed before access (seam for     │   ← built
│                   Phase 2.5 atomic router / escrow)                   │
│  Reputation    ✔  behavioral score/tier + SBT anchor                 │   ← built
│  Providers     ◐  one AI Service Provider (photos); adapters later   │   ← 1 built
├─────────────────────────────────────────────────────────────────────┤
│  Whitechain    ✔  tEURC (EIP-3009), WB Soul, gas in WBT              │   ← built (local; testnet-ready)
└─────────────────────────────────────────────────────────────────────┘
   ✔ = implemented in this PoC   ◐ = single instance   (rest = roadmap)
```

## Reputation formula

So there's no "how is the score computed?" hand-waving, the exact off-chain
formula (`facilitator/reputation.py`, unit-tested in
`tests/test_reputation.py`):

```
score (0..100) =
      30 · completed_norm      # min(completed_payments / N_target, 1),  N_target = 20
    + 25 · (1 − dispute_ratio)
    + 20 · (1 − refund_ratio)
    + 15 · tenure_norm         # min(days_active / 365, 1)
    + 10 · sbt_bonus           # 1.0 if the agent holds a trust SBT, else 0
    − 25 · fraud_flags         # fraud penalty
score is clamped to [0, 100].

tier:  0..39 → tier 0     40..69 → tier 1     70..100 → tier 2
```

Behavioral counters (`completed_payments`, `disputes`, `refunds`,
`first_seen`, `fraud_flags`) live in a local SQLite `agent_stats` table and
are updated on settlement events. A **cold-start guard** in `identity.py`
matters: the formula gives a brand-new agent with no history 45 points
(because `1 − 0 = 1` on the dispute/refund terms), so for an agent with **no
recorded activity** we ignore the formula and use only its on-chain
SBT-attested tier — an SBT is minted precisely when an agent crosses a tier
threshold, so a held SBT attests a previously-reached tier. Effective tier =
`max(SBT-attested tier, behavioral tier)`. When an agent crosses a threshold,
the facilitator logs `"SBT tier N would be minted"` (actually minted via
`MockSoulRegistry.issueSBT` in the demo; real WB Soul minting is a TODO).

## Step 0: network & WB Soul recon

Before writing any contract, we tried to confirm whether WB Soul is
deployed on Whitechain testnet with public addresses. Result:

- This development session's network egress policy blocks the **entire
  `whitechain.io` domain** — RPC, docs, and explorer all return a policy
  `403` at the proxy level. Confirmed via the proxy's own status endpoint,
  not a guess.
- GitHub (reachable) has the real
  [`whitebit-exchange/soul-ecosystem-contracts`](https://github.com/whitebit-exchange/soul-ecosystem-contracts)
  repo. Its README publishes only **mainnet** addresses (IsVerified,
  HoldAmount attributes; an EarlyBird SBT collection; SoulDrop) — no
  testnet address list.
- Conclusion per the project's own contingency plan: since testnet Soul
  deployment couldn't be confirmed from this environment, build
  **`MockSoulRegistry`** — copied 1:1 against the real
  `ISoulRegistry`/`ISoulAttributeRegistry`/`ISoulBoundTokenRegistry`
  interfaces (`contracts/interfaces/`, fetched from the real repo) — so
  swapping to the real WB Soul deployment later is a config change
  (`USE_MOCK_SOUL=false` + real addresses in `.env`), not a rewrite.
  `facilitator.py` talks to Soul through that one interface either way.
- A second, independent blocker: Hardhat's solc downloader
  (`binaries.soliditylang.org`) is also outside this session's allowlist.
  Worked around by fetching the identical compiler binaries from
  `raw.githubusercontent.com/ethereum/solc-bin` (the same repo that backs
  that domain) and verifying their sha256 against the official
  `list.json` before use.

## Whitechain Testnet Configuration

| | |
|---|---|
| Chain ID | `2625` |
| RPC | `https://rpc-testnet.whitechain.io` |
| Explorer | `https://testnet.whitechain.io` |
| Faucet | `https://testnet.whitechain.io/faucet` |
| Gas token | WBT (pays for transactions; tEURC is the payment currency) |

## Contract addresses

Deployed fresh by `scripts/demo.py` when `NETWORK=local` (the default) —
there is no fixed local address table, a new one is printed every run. For
`NETWORK=whitechain_testnet`, addresses come entirely from `.env` — see
[`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md) for the deploy runbook and
where to record them once you've actually deployed there.

WB Soul mainnet reference addresses (not used directly by this PoC, but
what `USE_MOCK_SOUL=false` would eventually point at on mainnet):

| Contract | Address |
|---|---|
| IsVerified attribute | `0xd88fa142B67F561C5f2Cbf803bF5AE906a8f1e41` |
| HoldAmount attribute | `0xE6246B2C5bC67976eD6e28583e94a2a63ff36c93` |
| EarlyBird SBT collection | `0x57e0Dd3c3128CE9C580196Dc22F6204fc9A0bF18` |
| SoulDrop | `0x0000000000000000000000000000000000001001` |

## Run the demo in 5 minutes

No testnet, no API key, no real money — everything below runs against a
local in-memory chain by default.

```bash
git clone <this-repo-url>
cd agentpay-whitechain

# Python side
pip install -r requirements.txt
cp .env.example .env

# Solidity side (compiles the contracts scripts/demo.py and the tests deploy)
npm install
npx hardhat compile

# Run it
python scripts/demo.py
```

You should see six `[OK]` lines: an unverified agent rejected for KYA, a
verified agent's off-chain signature accepted and settled instantly, the
fee and `reputation_tier` shown, a verified-but-unbadged agent rejected on
the premium resource, the same resource granted to an agent that does hold
the badge, and a replayed signature rejected.

### Run the test suites

```bash
npx hardhat test                              # Solidity: tEURC + MockSoulRegistry
pip install -r requirements-dev.txt           # adds eth-tester[py-evm] + pytest
python -m pytest tests/ -v                    # Python: facilitator KYA/reputation/anti-replay
```

### Against real Whitechain testnet

See [`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md) — get testnet WBT from
the faucet, fill in `.env`, deploy, then run the exact same
`python scripts/demo.py` with `NETWORK=whitechain_testnet` set. No code
changes.

## Project Structure

```
agentpay-whitechain/
├── contracts/
│   ├── tEURC.sol                      # ERC-20 (6 decimals) + EIP-2612 + EIP-3009
│   ├── interfaces/                    # ISoulRegistry etc., copied from the real WB Soul repo
│   └── mocks/                         # MockSoulRegistry + two small stub contracts
├── deploy/deploy.ts                   # Hardhat deploy script (tEURC + mocks if USE_MOCK_SOUL)
├── test-solidity/                     # Hardhat/TS tests (tEURC, MockSoulRegistry)
├── hardhat.config.ts                  # 'hardhat' (in-memory) + 'whitechain_testnet' networks
│
├── chain.py                           # deploy/connect to contracts from Python via Hardhat artifacts
├── config.py                          # NETWORK, USE_MOCK_SOUL, addresses, prices — all from .env
├── facilitator/                       # decomposed facilitator (Phase 2)
│   ├── whitechain_facilitator.py      #   thin orchestrator: identity→policy→payment→settlement→event
│   ├── identity.py                    #   the only reader of WB Soul (+ reputation aggregation)
│   ├── reputation.py                  #   the explicit score/tier formula
│   ├── policy.py                      #   allow/deny from identity + resource reqs
│   ├── payment.py                     #   off-chain EIP-3009/EIP-712 validation
│   ├── settlement.py                  #   relay + fee; seam for a Phase 2.5 router
│   ├── capability.py                  #   capability registry / service discovery
│   ├── events.py                      #   payment-flow event journal
│   └── store.py                       #   SQLite: agent_stats / events / capabilities
├── agent_client.py                    # builds + signs EIP-3009 auth; discovers providers via registry
├── author/agent.py                    # Claude tool-use agent (the buyer)
├── service_provider/server.py         # FastAPI seller + registry endpoints (formerly "photobank")
├── wallets/setup_wallets.py           # native WBT (gas) wallet utilities
│
├── scripts/demo.py                    # end-to-end discovery + KYA + reputation showcase
├── tests/                             # pytest: facilitator + reputation/policy/capability/identity/events
│
├── SECURITY_REVIEW.md                 # Phase 0 audit (scope note: predates this architecture)
└── DEPLOY_WHITECHAIN.md               # runbook for a real Whitechain testnet deploy
```

## Tech Stack

- **Agents:** Python + Claude API (tool use)
- **Payments:** tEURC (ERC-20, 6 decimals) with EIP-3009 off-chain-authorized
  transfers and EIP-2612 permit, adapted from
  [Circle's reference EIP-3009 implementation](https://github.com/circlefin/stablecoin-evm/blob/master/contracts/v2/EIP3009.sol)
- **Identity/reputation:** WB Soul-shaped interfaces
  (`ISoulRegistry`/`ISoulAttributeRegistry`/`ISoulBoundTokenRegistry`),
  mocked locally, address-swappable for the real deployment
- **Contracts:** Solidity 0.8.24, OpenZeppelin 5.6, Hardhat 2.29
- **Chain:** Whitechain testnet (Chain ID 2625), gas in WBT
- **Server:** FastAPI

## Store schema & local state (breaking change)

The facilitator keeps a small local SQLite store (`STORE_DB_PATH`, default
`.agentpay.db`) with three tables — `agent_stats`, `events`, `capabilities`.
This is a **derived, off-chain cache**: the source of truth for money is
on-chain, and this file can be regenerated.

The schema is versioned via `PRAGMA user_version` (current: **2**). A
**breaking change** landed in this version: `capabilities.price` (REAL, a
float euro amount) became **`price_wei` (INTEGER, minimal units)** as part of
moving all money paths to integer wei. There is **no automatic migration** —
the store deliberately does not rewrite your data.

If you point the facilitator at an **older `.db`** (schema version < 2), it
**fails loudly at startup** with `StoreSchemaError` instead of opening and
then crashing later on the first write. To recover, delete the stale file and
its lock sidecar and let the state rebuild from the chain:

```bash
rm -f .agentpay.db .agentpay.db.lock   # or whatever STORE_DB_PATH points to
```

…or set `STORE_DB_PATH` to a fresh path. (Demos and tests use `:memory:`, so
they're always fresh and unaffected.)

## Trade-offs worth knowing about

- **Fee via custody, not an atomic split.** The facilitator receives the
  full payment at its own address, then forwards price-minus-fee to the
  service provider in a second transaction. Simpler than an atomic router
  contract, but it means the facilitator briefly custodies buyer funds. If the
  relay confirms but the forward reverts, the settlement is journaled as an
  explicit **funds-held / obligation-unmet** state (no silent loss) rather than
  retried automatically. Operators can list these for reconciliation via
  `GET /admin/held-settlements` (each entry carries the relay tx hash);
  forwarding them is a deliberate manual step, not an automatic retry.
- **Confirmation vs. latency.** By default (`WAIT_FOR_CONFIRMATION=true`)
  the resource is released only after the relay's receipt is mined
  (SettlementConfirmed), which closes the off-chain race where content
  could be handed out before the payment landed — at the cost of blocking
  the request on block time. Setting it `false` restores the fast
  broadcast-only path (release on submit) for local demos, and reintroduces
  that race; a production system on the fast path would need a
  reconciliation/retry mechanism for relays that later fail.
- **Reputation is a first-pass model**, not a hardened one. The formula is
  explicit and unit-tested (see [above](#reputation-formula)), but its
  behavioral inputs come from a local, single-node SQLite table that this
  same process writes — so it's trust-on-first-use and sybil-able (spin up
  new souls, self-deal to inflate `completed_payments`). A production
  version needs cross-node/on-chain provenance for the counters and
  sybil resistance. The formula also over-rewards absence of history (a
  brand-new agent scores 45); we guard that at cold start (see the formula
  section) but it's a real limitation of the model.

## Security Notes

This is a testnet prototype, not production-hardened financial software.

- **The AI Service Provider server binds to `127.0.0.1` by default** — keep
  it that way; don't expose it publicly without authentication and HTTPS.
- **The facilitator checks it's actually talking to the configured chain**
  on startup and refuses to run against a misconfigured or unreachable RPC.
- **`wallets/setup_wallets.py` prints private keys to your terminal.** Don't
  screen-record it; clear your scrollback afterwards.
- **Only ever fund these wallets with testnet assets.**
- [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md) is a full audit of the
  **Phase 0** architecture (native WBT transfers, no KYA gate) — read its
  scope note at the top. Phase 1's new surface (the EIP-3009 relay, the
  KYA/reputation gate, the fee-forwarding custody step) has **not** had an
  equivalent dedicated review yet. Treat that as an open item, not a gap
  that's been checked and found fine.

## Out of scope (Phase 3+)

Deliberately **not** built here, to keep scope honest. These were
consciously left as roadmap, not overlooked:

- **Microservices** — the whole system is one process (a modular monolith).
  Splitting identity / policy / settlement / registry into separately
  deployed services is a scaling decision for later, not a PoC concern.
- **Event bus / message queue** — events are a structured log + a SQLite
  `events` table, not Kafka/RabbitMQ/streams. A real bus (for fan-out,
  replay, async consumers) is Phase 3.
- **Provider adapters** — one AI Service Provider (photos). Pluggable
  adapters for real providers (OpenAI / Claude / Gemini / …) behind the
  capability layer are premature until there's more than one provider.
- **On-chain capability registry** — discovery is a local SQLite table today;
  a decentralized, on-chain registry (so discovery doesn't trust one node)
  is future work.
- **Escrow / atomic router contract (Phase 2.5)** — settlement is
  receive-full-then-forward with the facilitator briefly custodying funds;
  `settlement.py` keeps a clean seam so an atomic router/escrow contract can
  replace it without touching callers. That contract itself is a separate
  phase.
- **Payment channels / batching / streaming** — every purchase is its own
  on-chain settlement; no channels/batching to amortize gas, and payment is
  all-or-nothing per resource rather than metered/continuous.

## Contact

Telegram [@kingsmel](https://t.me/kingsmel) · [mel71echo@gmail.com](mailto:mel71echo@gmail.com)
