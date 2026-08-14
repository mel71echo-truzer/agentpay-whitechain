# SECURITY.md — threat model & security posture

Consolidated security documentation for AgentPay on Whitechain: the assets, the
threat model, what is covered (with evidence), the known accepted limitations,
and the audit status.

> **Audit status: external audit not yet commissioned (planned).** What exists
> today is internal review (three passes — [`AUDIT_REPORT.md`](AUDIT_REPORT.md),
> [`SECURITY_AUDIT_2026-08.md`](SECURITY_AUDIT_2026-08.md),
> [`SECURITY_REVIEW.md`](SECURITY_REVIEW.md), plus [`docs/audit/`](docs/audit/)),
> static analysis ([`docs/audit/06-slither.md`](docs/audit/06-slither.md)), and a
> comprehensive test suite (Python 96% coverage; production contracts 100%
> line/statement/function — [`COVERAGE.md`](COVERAGE.md)). This is a testnet PoC,
> not production-hardened financial software.

---

## 1. Assets & actors

**Assets:** buyer tEURC balances; the fee split; the integrity of the KYA gate
(who is allowed to transact); the reputation signal.

**Actors:** the **buyer** (signs authorizations, never pays gas); the
**facilitator/relayer** (relays on-chain, pays WBT gas, enforces the gate, is
trusted to *submit* but — in atomic mode — **cannot redirect** funds); the
**seller/provider**; an **attacker** who can see HTTP bodies and the mempool.

**Trust anchor:** on-chain **WB Soul** (KYA identity + SBT reputation). The gate
is the whole point: no verified soul ⇒ no payment, regardless of a valid
signature or amount.

---

## 2. Threat model — covered

Each row links the threat to where it's enforced and the test that proves it.

| Threat | Mitigation | Evidence |
|---|---|---|
| **Anonymous/unaccountable payer** | On-chain KYA gate: `soulOf(payer)≠0` **and** IsVerified | `facilitator/identity.py`, `policy.py`; `tests/test_policy.py`, `test_identity.py` |
| **Under-reputationed access to premium** | Reputation tier ≥ resource minimum; cold-start guard | `facilitator/reputation.py`; `tests/test_reputation.py` |
| **Signature replay (double-spend)** | On-chain per-authorizer nonce (`tEURC.authorizationState`); server pre-checks | `tEURC.sol`; `tests/test_atomic_settlement_integration.py`, Solidity `replay` test |
| **Resource swap** (pay for A, fetch B) | Nonce binds the signature to the resource: `keccak(resource‖salt)`; server verifies | `agent_client.py`, `payment.py` |
| **Relayer redirects the payout** (C-1) | Atomic router binds seller+amount+fee+resource into the signed nonce; relayer allow-list | `AgentPayRouter.sol`; Solidity `attack #1/#2a/#2b/#3` |
| **Fee inflation past the signature** | `feeBps` in the nonce + hard `MAX_FEE_BPS` cap | `AgentPayRouter.sol`; Solidity `attack #2b/#2c` |
| **Overpayment** | `value > price` rejected (no refund path) | `payment.py`; `tests/test_payment_overpayment.py` |
| **Expired / not-yet-valid authorization** | `validAfter`/`validBefore` window enforced on-chain and off | `tEURC.sol`, `payment.py` |
| **Leaked-but-unrelayed authorization** | EIP-3009 `cancelAuthorization` voids the nonce | `tEURC.sol`; Solidity `cancelAuthorization` test |
| **Reentrancy / unchecked transfer** | `ReentrancyGuard` + checks-effects-interactions + `SafeERC20` | `AgentPayRouter.sol`; Slither clean |
| **Fat-fingered ownership handoff** | `Ownable2Step` (mint authority / fee recipient) — new owner must accept | both contracts; Solidity `Ownable2Step` test |
| **Zero-address bricking** | Constructor `ZeroAddress()` guard on immutables | `AgentPayRouter.sol`; Solidity constructor test |
| **Partial settlement (relay ok, forward fails)** | Legacy path journals *funds held*; no silent loss, no auto-retry; operator reconciliation | `settlement.py`, `whitechain_facilitator.py`; `tests/test_settlement_partial_failure.py` |
| **Path traversal on resource name** | `^[A-Za-z0-9_-]+$` whitelist, `fullmatch` | `service_provider/server.py`; `tests/test_server_routes*.py` |
| **500 leaking internals on bad input** | try/except → sanitized `400`; RPC/revert text logged only, never surfaced | `server.py`, `whitechain_facilitator.py`; `tests/test_error_sanitization.py` |
| **Unauthenticated `/admin/*`** | Empty `ADMIN_API_TOKEN` ⇒ disabled (403); else Bearer + `hmac.compare_digest`; token never logged | `server.py`; `tests/test_server_routes.py` |
| **Spoofed `provider_url` redirecting payment** | Signed discovery records (`id==signer`); buyer pins signed `pay_to`, hard-fails on `payTo` mismatch | `registry_auth.py`, `agent_client.py`; `tests/test_registry_signed.py` |
| **Content released before payment lands** | Release on SettlementConfirmed (`WAIT_FOR_CONFIRMATION=true`) | `settlement.py`, `config.py` |
| **Float rounding drift in money** | Integer-wei arithmetic throughout; floored fee favors seller | `money.py`, `settlement.py`; `tests/test_money_scaling.py`, `test_price_scaling_audit.py` |
| **Secret leakage** | Keys/tokens only from env; `.env` gitignored & absent from history; startup validation prints names, not values; bind `127.0.0.1` | `config.py`, `.gitignore` |

