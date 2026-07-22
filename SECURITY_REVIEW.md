# Security Review — AgentPay on Whitechain

Internal audit of the payment-verification core, the FastAPI seller, the
spend-limit ledger, dependencies, and general code quality. Scope: this
repository as of the commit this file was added in. No secrets were read,
logged, or committed as part of this review; `run_demo.py` was not executed
against real credentials, and no on-chain transactions were made.

Severity is scored for **this project's actual current state** (a local,
testnet-only, single-operator demo), with a separate note on how severity
changes if the photobank server is ever exposed publicly or funded with
real value.

---

## Critical

### C-1. RPC error text (may contain a secret API key) is echoed straight to any client
**File:** `facilitator/whitechain_facilitator.py:88` (before fix, now fixed — see below) · **Status: FIXED**

`verify_payment` used to build its "not found" reason as
`f"Транзакцію не знайдено в мережі: {exc}"`, embedding the raw exception
object, and `photobank/server.py` forwarded that string directly into the
JSON body of the 402 response — sent to **any unauthenticated caller**.

Many RPC providers embed the access key in the URL path itself (e.g.
`https://rpc-provider.example/v2/<secret-key>`, the same pattern
Infura/Alchemy/QuickNode/etc. use). When such an RPC endpoint is briefly
unreachable, rate-limited, or errors, `requests`'/`web3.py`'s exception
message includes the **full URL, secret key included**.

**Reproduced:** a `ConnectionError`/`ProxyError` raised while calling
`w3.eth.get_transaction` was confirmed, via an actual HTTP round-trip
through `photobank/server.py`, to leak a synthetic
`.../v2/SUPER-SECRET-API-KEY-1234` RPC URL verbatim into the client-visible
`error` field of the 402 response. Any anonymous request with a bogus
`X-PAYMENT` header sent while the RPC is having a bad moment (rate limits,
brief outages — not a rare or contrived condition) would exfiltrate the
operator's RPC credential.

**Exploitability:**
- Locally / testnet demo today: low probability but real — triggers
  whenever the configured RPC has any hiccup.
- Public deployment: **critical** — an attacker can simply keep sending
  garbage `X-PAYMENT` values until the RPC rate-limits the server (which an
  attacker can often induce by flooding requests), harvesting the key on
  demand.

**Fix applied:** the raw exception is now logged server-side only
(`logger.warning(...)`), and the client receives a fixed, generic message
("Транзакцію не знайдено в мережі (або мережа тимчасово недоступна).").
No other exception-embedding response-content paths were found (all other
`reason` strings interpolate only addresses/amounts/counts, which are not
secret).

---

## High

*(none found that were left unfixed — see C-1, which would be High-and-up in
a public deployment but is fixed)*

---

## Medium

### M-1. A payment is not bound to the specific resource it's redeemed against (race / front-running)
**Files:** `facilitator/whitechain_facilitator.py` (`verify_payment`), `photobank/server.py` (`get_photo`), `wallets/setup_wallets.py` (`send_wbt`), `author/x402_client.py` (`pay_and_fetch`) · **Status: FIXED**

`verify_payment(tx_hash, expected_to, expected_amount_wbt, ...)` used to
never receive *which resource* (`name`) the caller was trying to redeem
the payment for — it only checked "was enough sent to the right address,
and is this `tx_hash` unused." Whoever was first to present a given valid,
unused `tx_hash` could choose which same-priced resource it got redeemed
for, not necessarily the buyer who made the payment (details in the
original write-up, kept below the fix for context).

**Fix applied — option 2 from the original proposal (the "correct" fix,
not the cheap first-come-first-served lock), with your sign-off:**
payment is now bound to its resource **on-chain**, via the transaction's
`data`/calldata field, which is signed together with the rest of the
transaction and cannot be altered after the fact:

- `wallets/setup_wallets.py:send_wbt` gained an optional `data: bytes`
  parameter. When present, gas is computed via `w3.eth.estimate_gas(...)`
  instead of the fixed `21000` (a plain transfer's base cost — insufficient
  once calldata is attached; `estimate_gas` is only used on this new path,
  so the old no-data behavior is byte-for-byte unchanged).
- `author/x402_client.py:pay_and_fetch` reads the `resource` field already
  present in the server's 402 payment-requirements JSON (e.g.
  `/photo/kyiv-lavra`) and passes its UTF-8 bytes as `data` when paying.
- `photobank/server.py:get_photo` now passes `expected_resource=f"/photo/{name}"`
  to `verify_payment`.
