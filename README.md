# AgentPay on Whitechain

**AI agents paying AI agents — autonomously, on a MiCA-regulated European chain.**

A working prototype where one AI agent pays another for a service using an
[x402](https://github.com/coinbase/x402)-style payment protocol, settled on
[Whitechain](https://docs.whitechain.io) testnet — with zero human in the loop.

---

## The Problem

AI agents can reason, plan, and execute tasks across the internet. But they can't
pay. Every transaction still needs a human: a card number, a signature, a tap.
Banks won't open accounts for software — KYC requires a person.

## The Solution

x402 lets a web service reply "Payment Required" and lets an agent pay in
crypto automatically. This project runs that flow on **Whitechain** — an
EVM-compatible L1 with ~2s finality and cheap gas, operated by a
MiCA-licensed European exchange.

## Why Whitechain (not Base)

x402 is an open standard, but most real settlement runs on USDC on Base —
US infrastructure (Coinbase, Circle) inside the critical path. After MiCA, European
businesses need a **regulated European settlement option**. Whitechain is that option:
same standard, different rails.

Whitechain testnet has no live USDC, so this prototype adapts x402 to a
**native-currency scheme** (`exact-native` in `config.py`): the buyer pays
directly in testnet WBT and proves it with a transaction hash instead of an
EIP-3009 signed authorization. Same principle — the seller never trusts the
client, only the chain — with the crypto-plumbing simplified for an MVP.

## Whitechain Testnet Configuration

| | |
|---|---|
| Chain ID | `2625` |
| RPC | `https://rpc-testnet.whitechain.io` |
| Explorer | `https://testnet.whitechain.io` |
| Faucet | `https://testnet.whitechain.io/faucet` |
| Gas token | WBT |

## How It Works

```
Author Agent  ──GET /photo/{name}──▶  Photobank Server
     ▲                                        │
     │                              402 Payment Required
     │                              { payTo, amount_wbt, ... }
     │                                        ▼
     ├── pays WBT on Whitechain (~2s) ────────┘
     │
     ├── GET /photo/{name}
     │   header: X-PAYMENT = base64({txHash})  ──▶  Photobank Server
     │                                                    │
     │                                     facilitator.verify_payment(txHash)
     │                                     checks: on-chain, right address,
     │                                     right amount, not replayed
     │                                                    ▼
     ◀──────────────────── photo + X-PAYMENT-RESPONSE ────┘
```

Claude (via tool use) decides *when* to call the `buy_photo` tool — the
payment mechanics are hidden behind that one tool call.

## On-chain proof / Доказ роботи

Real `run_demo.py` run on Whitechain testnet — three autonomous agent-to-agent
payments, 0.02 WBT each:

- Seller wallet (all incoming payments): [0xfD760023E5671eed77B6f25907d93C077B28441B](https://testnet.whitechain.io/address/0xfD760023E5671eed77B6f25907d93C077B28441B)
- Transactions:
  - [0xb7ef1301e13677a92beb9ba37417fe83b82eb48a83ca85633696118e63acea81](https://testnet.whitechain.io/tx/0xb7ef1301e13677a92beb9ba37417fe83b82eb48a83ca85633696118e63acea81)
  - [0x215ffede864910a14fb162d1457eb9ff0540ff3838bf8e41874b12d8cb099f9e](https://testnet.whitechain.io/tx/0x215ffede864910a14fb162d1457eb9ff0540ff3838bf8e41874b12d8cb099f9e)
  - [0x05c14d6b76deefda21662681ce1fe651441a2ae95c06ea91708770722b64f7e8](https://testnet.whitechain.io/tx/0x05c14d6b76deefda21662681ce1fe651441a2ae95c06ea91708770722b64f7e8)

![Демо в терміналі](docs/screenshots/terminal.png)
![Доказ в експлорері](docs/screenshots/explorer.png)

## Run It Yourself

```bash
git clone <this-repo-url>
cd agentpay-whitechain
pip install -r requirements.txt
cp .env.example .env

# 1. Generate wallets, fund the Author wallet from the Whitechain testnet faucet
python -m wallets.setup_wallets      # prints two address/key pairs — save them into .env
python -m wallets.setup_wallets      # run again after funding to see balances

# 2. Add your ANTHROPIC_API_KEY and WHITECHAIN_RPC_URL to .env, then:
python run_demo.py "Write an article about Kyiv"
```

### Try it offline first (optional, no testnet or API key needed)

Before spending testnet WBT or Claude API calls, you can verify the whole
pipeline end-to-end against a local in-memory EVM chain and a fake Claude
client:

```bash
pip install -r requirements-dev.txt   # adds eth-tester[py-evm], dev-only
python tests/local_integration_test.py
```

This exercises wallets → facilitator → photobank server → author agent in
one process and asserts the money actually moves. If it passes, the only
thing left is plugging in real Whitechain RPC / API credentials.

This test is optional — `run_demo.py` does not need `requirements-dev.txt`.
Note for Windows users: `eth-tester[py-evm]` builds a native extension and
requires [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
to install; if you'd rather skip that, just go straight to `run_demo.py`
against real Whitechain testnet.

## Project Structure

```
agentpay-whitechain/
├── config.py                     # RPC, prices, limits — all from .env
├── requirements.txt               # runtime dependencies
├── requirements-dev.txt           # dev-only (offline integration test)
├── wallets/setup_wallets.py      # generate wallets, check_balance, send_wbt
├── facilitator/whitechain_facilitator.py  # verifies payments on-chain
├── photobank/server.py           # FastAPI seller: 402 → verify → deliver
├── photobank/images/             # sample photos for sale
├── author/x402_client.py         # pay_and_fetch() + spend-limit ledger
├── author/agent.py               # Claude tool-use agent (the buyer)
├── run_demo.py                   # orchestration + terminal showcase
├── tests/local_integration_test.py  # offline end-to-end proof
└── docs/
    ├── demo-video-script.md      # 2-min recording script
    └── screenshots/              # terminal.png, explorer.png for this README
```

## Tech Stack

- **Agents:** Python + Claude API (tool use)
- **Payments:** x402-style protocol (`exact-native` scheme), adapted for Whitechain
- **Chain:** Whitechain testnet (Chain ID 2625), gas in WBT
- **Server:** FastAPI

## Security Notes

This is a testnet prototype, not production-hardened financial software. Before
deploying it anywhere beyond your own machine, read this:

- **The photobank server binds to `127.0.0.1` by default (good) — keep it that
  way.** Do not expose it to the public internet or `0.0.0.0` without adding
  authentication and HTTPS in front of it. `verify_payment` currently trusts
  any caller who can reach the server and present a valid-looking payment
  proof; there is no per-caller auth, rate limiting, or resource-level binding
  of a payment to the specific item it was meant to pay for.
- **`wallets/setup_wallets.py` prints private keys to your terminal** so you
  can copy them into `.env`. Don't run it during a screen recording (see
  `docs/demo-video-script.md`), and clear your terminal scrollback/history
  afterwards.
- **Only fund these wallets with testnet WBT.** This codebase has not had a
  professional security audit and should not hold mainnet funds.
- A full internal security review is in [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md) —
  read it before any public deployment or mainnet work.

## Status

Prototype / proof-of-concept. Looking to take this to production.

## Demo

▶ [2-min demo video](#) — see `docs/demo-video-script.md` for the recording script.

## Contact

[your name] · [Telegram / email / LinkedIn]
