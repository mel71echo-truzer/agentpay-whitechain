# 06 — Slither static analysis

Static analysis of the two first-party contracts (`contracts/tEURC.sol`,
`contracts/AgentPayRouter.sol`) with **Slither 0.11.6**, solc **0.8.24** (cancun,
the project's pinned compiler). OpenZeppelin library code under `node_modules/`
is out of scope (`--filter-paths node_modules`) — it is audited upstream, and
its version-pragma / naming / too-many-digits notices are library noise, not
findings against this project.

## How to reproduce

```bash
bash scripts/bootstrap_solc.sh                 # verified solc 0.8.24 (see Phase 1)
ln -sf "$HOME/.cache/hardhat-nodejs/compilers-v2/linux-amd64/solc-linux-amd64-v0.8.24+commit.e11b9ed9" /usr/local/bin/solc
pip install slither-analyzer

slither contracts/AgentPayRouter.sol \
  --solc-remaps @openzeppelin=node_modules/@openzeppelin \
  --solc /usr/local/bin/solc --solc-args "--evm-version cancun" \
  --filter-paths node_modules
slither contracts/tEURC.sol \
  --solc-remaps @openzeppelin=node_modules/@openzeppelin \
  --solc /usr/local/bin/solc --solc-args "--evm-version cancun" \
  --filter-paths node_modules
```

(Slither's Hardhat integration re-runs `hardhat clean --global`, which tries to
re-download solc from `binaries.soliditylang.org` — blocked in restricted
networks. Driving solc directly, as above, avoids that.)

## Results

| Contract | Findings | Status |
|---|---|---|
| `AgentPayRouter.sol` | 0 | clean |
| `tEURC.sol` | 2 (`timestamp`, `naming-convention`) | both accepted with rationale below |

### Fixed

- **`AgentPayRouter` constructor — `missing-zero-check` on `_teurcToken` and
  `_soulRegistry`.** Both are `immutable`, so a zero address at deploy would
  permanently brick the router (every settlement would revert or misroute).
  **Fixed:** the constructor now reverts `ZeroAddress()` if either is `address(0)`.
  Slither on the router is now clean (0 results).

### Accepted (won't fix) — `tEURC.sol`

- **`timestamp` — "uses timestamp for comparisons"**
  (`_requireValidAuthorization`, the `validAfter`/`validBefore` window).
  **Accepted.** EIP-3009 authorizations are *defined* as time-windowed, and
  `block.timestamp` is the only clock available on-chain. A miner's few-seconds
  timestamp tolerance is immaterial to a 5-minute authorization window
  (`AUTH_VALID_SECONDS = 300` in `agent_client.py`) and cannot be used to
  bypass the window or replay a nonce (replay is a separate `used`-flag check).
  This is the intended, spec-mandated behaviour, not a vulnerability.

- **`naming-convention` — "Contract `tEURC` is not in CapWords"**
  **Accepted.** `tEURC` is the token's symbol/brand ("test EURC"), deliberately
  lowercase-`t` like real regulated tickers (`tEURC`/`tUSDC` conventions).
  Renaming to `TEURC` would break the token's identity for no security benefit.

## Notes

- No `reentrancy`, `arbitrary-send`, `unchecked-transfer`, `tx-origin`,
  `delegatecall`, `suicidal`, or access-control findings on either contract.
  `AgentPayRouter` uses `ReentrancyGuard` + checks-effects-interactions +
  `SafeERC20` + a relayer allow-list; fund destinations are bound into the
  buyer's signed nonce (finding C-1, closed — see `AUDIT_REPORT.md`).
- Ownership on both contracts is `Ownable2Step`, so a mistyped ownership
  transfer cannot silently hand off mint authority (`tEURC`) or the fee
  recipient (`AgentPayRouter`) — the new owner must call `acceptOwnership()`.
- This is automated static analysis, not a substitute for an external audit.
  Audit status: **external audit not yet commissioned (planned)** — see
  `SECURITY.md` (Phase 5).