- `facilitator/whitechain_facilitator.py:verify_payment` gained an optional
  `expected_resource: str | None` parameter. When given, it decodes
  `tx["input"]` as UTF-8 and rejects the payment if it doesn't match
  exactly — *in addition to*, not instead of, the existing
  to/amount/replay checks. `expected_resource` defaults to `None` (check
  skipped) for backward compatibility with any other caller of the
  facilitator that doesn't need per-resource binding.

Because calldata is part of what's cryptographically signed and mined, a
third party can no longer redeem someone else's `tx_hash` against a
resource other than the one it was actually paid for — the calldata simply
won't match, no matter who submits the redeem request or how fast.
Confirmed with a new test (`tests/test_security_fixes.py::test_payment_for_photo_a_cannot_unlock_photo_b`):
paying for `/photo/kyiv-lavra` and then presenting that same `tx_hash`
against `/photo/kyiv-sofia-cathedral` now gets a clean 402 ("платіж
призначений для іншого ресурсу"), while presenting it against the correct
resource still succeeds, and reusing it a second time still hits the
existing replay protection. The full offline pipeline
(`tests/local_integration_test.py`) and the standalone facilitator
self-test both still pass unchanged.

<details>
<summary>Original write-up (for context)</summary>

`verify_payment(tx_hash, expected_to, expected_amount_wbt, ...)` never
receives *which resource* (`name`) the caller is trying to redeem the
payment for. It only checks: "was `expected_amount_wbt` sent to
`expected_to`, and has this exact `tx_hash` not been used before." Because
every photo costs the same fixed `PHOTO_PRICE_WBT`, this was not
exploitable for *underpayment* — but it did mean:

> Whoever is first to present a given valid, unused `tx_hash` to
> `/photo/{name}` gets to choose which `name` it's redeemed for — not
> necessarily the buyer who made the payment.

Transaction hashes are not secret (public the moment they're mined). If a
third party could reach the photobank server and observe the buyer's
`tx_hash` before the buyer's own follow-up request landed, that third
party could race to claim a (same-priced) resource of *their* choosing
using the buyer's money; the legitimate buyer's subsequent request would
then fail as a replay, and they'd get nothing for the WBT they spent.
Mitigated in practice by the server defaulting to `127.0.0.1`, but a real
architectural gap regardless.

</details>



### M-2. `SpendLedger` has no locking — concurrent processes can blow through the spend cap
**File:** `author/x402_client.py:32-70` · **Status: NOT FIXED — proposal only**

`SpendLedger` reads `SPEND_LEDGER_PATH` into memory in `__init__` and
writes the whole file back on every `record()`, with no file lock. This was
reproduced directly: two `SpendLedger` instances pointed at the same path,
each loaded before the other wrote, **both** pass `ensure_can_spend()`
against a cap they together exceed, and the second `record()` call
silently overwrites the first's write — the on-disk ledger ends up
under-reporting real spend, while the real on-chain total silently exceeds
the configured `AUTHOR_MAX_SPEND_WBT`.

In the demo's current usage (`author/agent.py`'s tool-use loop calls
`_buy_photo_tool` one at a time, synchronously, in a plain `for` loop) this
is **not reachable** — there is no concurrency in the shipped code path.
It becomes live the moment: (a) you run two `run_demo.py` invocations
concurrently against the same `.env`/ledger path, or (b) a future
optimization parallelizes purchases (e.g. `asyncio.gather` over multiple
`buy_photo` calls) — a very natural next feature to want.

There is a second, related correctness gap: in `pay_and_fetch`
(`author/x402_client.py:126`), `send_wbt(...)` is called and can raise
(RPC hiccup, or `web3.exceptions.TimeExhausted` if
`wait_for_transaction_receipt` in `wallets/setup_wallets.py` times out)
*after* the transaction has already been broadcast — i.e. real WBT may
already be in flight on-chain — but since the exception propagates before
`ledger.record(...)` runs, that spend is **never recorded**, permanently
under-counting true spend for the rest of the task.

**Suggested fix (not applied — changes limit-enforcement behavior):**
add a file lock around the read-check-write sequence (e.g. `filelock`
package, or `fcntl.flock` on POSIX) so `ensure_can_spend` + `record` become
atomic across processes; and wrap the `send_wbt` call so that if it raises
after broadcast, the ledger still records a best-effort entry (or at least
logs a loud warning that spend tracking may now be inaccurate).

### M-3. No `chain_id` sanity check — facilitator trusts whatever chain its RPC happens to be on
**File:** `facilitator/whitechain_facilitator.py:40-67` (`__init__`, `_assert_correct_chain`) · **Status: FIXED**

Nothing in `WhitechainFacilitator` used to assert
`self.w3.eth.chain_id == config.WHITECHAIN_CHAIN_ID` (2625). A misconfigured
`WHITECHAIN_RPC_URL` (operator typo pointing at a different chain), or a
third-party RPC provider silently serving the wrong network, would have
been accepted without complaint.

**Fix applied — fail fast at construction time, with your sign-off:**
`WhitechainFacilitator.__init__` now calls a new `_assert_correct_chain()`
right after `self.w3` is created:
- If the RPC can't even be reached (connection error/timeout), it raises a
  `RuntimeError` with a clear, actionable message ("перевір
  WHITECHAIN_RPC_URL"). The raw exception (which, per C-1, may embed a
  secret RPC API key in the URL) is logged server-side only via
  `logger.error`, never included in the raised message — consistent with
  the C-1 fix.
- If the RPC answers but reports a different `chain_id` than
  `config.WHITECHAIN_CHAIN_ID`, it raises a `RuntimeError` stating both the
  expected and actual chain IDs.

**A knock-on change was needed to keep this safe:** `photobank/server.py`
used to construct `facilitator = WhitechainFacilitator()` eagerly at
*module import time* — which would now mean a bare `import photobank.server`
(as done by any test, or any tool that just wants `app`) crashes immediately
if `WHITECHAIN_RPC_URL` isn't configured/reachable yet, before
`run_demo.py`'s own friendly `_check_config()` pre-flight check even runs.
Fixed by making the module-level `facilitator` start as `None` and
constructing it explicitly right before the server actually starts serving:
in `photobank/server.py`'s own `if __name__ == "__main__"` block for
standalone runs, and in `run_demo.py:start_photobank_server()` for the
demo (after `_check_config()` has already confirmed `WHITECHAIN_RPC_URL`
is set). `get_photo` also now returns a clean 503 instead of crashing if
somehow called before the facilitator is initialized. Test harnesses
(`tests/local_integration_test.py`, `tests/test_security_fixes.py`)
already construct their own facilitator explicitly and were unaffected
apart from one that needed the same treatment already in place.

Confirmed with a new test
(`tests/test_security_fixes.py::test_chain_id_mismatch_fails_fast`): a
facilitator pointed at a local test chain with a deliberately wrong
`config.WHITECHAIN_CHAIN_ID` refuses to construct; the same setup with the
correct chain ID works. Also manually confirmed the RPC-unreachable path
raises a clean `RuntimeError` without leaking the RPC URL.

### M-4. Replay protection breaks under multiple worker processes
**File:** `facilitator/whitechain_facilitator.py:33-52` · **Status: NOT FIXED — documented limitation**

`_used_tx` is an in-memory `set`, guarded by a `threading.Lock`, persisted
to a JSON file on every successful verification. Within **one process**
this is correct (confirmed by test: concurrent-in-thread requests replaying
the same `tx_hash` are correctly serialized and the second is rejected —
FastAPI runs sync `def` routes in a thread pool, and the lock is a real OS
lock, so this works).

It stops being correct the moment photobank is run with more than one
**process** — e.g. `uvicorn photobank.server:app --workers 4`, a completely
standard production scaling step. Each worker process gets its own
independent `WhitechainFacilitator` with its own in-memory `_used_tx`
loaded once at startup; two requests replaying the same `tx_hash` against
two different workers can both pass the "not yet used" check before either
one's write lands on disk. `run_demo.py`/`photobank/server.py`'s
`if __name__ == "__main__"` block runs single-process today, so this is
**not currently live**, but it's a sharp edge for anyone who "takes this to
production" (the README's stated goal) by adding workers without realizing
replay protection quietly degrades.

Also: `_save_used_tx` (`facilitator/whitechain_facilitator.py:51`) does a
plain `Path.write_text(...)`, not an atomic write (write-to-temp +
rename). A crash mid-write can leave the file truncated/corrupt, and
`_load_used_tx` has no error handling around `json.loads` — a corrupt file
means the **server fails to start** on next boot until the file is fixed
or deleted (deleting it also silently resets replay protection, so a
crash-then-manual-fix cycle could reopen previously-closed replay windows).

**Not fixed** — this is an architecture decision (single-process-only vs.
adding a shared backing store like Redis/SQLite with real locking), noted
in the README's Security Notes pointing here rather than silently patched.

---

## Low

### L-1. Wallet private keys are printed to stdout by design
**File:** `wallets/setup_wallets.py:98,101`

`python -m wallets.setup_wallets` prints both freshly-generated private
keys to the terminal so you can copy them into `.env` — necessary UX for a
one-time setup script, but a real secret-hygiene risk given this project's
own plan involves *recording a demo video* of terminal output
(`docs/demo-video-script.md`). A key visible in a screen recording,
terminal scrollback, or shell history is compromised the moment it's
funded. **Fixed:** added an explicit warning in the README's new Security
Notes section. Not changed in code — printing the key is the intended,
simplest way to get it into `.env` for an MVP, and there's no good silent
alternative that isn't more complex (e.g., writing straight into `.env`
automatically) without your buy-in.

