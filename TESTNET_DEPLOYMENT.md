# Live Whitechain Testnet Deployment

**Status: the full AgentPay trust layer is now deployed and verified end-to-end
on real Whitechain testnet** — closing the gap noted in the README ("verified
end-to-end locally … but not yet run against real Whitechain testnet").

Date: 2026-08-05 · Network: Whitechain Testnet (Chain ID `2625`) ·
`USE_MOCK_SOUL=true` (WhiteBIT's real WB Soul is not yet published on testnet;
swapping to it is a config change — `USE_MOCK_SOUL=false` + real addresses).

## Deployed contracts

| Contract | Address |
|---|---|
| tEURC (ERC-20 + EIP-3009) | [`0x6aadCEc9E885BeeeB1B01924174a4Bb261caA579`](https://testnet.whitechain.io/address/0x6aadCEc9E885BeeeB1B01924174a4Bb261caA579) |
| MockSoulRegistry | [`0xb54050e9ff50E98b7ad34017D66A0E4637A5e23D`](https://testnet.whitechain.io/address/0xb54050e9ff50E98b7ad34017D66A0E4637A5e23D) |
| MockSoulAttribute (IsVerified) | [`0xBd98A9F2255846cE3e78437Cd2fBdB3e0cAB6Ba0`](https://testnet.whitechain.io/address/0xBd98A9F2255846cE3e78437Cd2fBdB3e0cAB6Ba0) |
| MockSoulBoundTokenCollection (SBT) | [`0x1DA4244506353e43b171F03E7b349263f5B9E862`](https://testnet.whitechain.io/address/0x1DA4244506353e43b171F03E7b349263f5B9E862) |

Deployer / owner: `0x76E630D336f3cF055698EBFd1C1e506E48241d86` ·
Facilitator (gas payer / relay): `0x32F45Fd81C0453B36d7D3610adbbc5CF1C34c3Af`

## End-to-end run on testnet (`python scripts/demo.py`, `NETWORK=whitechain_testnet`)

All eleven steps passed — every payment, verification and SBT issuance was a
real transaction on Whitechain testnet:

- **KYA gate** — an agent with no WB Soul is refused, regardless of a valid
  signature or amount.
- **Reputation gate** — a verified agent without the required SBT tier is
  refused the premium resource; an agent with the SBT is granted it.
- **Behavioral reputation** — a "veteran" agent (score 83.22 → tier 2) is
  granted the premium resource with no SBT; a "flagged" agent (score 0.71 →
  tier 0) is refused.
- **EIP-3009 settlement** — off-chain-authorized `transferWithAuthorization`
  in tEURC, relayed on-chain by the facilitator, resource released on
  `SettlementConfirmed`.
- **Anti-replay** — re-presenting a used authorization (nonce) is rejected.

### Run summary (metrics)

| Metric | Value |
|---|---|
| Settlement transactions | 4 |
| AI Service Provider net earnings | 0.238800 tEURC |
| Facilitator fee | 0.5% |
| Resource release | on SettlementConfirmed |
| PaymentRequested → AccessGranted latency | ~9.2 s (real block confirmation) |
| Held settlements (reconciliation) | 0 |

## Reproduce

```bash
# .env: NETWORK=whitechain_testnet, RPC, CHAIN_ID=2625, DEPLOYER_PRIVATE_KEY,
#       wallet keys, USE_MOCK_SOUL=true, ADMIN_API_TOKEN=<any value>
npx hardhat compile
npx hardhat run deploy/deploy.ts --network whitechain_testnet   # deploys tEURC + mock Soul
# copy printed TEURC_ADDRESS / SOUL_* into .env
python scripts/demo.py                                          # full KYA + reputation + EIP-3009 run
```

## Notes

- **EVM version:** the contracts compile with `evmVersion: cancun`; Whitechain
  testnet executes the resulting bytecode without issue (no `mcopy`/opcode
  fallback to `shanghai` was needed).
- **WB Soul:** run against `MockSoulRegistry`. When WhiteBIT publishes testnet
  WB Soul addresses, set `USE_MOCK_SOUL=false` and fill the real `SOUL_*` /
  `IS_VERIFIED_ATTRIBUTE_ADDRESS` / `SBT_COLLECTION_ADDRESS` — no code change.
