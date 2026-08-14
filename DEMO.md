# DEMO.md — reproducible end-to-end demo

One script, `scripts/demo.py`, runs the full pipeline — discovery → KYA gate →
reputation gate → EIP-3009 settlement → anti-replay — as **11 steps**, each
printing an `[OK]`. It runs identically against a **local** in-memory chain (no
setup) and against **real Whitechain testnet** (the recording target). No code
changes between the two — only `.env`.

- [A. Local demo (30 seconds, no testnet)](#a-local-demo)
- [B. Whitechain testnet demo (for the video)](#b-whitechain-testnet-demo)
- [What each step shows](#what-each-step-shows)

---

## A. Local demo

Runs against an in-memory EVM; deploys everything fresh each run. Nothing to
fund, no keys.

```bash
pip install -r requirements.txt -r requirements-dev.txt
npm install && npm run compile        # bootstraps a sha256-verified solc
cp .env.example .env                  # defaults: NETWORK=local
python scripts/demo.py
```

Expected: 11 steps, each ending `[OK]`, then a summary table (4 settlement
transactions, provider net earnings, 0 held settlements), exit code 0.

---

## B. Whitechain testnet demo

> This is the version to screen-record: every payment, verification and SBT
> issuance is a **real transaction** on Whitechain testnet, viewable on the
> explorer.

### B.1 One-time setup

```bash
# 0. Contracts compile (sha256-verified solc; works on restricted networks)
npm install && npm run compile

# 1. Fund gas wallets. The DEPLOYER and FACILITATOR addresses in your .env each
#    need testnet WBT (gas). Get it from https://testnet.whitechain.io/faucet
#    (see wallet addresses: `grep _WALLET_ADDRESS .env`, keys stay secret).

# 2. .env must have (fill the rest per .env.example):
#      NETWORK=whitechain_testnet
#      WHITECHAIN_TESTNET_RPC=https://rpc-testnet.whitechain.io
#      CHAIN_ID=2625
#      USE_MOCK_SOUL=true            # WhiteBIT hasn't published testnet WB Soul
#      ADMIN_API_TOKEN=<any value>   # enables /admin reconciliation in the summary
#      DEPLOYER_PRIVATE_KEY / *_WALLET_* keys (already generated, env only)

# 3. Deploy tEURC + mock WB Soul + AgentPayRouter from the DEPLOYER wallet:
npx hardhat run deploy/deploy.ts --network whitechain_testnet
#    -> prints a KEY=VALUE block (TEURC_ADDRESS, SOUL_*, ROUTER_ADDRESS,
#       ROUTER_KYA_ADDRESS, SETTLEMENT_MODE=atomic, TREASURY_ADDRESS).
#    Copy that block into your .env.
#    (If TREASURY_ADDRESS differs from the deployer, the treasury must call
#     router.acceptOwnership() — Ownable2Step. Default treasury = facilitator.)
```

### B.2 Record this

```bash
python scripts/demo.py
```

The script deploys nothing now (addresses come from `.env`); it mints tEURC to
fresh demo agents, verifies their WB Soul (mock), seeds the router's KYA, and
runs all 11 steps as real testnet transactions. Open
`https://testnet.whitechain.io` on the printed tx hashes to show them landing
on-chain.

Expected: the same 11 `[OK]` lines, plus a summary showing real settlement tx
hashes and a `PaymentRequested→AccessGranted` latency of a few seconds (real
block confirmation). A prior live run is recorded in
[`TESTNET_DEPLOYMENT.md`](TESTNET_DEPLOYMENT.md).

### Troubleshooting

- **`RPC веде на chain_id=X, а очікується 2625`** — wrong `WHITECHAIN_TESTNET_RPC`
  or `CHAIN_ID`.
- **`Конфіг неповний для старту: …`** — the startup validator listing missing
  `.env` keys; fill them and re-run.
- **Relay succeeds but the seller isn't paid (legacy mode)** — facilitator ran
  out of WBT gas mid-flow; top it up. Atomic mode (default) can't have this
  window.
- See [`DEPLOY_WHITECHAIN.md`](DEPLOY_WHITECHAIN.md) for the full runbook.

---

## What each step shows

| Step | Demonstrates |
|---|---|
| 1–2 | Five demo agents created; each funded with tEURC |
| 3 | WB Soul (mock) verification + one SBT badge issued |
| 4–5 | Provider + registry up; agent discovers the service via the **registry**, not a hardcoded URL |
| 6 | **KYA gate:** an agent with no WB Soul is refused — despite a valid signature and amount |
| 7 | **Reputation gate:** a verified-but-unbadged agent is refused the *premium* resource |
| 8 | A verified agent's off-chain signature is accepted; resource released on **SettlementConfirmed**; fee to treasury |
| 9 | An agent that holds the SBT is granted the premium resource |
| 10 | **Reputation engine:** a "veteran" (behavioral tier 2, no SBT) is granted premium; a "flagged" agent (tier 0) refused |
| 11 | **Anti-replay:** re-presenting a used authorization (nonce) is rejected |

---

### Note on running the testnet demo from this repository's CI/agents

The testnet steps require network egress to `whitechain.io`. Some sandboxed
environments (including the agent session that built this) block that host, so
the **testnet** demo must be run from a machine with real network access to
Whitechain (your laptop, or CI with the host allow-listed). The **local** demo
(§A) runs anywhere.
