# Real Whitechain Testnet Gate A–F (Phase 7 closure)

The final gate for Phase 7. It runs the **production atomic path** end-to-end on
real Whitechain testnet and produces the seven evidences that close the phase.

It must run from an environment with **Whitechain RPC access** — the cloud dev
session that wrote this cannot reach `whitechain.io` (egress returns 403), so the
harness was validated on the in-memory EVM (`NETWORK=local`, 6/6) and is run for
real from your machine.

Harness: [`scripts/testnet_gate.py`](scripts/testnet_gate.py). It is a **test
harness only** — no product code, contracts, token, atomic path, or `main` are
changed by running it.

## Gate → evidence map

| Gate | What it proves | Evidence |
|---|---|---|
| **A** | Atomic settlement confirmed; net→seller, fee→treasury, buyer debited | #1 real atomic settlement · #6 balances + explorer TX |
| **B** | A buyer that **passes the app TrustGate** but is **not router-KYA'd** is rejected **on-chain** (`_requireKYA`), nothing moves | #2 on-chain KYA rejection |
| **C** | Re-presenting a used authorization (consumed nonce) is rejected | #3 replay rejection |
| **D** | Auth signed for resource A, presented to the server serving resource B (same price), is rejected before any money moves (H-2 derived-nonce binding) | #4 resource A→B rejection |
| **E** | With confirmation off, settlement is only `SUBMITTED`; pipeline returns **202** and **withholds** the resource (M-2) | #5 CONFIRMED-only |
| **F** | A held settlement is resolved by a **real on-chain forward**; `dry_run` moves nothing; `RESOLVED_FORWARDED` only after confirmation; repeat is an idempotent no-op | #7 reconciliation |

Every settling gate records the relay/action **tx hash + explorer link** and the
**buyer / seller / treasury balances** before and after, into
`gate_evidence/gate_whitechain_testnet_<ts>.json`.

## Prerequisites

1. Contracts deployed to testnet (tEURC + mock Soul + **AgentPayRouter + MockRouterKYA**):
   ```bash
   npx hardhat compile
   npx hardhat run deploy/deploy.ts --network whitechain_testnet
   ```
   Copy the printed `TEURC_ADDRESS / SOUL_* / ROUTER_ADDRESS / ROUTER_KYA_ADDRESS /
   TREASURY_ADDRESS / SETTLEMENT_MODE=atomic` block into `.env`.
2. `.env` set:
   ```
   NETWORK=whitechain_testnet
   SETTLEMENT_MODE=atomic
   WHITECHAIN_TESTNET_RPC=<your testnet RPC>
   CHAIN_ID=2625
   USE_MOCK_SOUL=true
   DEPLOYER_PRIVATE_KEY=<tEURC/registry owner; mints + seeds>
   FACILITATOR_WALLET_PRIVATE_KEY=<relayer; must have WBT for gas>
   FACILITATOR_WALLET_ADDRESS=<relayer address, allow-listed by deploy.ts>
   SERVICE_PROVIDER_WALLET_ADDRESS=<seller>
   ```
   Never commit `.env` (already git-ignored).
3. **Gas**: the facilitator wallet pays gas (WBT) for every relay/forward — fund it
   on testnet. The gate's throwaway buyers only **sign** (EIP-712); they need tEURC
   (minted by the harness) but no native gas.
4. If the treasury is a distinct address (2-step ownership), it must have called
   `router.acceptOwnership()` — otherwise `owner()` (fee recipient) is still the
   deployer and gate A's treasury-delta assertion measures the deployer.

## Run

```bash
# optional dry validation of the harness itself (no RPC needed)
NETWORK=local SETTLEMENT_MODE=atomic python scripts/testnet_gate.py   # expect 6/6

# the real gate
NETWORK=whitechain_testnet SETTLEMENT_MODE=atomic python scripts/testnet_gate.py
```

Exit code is `0` only if all six gates pass. The evidence JSON is the artifact to
review (and to attach to the Phase-7 closure): open each `explorer` link and
confirm the balances on-chain.

## Honest notes

- **Gate F origin is seeded.** `FUNDS_HELD` arises only in legacy mode when a relay
  confirms but the net forward reverts. Forcing a real forward-revert on testnet is
  not safely reproducible, so F seeds the held record and then performs the
  **resolution as a real on-chain transfer** (facilitator→seller). What is proven
  on-chain is the reconciliation action + its idempotency + confirmation-gated
  state — not a synthetic forward failure. This matches the M-3 design: accounting +
  re-check, never a fake guarantee.
- **Mock WB Soul.** The gate seeds its own throwaway buyers, which needs the mock
  Soul/KYA registries (`USE_MOCK_SOUL=true`). Against real WB Soul, pre-verify buyer
  keys out of band and adapt `_seed_buyers`.
- **B is the real security claim.** It deliberately makes the buyer pass the
  app-level TrustGate and still get rejected — proving the router, not the app, is
  the settlement boundary (H-3).
