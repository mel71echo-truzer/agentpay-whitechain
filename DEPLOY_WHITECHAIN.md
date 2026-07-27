# Deploying to real Whitechain testnet

This is a runbook for a human with actual network access to `whitechain.io`
(this development session didn't have that — see README "Step 0"). Follow
it in order; each step tells you what to put in `.env`.

## 0. Prerequisites

- Node.js 18+, Python 3.11+, `npm install` and `pip install -r requirements.txt`
  already run in this repo.
- A wallet you control, with its private key available (never commit it).

## 1. Get testnet WBT from the faucet

WBT is the **gas token** — you need it to pay for every transaction
(deploying contracts, minting tEURC, relaying payments), separate from
tEURC (the actual payment currency, which you mint yourself in step 3).

1. Generate a wallet if you don't have one: `python -m wallets.setup_wallets`
   (run it once with no addresses in `.env` — it prints a fresh
   `AUTHOR_WALLET_ADDRESS`/`AUTHOR_WALLET_PRIVATE_KEY` and
   `FACILITATOR_WALLET_ADDRESS`/`FACILITATOR_WALLET_PRIVATE_KEY` pair).
2. Visit `https://testnet.whitechain.io/faucet`, request WBT for the
   **facilitator** address (it's the one that pays gas to relay every
   payment) and, ideally, your deployer address too.
3. Confirm you can reach the RPC and see a balance: fill in
   `WHITECHAIN_TESTNET_RPC` and `DEPLOYER_PRIVATE_KEY` in `.env` (see step 2),
   then `python -m wallets.setup_wallets` again to see balances.

## 2. Fill in `.env`

```bash
NETWORK=whitechain_testnet
WHITECHAIN_TESTNET_RPC=<RPC URL from docs.whitechain.io>
CHAIN_ID=2625
DEPLOYER_PRIVATE_KEY=<the wallet you funded in step 1 — this account deploys and owns the contracts>

AUTHOR_WALLET_ADDRESS=<from wallets/setup_wallets.py>
AUTHOR_WALLET_PRIVATE_KEY=<same>
FACILITATOR_WALLET_ADDRESS=<from wallets/setup_wallets.py — needs WBT for gas>
FACILITATOR_WALLET_PRIVATE_KEY=<same>
SERVICE_PROVIDER_WALLET_ADDRESS=<any address you control — receives net payments>
SERVICE_PROVIDER_WALLET_PRIVATE_KEY=<its private key>
```

Leave `TEURC_ADDRESS` and the `SOUL_*`/`IS_VERIFIED_ATTRIBUTE_ADDRESS`/
`SBT_COLLECTION_ADDRESS` vars blank for now — the next step fills them in.

## 3. Check whether WB Soul is deployed on testnet

Before deploying, check `https://docs.whitechain.io` and
`https://testnet.whitechain.io` (the explorer) for a WB Soul contract
address list on **testnet** specifically — the addresses in this
repository's README are **mainnet only** (that's all that was published as
of when this was written; this session couldn't check testnet directly —
see "Step 0" in README.md).

- **If you find real testnet WB Soul addresses:** set
  `USE_MOCK_SOUL=false` and fill in `SOUL_REGISTRY_ADDRESS`,
  `SOUL_ATTRIBUTE_REGISTRY_ADDRESS`, `SOUL_BOUND_TOKEN_REGISTRY_ADDRESS`,
  `IS_VERIFIED_ATTRIBUTE_ADDRESS`, `SBT_COLLECTION_ADDRESS` in `.env`.
  In this mode, you cannot register/verify souls or issue SBTs yourself —
  that's WhiteBIT's real KYC process. You'll need a wallet that's already
  gone through it (or WhiteBIT's own testnet KYC flow, if one exists) to
  demo the "verified agent" path.
- **If you don't find them (most likely today):** leave
  `USE_MOCK_SOUL=true`. The deploy step below will deploy the mock
  contracts for you, and you fully control verification/SBT issuance as
  the contract owner — this is what `scripts/demo.py` already does
  locally, just now for real on testnet.

## 4. Deploy

```bash
npx hardhat compile
npx hardhat run deploy/deploy.ts --network whitechain_testnet
```

This deploys `tEURC` always, and — if `USE_MOCK_SOUL=true` — the mock Soul
stack too. It prints the resulting addresses in `.env` `KEY=VALUE` form.
**Copy that block into your `.env`.**

## 5. Mint yourself some tEURC

`tEURC.mint(address, amount)` is owner-only (the deployer). Quick way, from
a Python shell in the repo root:

```python
import chain, config
from eth_account import Account

w3 = chain.get_w3()
teurc = chain.get_contract(w3, "tEURC", config.TEURC_ADDRESS)
deployer_key = config.DEPLOYER_PRIVATE_KEY

for addr in (config.AUTHOR_WALLET_ADDRESS, ...):  # any agent wallets you want funded
    tx = chain.send_contract_tx(w3, deployer_key, teurc.functions.mint(addr, 10_000_000))  # 10.0 tEURC (6 decimals)
    w3.eth.wait_for_transaction_receipt(tx)
```

## 6. Run the demo for real

```bash
python scripts/demo.py
```

With `NETWORK=whitechain_testnet` set, this connects to your deployed
contracts instead of spinning up a local chain — same script, same six
`[OK]` lines, but every transaction is now a real one on Whitechain
testnet. Check them on `https://testnet.whitechain.io`.

## Troubleshooting

- **"RPC веде на chain_id=X, а очікується Y"** — `WHITECHAIN_TESTNET_RPC`
  points at the wrong network, or `CHAIN_ID` in `.env` is wrong.
- **Deploy or relay reverts with an "invalid opcode"-style error** —
  `hardhat.config.ts` targets `evmVersion: "cancun"` (required by
  OpenZeppelin 5.6's newer utilities). If Whitechain hasn't upgraded to the
  Cancun EVM opcodes yet, lower `evmVersion` to `"shanghai"` in
  `hardhat.config.ts`, and correspondingly pin `@openzeppelin/contracts` to
  a version that doesn't require Solidity ≥0.8.24 (check its `package.json`
  `engines`/peer requirements before downgrading `solidity.version` too).
- **Facilitator relay succeeds but the service provider never gets paid** —
  check the facilitator wallet's WBT (gas) balance; if it ran out mid-flow,
  the forwarding transfer (the second of the two transactions per payment)
  may not have been submitted. See README "Trade-offs" — this is a known,
  documented limitation of the current fire-and-forget relay design.