### L-2. `.gitignore` was missing generated runtime files
**File:** `.gitignore` · **Status: FIXED**

`.facilitator_used_tx.json` (replay-protection ledger, written to CWD by
default) and `downloaded_photos/` (where `author/agent.py` saves purchased
images) were not ignored. Neither contains secrets — tx hashes and
purchased stock photos are already public/non-sensitive — but a careless
`git add -A` after a local run would commit run-specific noise into the
repo. Added both to `.gitignore`.

### L-3. Money represented as `float` throughout
**Files:** `facilitator/whitechain_facilitator.py:119` (`amount_wbt = float(Web3.from_wei(...))`), `config.py` (`PHOTO_PRICE_WBT`, `AUTHOR_MAX_SPEND_WBT`), `author/x402_client.py` (`SpendLedger`)

Prices and balances are plain Python `float`, not `Decimal`/integer-wei.
I specifically tested for the classic float-money failure mode (values
that don't round-trip cleanly through `to_wei`/`from_wei`) using the actual
prices in this codebase (0.02, 0.1, 0.3, and a repeating-decimal case) and
**did not find an active rounding bug** — `web3.py`'s `to_wei`/`from_wei`
round-trip through `Decimal` internally and reproduced every test value
exactly. So this is a hardening recommendation, not a demonstrated
exploit: as the codebase grows (more price tiers, smaller/larger amounts,
different currencies), float accumulation error becomes a real risk again.
Recommend moving to integer wei or `Decimal` throughout before scaling
past a single fixed demo price. Not fixed (touches amount-comparison code
directly, which is exactly the "payment logic" you asked not be touched
without sign-off).

### L-4. Single confirmation (`min_confirmations=1`), no reorg protection
**File:** `facilitator/whitechain_facilitator.py:59,128-130`

A payment is accepted the moment it has one confirmation. If Whitechain
testnet ever reorgs a very recent block, a payment could be accepted and
the resource delivered, then the underlying transaction disappears from the
canonical chain — an irreversible-in-practice case of "got paid on paper,
not really." Reasonable default for a low-value testnet demo given the
chain's fast finality claims; would need raising for any real-value
deployment. Not fixed (changes verification behavior).

### L-5. No explicit application-level cap on the agent's tool-use loop
**File:** `author/agent.py:123-124` (`while True: ... self.client.messages.create(...)`)

Only WBT spend is capped (`AUTHOR_MAX_SPEND_WBT`); the number of
Claude API round-trips per task is unbounded. A confused model (or an
adversarial task description, if this were ever wired to accept untrusted
input) that never reaches `stop_reason != "tool_use"` would loop
indefinitely, burning Anthropic API credits and wall-clock time with no
circuit breaker. Requesting a non-existent photo is already safe (clean
404 before any payment is attempted — verified), so this isn't a money
risk, just an API-cost/availability one. Not fixed — changes agent control
flow, and the right cap value is a product decision, not mine to set.

### L-6. Dependencies: clean
**Files:** `requirements.txt`, `requirements-dev.txt`

Ran `pip-audit` (via OSV database) against every package actually resolved
from both files (64 packages from `requirements.txt`, 32 more transitively
from `requirements-dev.txt`, including `web3`, `fastapi`, `anthropic`,
`requests`, `pillow`, `eth-account`, `eth-tester[py-evm]` and all their
sub-dependencies). **Zero known vulnerabilities found** in the currently
installed/latest versions. Note: `requirements.txt` uses `>=` floors, not
pinned exact versions, so a fresh `pip install` next month could silently
pull in a version with a newly-disclosed CVE with no warning. Recommend
either pinning exact versions with a lockfile, or adding a periodic
`pip-audit` check (e.g. in CI) — not done here since it's a process change
outside this review's scope, not a code fix.

---

## Secrets scan (git history + code)

- `.env` has **never** been tracked by git at any point in this
  repository's history (`git log --all --oneline -- .env` returns nothing;
  confirmed by walking every file ever added across all commits/branches).
  `.gitignore` correctly excludes it.
