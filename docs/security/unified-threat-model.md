# Unified pipeline — threat model

Scope: the unified pipeline (`unified/`) that runs
`Agent → Discovery → Scoring → TrustGate → X-PAYMENT → SettlementEngine → Result`.
This complements `SECURITY.md` (on-chain layer). Testnet PoC — **not an audited
production system** (see the RED items in the Phase 5 assessment).

## Actors & assets

| Actor | Trust | Asset it touches |
|---|---|---|
| Agent (buyer) | signs off-chain, never pays gas | its tEURC balance |
| Provider (seller) | signs its listing; KYA-anchored identity | its earnings (net) |
| Registry | observer authority for quality | listings + quality attestation |
| Facilitator/Router | relays; cannot redirect funds (atomic) | fee split, gas |
| Token (tEURC) | on-chain source of truth | balances, nonces |

Assets: buyer funds; the fee/net split; the KYA gate's integrity; the quality &
reputation signals.

## Threats → controls → enforcement layer

| Threat | Control | Enforced in | Test |
|---|---|---|---|
| **Fake provider** (unregistered/spoofed) | signed listing; `id == recovered signer`; only verified records discoverable | `unified/registry.py` (registry_auth) | `test_unified_registry`, `test_unified_failure_paths#17` |
| **Listing tampering** (endpoint/url/pay_to/price) | seller signature covers those fields; any change breaks verification | registry_auth | `test_registry` tamper/hijack |
| **Quality manipulation** (seller inflates rating) | quality is **registry-attested**, outside the seller signature; seller-submitted quality ignored | registry (observer) | `test_seller_cannot_forge_quality` |
| **Identity spoofing** (claim another's id) | `id == signer` check; signature-bound Provider | registry_auth / models | `test_wrong_signer_id_mismatch` |
| **Payment replay** (reuse authorization) | on-chain nonce (`tEURC.authorizationState`) is source of truth; optional off-chain pre-check | tEURC (on-chain) + validator | `test_replay_same_authorization`, `#16` |
| **Payment substitution / underpay** (sign smaller amount) | `amount == service price` before settle | `UnifiedPaymentValidator` | `#5 wrong_amount` |
| **payTo substitution** (redirect funds) | `to == provider.pay_to` before settle; atomic router binds destination to signature | validator + `AgentPayRouter` (C-1) | `#6 wrong_pay_to` |
| **Amount manipulation post-sign** | amount is the signed EIP-3009 `value`; changing it fails signature recovery | validator + tEURC | `#2`, `#5` |
| **Asset substitution** (settle apUSD as tEURC) | asset guard: engine refuses a non-matching asset; validator checks expected asset | validator + settlement adapter | `#7`, `test_settlement_adapter_rejects_asset_mismatch` |
| **Network mismatch** | validator checks expected network | validator | `#8 wrong_network` |
| **Invalid / forged signature** | EIP-712 recover == `from` off-chain; tEURC reverts on-chain | validator + tEURC | `#2 invalid_signature` |
| **Expired / future authorization** | validAfter/validBefore window checked off-chain and on-chain | validator + tEURC | `#3`, `#4` |
| **Access without KYA / below tier / policy** | TrustGate hard gate; settlement never called on deny | `FacilitatorTrustGate` (identity+policy) | `#11/#12/#13`, E2E scenario B |
| **Settlement failure hidden** | FAILED/FUNDS_HELD surfaced with buyer-safe reason; no silent loss | settlement adapter | `#14`, `#15` |
| **Secret leakage in logs** | telemetry carries only non-secret fields; guard rejects secret-like keys | `unified/telemetry.py` | `test_unified_telemetry` |

## Key invariants (proven by tests)

1. **Money never moves before its gate.** Every pre-settlement rejection asserts
   `settlement.called == False` (`test_unified_failure_paths`).
2. **Quality ≠ Trust.** Scoring never grants access; only the TrustGate does.
3. **Anti-forge.** Seller signs the listing; registry attests quality; the buyer
   trusts neither blindly (verifies the signature, reads quality as registry data).
4. **Conservation.** `gross == fee + net`, integer units, no hidden `*10**decimals`.
5. **Replay.** The tEURC token is the on-chain source of truth for nonce reuse.

## Residual risks (not fully mitigated here)

- **Registry is a trusted observer** for quality/attestation — a malicious
  registry node could misreport quality (not settlement integrity). A
  decentralized/multi-attestor registry is future work.
- **Reputation is sybil-able** at the behavioral layer (single-node counters) —
  see `SECURITY.md` and `docs/audit/03-reputation-threat-model.md`.
- **WB Soul is mocked on testnet** — KYA strength depends on the real WB Soul,
  not yet published on testnet.
- **No external audit** of the unified layer yet.
