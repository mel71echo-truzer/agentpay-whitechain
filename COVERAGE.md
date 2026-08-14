# COVERAGE.md — test coverage report

Two suites, both measured. Target for buyer-grade was **>90%**; both clear it.

- **Python** (facilitator, server, client, money paths): **96%** line+branch.
- **Solidity production contracts** (`tEURC`, `AgentPayRouter`): **100%**
  statement / function / line, **~92%** branch.

Regenerate anytime — the exact commands are below. Report artifacts
(`.coverage`, `coverage.xml`, `coverage/`, `coverage.json`) are gitignored; this
file is the committed summary.

---

## Python — 96% (146 tests)

```bash
pip install -r requirements-dev.txt
python -m pytest --cov --cov-report=term-missing   # config: .coveragerc
```

| Module | Cover | Notes |
|---|---:|---|
| `money.py` | 100% | integer-wei arithmetic (no float in money paths) |
| `router_binding.py` | 100% | shared client↔router nonce derivation |
| `registry_auth.py` | 100% | signed capability records |
| `facilitator/policy.py` | 100% | KYA + reputation gate |
| `facilitator/reputation.py` | 100% | the score/tier formula |
| `facilitator/capability.py` | 100% | service discovery |
| `facilitator/events.py` | 100% | payment-flow journal |
| `facilitator/router_kya_adapter.py` | 100% | pluggable KYA source |
| `service_provider/server.py` | 99% | all HTTP routes + guards |
| `facilitator/settlement.py` | 98% | legacy relay+forward |
| `facilitator/identity.py` | 96% | WB Soul reader |
| `facilitator/payment.py` | 96% | EIP-712/EIP-3009 validation |
| `agent_client.py` | 95% | discovery/selection/pay + signing |
| `facilitator/whitechain_facilitator.py` | 94% | orchestrator |
| `chain.py` | 94% | web3 glue (deploy-failure branch uncovered by design) |
| `config.py` | 93% | env parsing |
| `facilitator/atomic_settlement.py` | 91% | atomic settle |
| `facilitator/store.py` | 90% | SQLite store |
| **TOTAL** | **96%** | 961 stmts, 190 branches |

Scope (`.coveragerc`): every money-moving / trust-gating module is measured.
Deliberately omitted with reasons — `author/agent.py` (needs a live
`ANTHROPIC_API_KEY`; exercised in the live demo), `scripts/demo.py` (end-to-end
harness, not a unit), `wallets/` (interactive key-printing utilities).

### Attack tests included

Double-spend / replay (`test_payment_overpayment.py`,
`test_atomic_settlement_integration.py`), overpayment rejection
(`test_payment_overpayment.py`), spend-limit enforcement
(`test_agent_client_*`, `SpendLedger`), partial-settlement / funds-held
(`test_settlement_partial_failure.py`), error sanitization / no-secret-leak
(`test_error_sanitization.py`), store concurrency
(`test_store_concurrency.py`).

---

## Solidity — production contracts 100% line/stmt/func (29 tests)

```bash
npm run bootstrap:solc          # sha256-verified solc 0.8.24 (Phase 1)
npx hardhat coverage --testfiles "test-solidity/*.test.ts"
```

| Contract | Stmts | Branch | Funcs | Lines |
|---|---:|---:|---:|---:|
| `tEURC.sol` | 100% | 94.4% | 100% | 100% |
| `AgentPayRouter.sol` | 100% | 88.9% | 100% | 100% |
| **contracts/ (production)** | **100%** | **91.7%** | **100%** | **100%** |

Mocks under `contracts/mocks/` are test scaffolding (WB Soul stand-ins) and are
not production code; their partial coverage pulls the *aggregate* number down
but is irrelevant to what ships. Interfaces are declaration-only (100%).

The residual branch gaps are short-circuit combinations of defensive
conditions (e.g. `soulId == 0 || !isVerified` — each operand is exercised
separately; the both-true combination is unreachable) and one EIP-3009 window
branch. No production statement, function, or line is unexecuted.

### Attack tests included

C-1 seller-substitution (`attack #1`), amount/fee inflation past the signature
(`#2a`/`#2b`), fee-cap breach (`#2c`), unauthorized relayer (`#3`), replay,
KYA gate (soul-absent and soul-present-but-unverified), `cancelAuthorization`
void + forged-cancel, `Ownable2Step` two-step handoff, constructor zero-address.