- Searched every commit's full diff content (`git log --all -p`) for
  `PRIVATE_KEY=<hex>`, `sk-ant-...`, `ANTHROPIC_API_KEY=<value>`, and any
  standalone 64-hex-character string (raw private-key shape). The only
  64-hex matches found are the three **public** transaction hashes you
  asked to be added to the README's on-chain-proof section — those are not
  secrets (transaction hashes are meant to be public).
- Checked commit messages and the reflog for anything suspicious — clean.
- No file ever committed under a name suggesting a key/credential/wallet
  backup.
- **Conclusion: no history rewrite (BFG / `git filter-repo`) is needed —
  nothing to clean up.** If this ever changes (e.g. someone force-commits
  `.env` in the future), treat any key that touched git history — even
  briefly, even if later removed — as permanently compromised and rotate
  it; a removed commit is still recoverable from anyone's local clone or
  the reflog until history is rewritten and force-pushed everywhere.
- In application code, the only place a private key is ever exposed
  outside of `.env`/`config.py` variable passing is the intentional,
  one-time `wallets/setup_wallets.py` stdout print discussed in L-1.
  Confirmed no private key is ever included in an HTTP response, log
  line, or error message anywhere in `facilitator/`, `photobank/`, or
  `author/`.

---

## What was fixed vs. left for you

