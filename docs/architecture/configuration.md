# Configuration — canonical vs legacy

Two config surfaces coexist by design (Phase 4/5). Neither was destructively
migrated; existing components keep their config, and the unified layer has one
canonical source.

```
config.py            → legacy GitHub components (facilitator, service_provider, scripts, tests)
unified/config.py    → unified layer (registry, pipeline, adapters, examples)
```

`unified/config.load_unified_config()` reads the **canonical** variable names and
**falls back** to the existing names, so a single `.env` drives both.

## Canonical variables (and their legacy fallbacks)

| Canonical (new) | Falls back to | Meaning | Default |
|---|---|---|---|
| `WHITECHAIN_RPC_URL` | `WHITECHAIN_TESTNET_RPC` | RPC endpoint | — |
| `WHITECHAIN_CHAIN_ID` | `CHAIN_ID` | chain id | `2625` |
| `TEURC_TOKEN_ADDRESS` | `TEURC_ADDRESS` | canonical asset address | — |
| `APUSD_TOKEN_ADDRESS` | — | compatibility asset address | — (empty = unused) |
| `FACILITATOR_URL` | — | facilitator base URL | — |
| `REGISTRY_URL` | — | capability registry URL | — |
| `SETTLEMENT_MODE` | `SETTLEMENT_MODE` | `atomic` \| `legacy` | `atomic` |
| `TEURC_DECIMALS` | `TEURC_DECIMALS` | canonical decimals | `6` |
| `APUSD_DECIMALS` | — | compatibility decimals | `6` |

**Precedence:** canonical name wins if set; else the legacy name; else the default.

## Assets

- **Canonical / production default:** `tEURC` (6 decimals). The only asset a
  production `SettlementEngine` must support.
- **Compatibility:** `apUSD` (kept so the local prototype keeps working). Leave
  `APUSD_TOKEN_ADDRESS` empty if not deployed. Never removed.

`PaymentAsset = tEURC | apUSD`; `load_unified_config().canonical_asset` is always
`tEURC`.

## Required vs optional

- **Required for a real testnet run:** `WHITECHAIN_RPC_URL` (or legacy),
  `WHITECHAIN_CHAIN_ID`, `TEURC_TOKEN_ADDRESS` (or legacy), plus the wallet keys
  the facilitator/deployer need (see the main `.env.example`). `validate_token_address`
  and `validate_chain_id` reject a malformed address / wrong chain.
- **Optional:** `APUSD_TOKEN_ADDRESS`, `FACILITATOR_URL`, `REGISTRY_URL`,
  `*_DECIMALS`.

## Development defaults

`NETWORK=local` needs none of the above — the demo/tests deploy fresh contracts
on an in-memory chain and set addresses at runtime. `python scripts/demo.py` and
`python examples/unified_quickstart.py` run with the shipped `.env.example`.

## Production requirements

- Real `WHITECHAIN_RPC_URL` + `WHITECHAIN_CHAIN_ID=2625`, a deployed
  `TEURC_TOKEN_ADDRESS`, and validated addresses (`validate_token_address`).
- Secrets (`*_PRIVATE_KEY`, `PRIVATE_KEY`, `ADMIN_API_TOKEN`) only in `.env`
  (gitignored) or a secrets manager — never committed.
- `SETTLEMENT_MODE=atomic` for no funds-held window.

## Money units

Everywhere on the settlement path, amounts are **integer minimal units** (for
tEURC/apUSD at 6 decimals, `0.02 → 20000`). The only decimals conversion is at the
human boundary (`to_minimal_units`/`from_minimal_units`, which reuse `money.py`).
There is no hidden `*10**decimals` in the pipeline.
