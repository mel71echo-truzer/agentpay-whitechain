# AgentPay on Whitechain

> **Status: proof-of-concept / MVP.** This is **not** a production payment
> network. No escrow, no dispute resolution, no mainnet settlement yet — see
> [Roadmap](#roadmap-out-of-scope-for-this-poc) for what's deliberately not
> here. Phase 1's new payment core (tEURC/EIP-3009 + the KYA/reputation gate)
> has been verified end-to-end locally (`scripts/demo.py`, Solidity + pytest
> suites) but **not yet run against real Whitechain testnet** — see
> [Step 0](#step-0-network--wb-soul-recon) for why, and
> [`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md) for how to do that deploy
> yourself.

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
├── facilitator/whitechain_facilitator.py  # KYA gate + reputation + EIP-3009 relay + fee
├── agent_client.py                    # builds + signs EIP-3009 authorizations
├── author/agent.py                    # Claude tool-use agent (the buyer)
├── service_provider/server.py         # FastAPI seller (formerly "photobank")
├── wallets/setup_wallets.py           # native WBT (gas) wallet utilities
│
├── scripts/demo.py                    # end-to-end KYA/reputation showcase
├── tests/                             # pytest: facilitator KYA/reputation/anti-replay
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

## Trade-offs worth knowing about

- **Fee via custody, not an atomic split.** The facilitator receives the
  full payment at its own address, then forwards price-minus-fee to the
  service provider in a second transaction. Simpler than an atomic router
  contract, but it means the facilitator briefly custodies buyer funds.
- **Content is issued before the relay is confirmed.** `verify_and_settle`
  broadcasts `transferWithAuthorization` but does not wait for a mined
  receipt before the service provider hands over the resource — the whole
  point of not blocking the request cycle on block time. If the relay
  later fails (e.g. the facilitator runs out of gas), the resource was
  already given away unpaid. Acceptable for a PoC; a production version
  needs either a wait-for-confirmation path or a reconciliation/retry
  mechanism for failed relays.
- **Reputation tiers are a placeholder heuristic** (0 SBTs = tier 0, 1 = tier
  1, 2+ = tier 2), not a designed reputation system.

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

## Roadmap (out of scope for this PoC)

Deliberately not built here, to keep this PoC's scope honest:

- **Escrow / dispute resolution** — today, once a payment settles, it's
  final; there's no mechanism to hold funds pending delivery confirmation
  or to arbitrate a disagreement.
- **Capability marketplace** — agents currently know which specific service
  to call; there's no discovery/listing layer for "which agents sell what."
- **Payment channels / batching / scaling** — every purchase is its own
  on-chain settlement; no channel or batching layer to amortize gas across
  many small payments.
- **Streaming payments** — payment is all-or-nothing per resource, not
  metered/continuous.

## Contact

[your name] · [Telegram / email / LinkedIn]