**Fixed in this review (safe, unambiguous, don't change payment-verification
outcomes for any valid input):**
- C-1 — stopped leaking RPC exception text (possible embedded API key) to
  clients; now logged server-side only.
- Added a strict whitelist (`^[A-Za-z0-9_-]+$`) for `/photo/{name}` as
  defense-in-depth against path traversal (empirically confirmed the
  underlying `IMAGES_DIR / f"{name}.png"` join is *not* traversal-safe by
  itself — `pathlib`'s `/` operator discards the left side entirely when
  the right side looks absolute, e.g. `Path("/safe") / "/etc/passwd"` ==
  `Path("/etc/passwd")` — currently only protected by Starlette's default
  refusal to put a literal `/` into a single-segment `{name}`, which is
  fragile, framework-behavior-dependent protection, not app-level
  defense).
- Fixed an unhandled `AttributeError` (500 crash) when `X-PAYMENT`'s
  `txHash` field is not a string (int/null/list) — now a clean 400.
- Added an explicit 4KB cap on the `X-PAYMENT` header before
  base64/JSON parsing (defense-in-depth; legitimate payloads are a few
  dozen bytes).
- Added `.facilitator_used_tx.json` and `downloaded_photos/` to
  `.gitignore`.
- Added a "Security Notes" section to `README.md`: don't expose the
  photobank server publicly without auth/HTTPS, don't screen-record wallet
  generation, testnet funds only, and a pointer to this file.

**Fixed in a follow-up pass, with your explicit sign-off (these do change
payment-verification behavior, which is why they'd been held back initially):**
- M-1 — payments are now bound to the specific resource they were paid for,
  via signed on-chain calldata (the "correct" option 2 from the original
  proposal, not the cheap first-come-first-served lock). See the M-1
  section above for the full before/after.
- M-3 — the facilitator now asserts `chain_id` matches Whitechain testnet
  (2625) at construction time and fails fast with a clear error otherwise,
  instead of silently trusting whatever chain its RPC happens to answer
  for. See the M-3 section above.

**Still left for your decision (documented above with a concrete proposed
patch) because they change spend-limit or replay-protection behavior:**
- M-2 — `SpendLedger` has no locking (real, reproduced race; not reachable
  via the current sequential agent loop).
- M-4 — replay protection is single-process-only; document or add a real
  shared store before ever running with multiple workers.
- L-3 — float-for-money hardening (no active bug found, but recommend
  Decimal/int wei before scaling).
- L-4 — 1-confirmation default (fine for demo, not for real value).
- L-5 — no cap on agent tool-use loop iterations/API cost.

---

## Overall risk assessment

**Current state — local machine, testnet-only, single operator, server bound
to `127.0.0.1`, you are the only person who can reach it:** **Low.** The one
Critical finding (RPC-secret leak) and both Medium findings that were
reachable in principle by a party who could reach the server (M-1) or a
misconfigured RPC (M-3) are fixed. Everything still open (M-2, M-4)
requires either a second concurrent process, or scaling decisions (multiple
workers, real pricing tiers, mainnet funds) that haven't been made yet.
Safe to keep developing and demoing as-is.

**If you deploy the photobank server publicly reachable, or move to
mainnet with real value:** treat this as **not ready.** At minimum, before
that step: resolve M-2 (ledger locking) if you ever parallelize purchases,
M-4 (replay protection needs a real shared store, not a single process's
in-memory set + plain file), add authentication in front of the server, and
get a second set of eyes / a professional audit — this review is thorough
but is not a substitute for one, especially before holding real funds.
