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

### Try it offline first (no testnet or API key needed)

Before spending testnet WBT or Claude API calls, verify the whole pipeline
end-to-end against a local in-memory EVM chain and a fake Claude client:

```bash
python tests/local_integration_test.py
```

This exercises wallets → facilitator → photobank server → author agent in
one process and asserts the money actually moves. If it passes, the only
thing left is plugging in real Whitechain RPC / API credentials.

## Project Structure

```
agentpay-whitechain/
├── config.py                     # RPC, prices, limits — all from .env
├── wallets/setup_wallets.py      # generate wallets, check_balance, send_wbt
├── facilitator/whitechain_facilitator.py  # verifies payments on-chain
├── photobank/server.py           # FastAPI seller: 402 → verify → deliver
├── photobank/images/             # sample photos for sale
├── author/x402_client.py         # pay_and_fetch() + spend-limit ledger
├── author/agent.py               # Claude tool-use agent (the buyer)
├── run_demo.py                   # orchestration + terminal showcase
└── tests/local_integration_test.py  # offline end-to-end proof
```

## Tech Stack

- **Agents:** Python + Claude API (tool use)
- **Payments:** x402-style protocol (`exact-native` scheme), adapted for Whitechain
- **Chain:** Whitechain testnet (Chain ID 2625), gas in WBT
- **Server:** FastAPI

## Status

Prototype / proof-of-concept. Looking to take this to production.

## Demo

▶ [2-min demo video](#) — see `docs/demo-video-script.md` for the recording script.

## Contact

[your name] · [Telegram / email / LinkedIn]