---

## 3. Known limitations — accepted & documented (not silently "fixed")

- **WB Soul is mocked on testnet.** WhiteBIT hasn't published testnet WB Soul
  addresses, so the KYA/SBT layer runs against `MockSoulRegistry`. This is the
  single biggest "not-yet-real" piece and an external dependency, not a code
  gap. Swap = `USE_MOCK_SOUL=false` + real addresses. The atomic router's KYA
  source is a pluggable adapter whose real implementation is a deliberate TODO
  against the confirmed attribute schema (`facilitator/router_kya_adapter.py`).
- **Reputation is sybil-able.** Behavioral counters live in a local, single-node
  SQLite table this process writes; self-dealing can inflate
  `completed_payments`. Trust is held by the on-chain KYA+SBT layer. A
  production version needs cross-node/on-chain provenance and sybil resistance.
  Threat model: [`docs/audit/03-reputation-threat-model.md`](docs/audit/03-reputation-threat-model.md).
- **Facilitator is trusted to relay.** In **atomic** mode it cannot redirect
  funds (destinations are signature-bound), but it can censor (decline to
  relay). In **legacy** mode it briefly custodies funds between relay and
  forward; a forward failure is journaled, not lost, but reconciliation is
  manual.
- **Fast path reintroduces a race.** `WAIT_FOR_CONFIRMATION=false` releases on
  broadcast for low-latency local demos; a production fast path would need a
  reconciliation/retry mechanism.
- **Static analysis, not audit.** Slither is clean on `AgentPayRouter` and has
  two accepted findings on `tEURC` (EIP-3009's inherent `block.timestamp`
  window; the intentional `tEURC` lowercase symbol) — see
  [`docs/audit/06-slither.md`](docs/audit/06-slither.md). No external audit yet.

---

## 4. Reporting

This is a testnet PoC — **fund wallets with testnet assets only.** For security
questions or to report an issue: Telegram [@kingsmel](https://t.me/kingsmel) ·
[mel71echo@gmail.com](mailto:mel71echo@gmail.com).

## 5. Before mainnet — the security checklist

1. Commission an **external audit** of `tEURC` + `AgentPayRouter` and the
   facilitator settlement path.
2. Land the **real WB Soul integration** (router KYA adapter over the confirmed
   attribute schema; `USE_MOCK_SOUL=false`).
3. Replace single-node reputation counters with **cross-node/on-chain
   provenance** + sybil resistance.
4. Decide the facilitator **censorship/liveness** model (multiple relayers?).
5. Add settlement **reconciliation/retry** if any fast path is used in
   production.
