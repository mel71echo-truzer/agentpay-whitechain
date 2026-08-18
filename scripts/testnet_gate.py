"""scripts/testnet_gate.py — Real Whitechain Testnet Gate A–F (Phase 7 closure).

Exercises the PRODUCTION atomic path end-to-end and produces the seven pieces of
evidence that close Phase 7. Runs identically in two modes (like scripts/demo.py):

  - NETWORK=local            — deploys everything on an in-memory EthereumTester
                               EVM. Used to smoke-test THIS harness's logic.
  - NETWORK=whitechain_testnet — reads deployed addresses/keys from .env, seeds
                               only the gate's own buyers, and runs against REAL
                               Whitechain testnet (real blocks, real TXs).

The seven evidences (mapped to gates):

  A  Atomic settlement (happy path)      → real atomic settle #1 + balances #6
  B  On-chain KYA rejection              → #2 (router is the boundary, not the app)
  C  Replay rejection                    → #3 (consumed nonce rejected on-chain)
  D  Resource A→B rejection (H-2)        → #4 (auth bound to its resource)
  E  CONFIRMED-only (M-2)                → #5 (SUBMITTED ⇒ 202, resource withheld)
  F  Reconciliation (M-3)                → #7 (held → real on-chain forward)

Every real transaction is captured with an explorer link, and buyer/seller/
treasury balances are recorded before/after the settling gates.

Nothing here changes product code, contracts, the token, or main. It is a test
harness. `.sol` is never touched; the atomic path and auto-retry are not altered.

Run:
    # local smoke test (no RPC needed)
    NETWORK=local SETTLEMENT_MODE=atomic python scripts/testnet_gate.py

    # real testnet (after `npx hardhat run deploy/deploy.ts --network whitechain_testnet`)
    NETWORK=whitechain_testnet SETTLEMENT_MODE=atomic python scripts/testnet_gate.py
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import chain  # noqa: E402
import config  # noqa: E402
from facilitator import store as store_mod  # noqa: E402
from facilitator.atomic_settlement import AtomicSettlementEngine  # noqa: E402
from facilitator.router_kya_adapter import get_router_kya_adapter  # noqa: E402
from facilitator.store import Store  # noqa: E402
from facilitator.whitechain_facilitator import WhitechainFacilitator  # noqa: E402
from unified.adapters import StandardX402Adapter  # noqa: E402
from unified.adapters.payment_validator import UnifiedPaymentValidator  # noqa: E402
from unified.adapters.settlement import AtomicFacilitatorSettlementEngine  # noqa: E402
from unified.adapters.trust import FacilitatorTrustGate  # noqa: E402
from unified.models import PaymentAsset, Provider, Service  # noqa: E402
from unified.payment import PaymentAuthorization  # noqa: E402
from unified.pipeline import UnifiedResourceServer  # noqa: E402

FEE_BPS = config.FACILITATOR_FEE_BPS or 50
PRICE = 20_000  # 0.02 tEURC (integer minimal units)


# ------------------------------------------------------------------ context ---

@dataclass
class Ctx:
    w3: object
    teurc: object          # tEURC contract handle
    teurc_addr: str
    router_addr: str
    kya: object            # RouterKYAAdapter
    facilitator: WhitechainFacilitator
    deployer_key: str
    seller: str
    treasury: str
    buyer_ok: object       # verified soul + router KYA seeded
    buyer_no_kya: object   # verified soul (app passes) but NOT router-KYA'd
    chain_id: int
    explorer: str
    evidence: list = field(default_factory=list)


def _explorer_tx(ctx: Ctx, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    return f"{ctx.explorer.rstrip('/')}/tx/{tx_hash}"


def _wait(w3, tx):
    return w3.eth.wait_for_transaction_receipt(tx, timeout=config.SETTLEMENT_CONFIRMATION_TIMEOUT)


def _bal(ctx: Ctx, addr: str) -> int:
    return ctx.teurc.functions.balanceOf(Web3.to_checksum_address(addr)).call()


def _balances(ctx: Ctx) -> dict:
    return {
        "buyer_ok": _bal(ctx, ctx.buyer_ok.address),
        "seller": _bal(ctx, ctx.seller),
        "treasury": _bal(ctx, ctx.treasury),
    }


# ------------------------------------------------------------------- setup ----

def _seed_soul(ctx: Ctx, soul_registry, addr: str) -> None:
    """Register + verify an address in the (attribute) WB Soul registry so the
    app-level TrustGate admits it. Used for BOTH buyers — B's whole point is that
    passing the app gate is NOT enough without on-chain router KYA."""
    _wait(ctx.w3, chain.send_contract_tx(ctx.w3, ctx.deployer_key, soul_registry.functions.registerSoul(addr)))
    sid = soul_registry.functions.soulOf(addr).call()
    _wait(ctx.w3, chain.send_contract_tx(ctx.w3, ctx.deployer_key, soul_registry.functions.setVerified(sid, True)))


def _setup_local() -> Ctx:
    config.NETWORK = "local"
    config.SETTLEMENT_MODE = "atomic"
    config.WAIT_FOR_CONFIRMATION = True
    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]
    deployer, facilitator, seller = (Account.create() for _ in range(3))
    for a in (deployer, facilitator):
        w3.eth.send_transaction({"from": faucet, "to": a.address, "value": Web3.to_wei(10, "ether")})

    teurc = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    isv = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulRegistry", isv, sbt)
    kya_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockRouterKYA")
    router = chain.deploy_contract(w3, deployer.key.hex(), "AgentPayRouter", teurc, kya_addr)

    config.TEURC_ADDRESS = teurc
    config.SOUL_REGISTRY_ADDRESS = config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS = config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = soul
    config.IS_VERIFIED_ATTRIBUTE_ADDRESS = isv
    config.SBT_COLLECTION_ADDRESS = sbt
    config.CHAIN_ID = w3.eth.chain_id
    config.FACILITATOR_WALLET_ADDRESS = facilitator.address
    config.FACILITATOR_WALLET_PRIVATE_KEY = facilitator.key.hex()
    config.SERVICE_PROVIDER_WALLET_ADDRESS = seller.address
    config.SERVICE_PROVIDER_WALLET_PRIVATE_KEY = seller.key.hex()
    config.FACILITATOR_FEE_BPS = FEE_BPS
    config.ROUTER_ADDRESS = router
    config.ROUTER_KYA_ADDRESS = kya_addr
    config.TREASURY_ADDRESS = deployer.address  # owner()=deployer collects fee locally
    config.USE_MOCK_SOUL = True

    router_c = chain.get_contract(w3, "AgentPayRouter", router)
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), router_c.functions.setRelayer(facilitator.address, True)))

    fac = WhitechainFacilitator(w3=w3, store=Store(":memory:"))
    ctx = Ctx(w3=w3, teurc=chain.get_contract(w3, "tEURC", teurc), teurc_addr=teurc, router_addr=router,
              kya=get_router_kya_adapter(w3, kya_addr, use_mock=True), facilitator=fac,
              deployer_key=deployer.key.hex(), seller=seller.address, treasury=deployer.address,
              buyer_ok=Account.create(), buyer_no_kya=Account.create(), chain_id=w3.eth.chain_id,
              explorer=config.WHITECHAIN_EXPLORER_URL)
    _seed_buyers(ctx, chain.get_contract(w3, "MockSoulRegistry", soul))
    return ctx


def _setup_testnet() -> Ctx:
    if config.SETTLEMENT_MODE != "atomic":
        _die("Gate A–F runs the PRODUCTION atomic path. Set SETTLEMENT_MODE=atomic.")
    required = ["WHITECHAIN_TESTNET_RPC", "TEURC_ADDRESS", "ROUTER_ADDRESS", "ROUTER_KYA_ADDRESS",
                "DEPLOYER_PRIVATE_KEY", "FACILITATOR_WALLET_PRIVATE_KEY", "SERVICE_PROVIDER_WALLET_ADDRESS",
                "SOUL_REGISTRY_ADDRESS", "IS_VERIFIED_ATTRIBUTE_ADDRESS"]
    missing = [k for k in required if not getattr(config, k)]
    if missing:
        _die(f"NETWORK=whitechain_testnet but .env is missing: {', '.join(missing)}. See DEPLOY_WHITECHAIN.md.")

    w3 = chain.get_w3()
    fac = WhitechainFacilitator(w3=w3, store=Store(":memory:"))
    ctx = Ctx(w3=w3, teurc=chain.get_contract(w3, "tEURC", config.TEURC_ADDRESS), teurc_addr=config.TEURC_ADDRESS,
              router_addr=config.ROUTER_ADDRESS,
              kya=get_router_kya_adapter(w3, config.ROUTER_KYA_ADDRESS, use_mock=config.USE_MOCK_SOUL),
              facilitator=fac, deployer_key=config.DEPLOYER_PRIVATE_KEY,
              seller=config.SERVICE_PROVIDER_WALLET_ADDRESS, treasury=config.TREASURY_ADDRESS,
              buyer_ok=Account.create(), buyer_no_kya=Account.create(), chain_id=w3.eth.chain_id,
              explorer=config.WHITECHAIN_EXPLORER_URL)
    if not config.USE_MOCK_SOUL:
        _die("This gate seeds its own throwaway buyers, which requires the mock Soul/KYA registries "
             "(USE_MOCK_SOUL=true). Against real WB Soul, seed pre-verified buyer keys and adapt _seed_buyers.")
    _seed_buyers(ctx, chain.get_contract(w3, "MockSoulRegistry", config.SOUL_REGISTRY_ADDRESS))
    return ctx


def _seed_buyers(ctx: Ctx, soul_registry) -> None:
    """Fund both buyers with tEURC; verify both souls (app gate); router-KYA ONLY
    buyer_ok. buyer_no_kya passes the app TrustGate but must be rejected on-chain."""
    for buyer in (ctx.buyer_ok, ctx.buyer_no_kya):
        _wait(ctx.w3, chain.send_contract_tx(ctx.w3, ctx.deployer_key, ctx.teurc.functions.mint(buyer.address, 10_000_000)))
        _seed_soul(ctx, soul_registry, buyer.address)
    # Router KYA (on-chain settlement boundary) — buyer_ok ONLY.
    ctx.kya.seed_verified(ctx.deployer_key, [ctx.buyer_ok.address])


# ------------------------------------------------------- atomic pipeline ------

def _atomic_server(ctx: Ctx, resource: str, *, wait_for_confirmation: bool = True) -> UnifiedResourceServer:
    teurc_c = ctx.teurc
    router_c = chain.get_contract(ctx.w3, "AgentPayRouter", ctx.router_addr)
    validator = UnifiedPaymentValidator(chain_id=ctx.chain_id, asset_address=ctx.teurc_addr, settlement_mode="atomic",
                                        seller=ctx.seller, fee_bps=FEE_BPS, router_address=ctx.router_addr, token=teurc_c)
    engine = AtomicSettlementEngine(ctx.w3, teurc_c, router_c, facilitator_private_key=config.FACILITATOR_WALLET_PRIVATE_KEY,
                                    treasury_address=ctx.treasury, fee_bps=FEE_BPS, teurc_decimals=6,
                                    wait_for_confirmation=wait_for_confirmation,
                                    confirmation_timeout=config.SETTLEMENT_CONFIRMATION_TIMEOUT)
    settlement = AtomicFacilitatorSettlementEngine(engine, seller=ctx.seller, fee_bps=FEE_BPS, router_address=ctx.router_addr)
    service = Service(name="gate", category="image", price_units=PRICE, currency="tEURC",
                      network="whitechain-testnet", provider=Provider(provider_id=ctx.seller, pay_to=ctx.router_addr))
    return UnifiedResourceServer(adapter=StandardX402Adapter(), trust_gate=FacilitatorTrustGate(ctx.facilitator.identity),
                                 settlement=settlement, payment_validator=validator, service=service,
                                 resource=resource, asset_address=ctx.teurc_addr, min_reputation_tier=0)


def _atomic_header(ctx: Ctx, buyer, resource: str) -> str:
    payload = agent_client.build_and_sign_authorization(
        buyer.key.hex(), ctx.router_addr, PRICE, resource, ctx.teurc_addr, ctx.chain_id,
        settlement_mode="atomic", seller=ctx.seller, fee_bps=FEE_BPS)
    a = payload["authorization"]
    sig = bytes.fromhex(a["r"][2:]) + bytes.fromhex(a["s"][2:]) + bytes([a["v"]])
    auth = PaymentAuthorization(from_address=a["from"], to_address=a["to"], value_units=int(a["value"]),
                                valid_after=int(a["validAfter"]), valid_before=int(a["validBefore"]), nonce=a["nonce"],
                                signature="0x" + sig.hex(), asset=PaymentAsset.TEURC, network="whitechain-testnet",
                                mode="atomic", salt=payload["resource_salt"])
    return StandardX402Adapter().encode_x_payment_header(auth)


# ------------------------------------------------------------------- gates ----

def _record(ctx: Ctx, gid: str, name: str, passed: bool, detail: str, **extra) -> None:
    ctx.evidence.append({"gate": gid, "name": name, "passed": passed, "detail": detail, **extra})
    tag = "PASS" if passed else "FAIL"
    print(f"[{tag}] {gid} — {name}: {detail}")


def gate_a(ctx: Ctx) -> None:
    """Atomic settlement happy path + balances #1/#6."""
    before = _balances(ctx)
    status, body = _atomic_server(ctx, "/gate/a").fulfill(_atomic_header(ctx, ctx.buyer_ok, "/gate/a"))
    after = _balances(ctx)
    tx = body.get("settlement", {}).get("relay_tx_hash") if status == 200 else None
    ok = (status == 200 and after["seller"] - before["seller"] == PRICE - PRICE * FEE_BPS // 10000
          and after["treasury"] - before["treasury"] == PRICE * FEE_BPS // 10000
          and before["buyer_ok"] - after["buyer_ok"] == PRICE)
    _record(ctx, "A", "Atomic settlement (CONFIRMED)", ok,
            f"status={status} net→seller={after['seller']-before['seller']} fee→treasury={after['treasury']-before['treasury']}",
            tx=tx, explorer=_explorer_tx(ctx, tx), balances_before=before, balances_after=after)


def gate_b(ctx: Ctx) -> None:
    """On-chain KYA rejection #2 — passes app gate, rejected by router _requireKYA."""
    before = _balances(ctx)
    status, body = _atomic_server(ctx, "/gate/b").fulfill(_atomic_header(ctx, ctx.buyer_no_kya, "/gate/b"))
    after = _balances(ctx)
    ok = status != 200 and after == before  # rejected, nothing moved
    _record(ctx, "B", "On-chain KYA rejection", ok,
            f"status={status} reason={body.get('error', body)!s:.80} balances_unchanged={after == before}")


def gate_c(ctx: Ctx) -> None:
    """Replay rejection #3 — consumed nonce cannot settle twice."""
    header = _atomic_header(ctx, ctx.buyer_ok, "/gate/c")
    server = _atomic_server(ctx, "/gate/c")
    s1, b1 = server.fulfill(header)
    s2, b2 = server.fulfill(header)
    tx = b1.get("settlement", {}).get("relay_tx_hash") if s1 == 200 else None
    ok = s1 == 200 and s2 != 200
    _record(ctx, "C", "Replay rejection", ok, f"first={s1} replay={s2} (used authorization rejected)",
            tx=tx, explorer=_explorer_tx(ctx, tx))


def gate_d(ctx: Ctx) -> None:
    """Resource A→B rejection #4 (H-2) — auth signed for resource A, presented to
    the server serving resource B (same price). The server pins its own resource
    and the validator re-derives the expected derived-nonce from it, so the signed
    nonce cannot match: rejected before any money moves."""
    header_for_a = _atomic_header(ctx, ctx.buyer_ok, "/gate/d-A")
    before = _balances(ctx)
    status, body = _atomic_server(ctx, "/gate/d-B").fulfill(header_for_a)  # served as B
    after = _balances(ctx)
    ok = status != 200 and after == before
    _record(ctx, "D", "Resource A→B rejection (H-2)", ok,
            f"status={status} stage={body.get('stage')} balances_unchanged={after == before}")


def gate_e(ctx: Ctx) -> None:
    """CONFIRMED-only #5 (M-2) — with wait_for_confirmation off, settlement is only
    SUBMITTED (broadcast, not yet mined). The pipeline must return 202 pending and
    WITHHOLD the resource. The relay still lands on-chain (real tx)."""
    server = _atomic_server(ctx, "/gate/e", wait_for_confirmation=False)
    status, body = server.fulfill(_atomic_header(ctx, ctx.buyer_ok, "/gate/e"))
    tx = body.get("relay_tx_hash")
    withheld = "result" not in body
    ok = status == 202 and body.get("settlement_status") == "submitted" and withheld
    if tx:  # let the broadcast mine so the nonce is settled on-chain for the record
        try:
            _wait(ctx.w3, tx)
        except Exception:  # noqa: BLE001 — confirmation is best-effort for the evidence log
            pass
    _record(ctx, "E", "CONFIRMED-only (M-2)", ok,
            f"status={status} settlement_status={body.get('settlement_status')} resource_withheld={withheld}",
            tx=tx, explorer=_explorer_tx(ctx, tx))


def gate_f(ctx: Ctx) -> None:
    """Reconciliation #7 (M-3) — a held settlement is resolved by a REAL on-chain
    forward. Safely forcing a real forward-revert on testnet is not reproducible,
    so the held origin is seeded; the RESOLUTION (facilitator→seller transfer) is a
    real on-chain transaction. dry_run must move nothing; the forward must move net
    and only reach RESOLVED_FORWARDED after on-chain confirmation; a repeat must be
    an idempotent no-op (no double pay)."""
    net = PRICE - PRICE * FEE_BPS // 10000
    relay_id = "0x" + "f0" * 32  # synthetic held-relay id for the seeded record
    # Fund the facilitator wallet with the net it is presumed to be holding.
    _wait(ctx.w3, chain.send_contract_tx(ctx.w3, ctx.deployer_key,
          ctx.teurc.functions.mint(config.FACILITATOR_WALLET_ADDRESS, net)))
    ctx.facilitator.store.record_held(relay_id, net_wei=net, seller=ctx.seller,
                                      buyer=ctx.buyer_ok.address, nonce="0x" + "ab" * 32)

    seller_before = _bal(ctx, ctx.seller)
    dry = ctx.facilitator.reconcile_held(relay_id, "forward", dry_run=True)
    seller_after_dry = _bal(ctx, ctx.seller)
    live = ctx.facilitator.reconcile_held(relay_id, "forward", dry_run=False)
    seller_after_live = _bal(ctx, ctx.seller)
    again = ctx.facilitator.reconcile_held(relay_id, "forward", dry_run=False)  # idempotent
    seller_after_again = _bal(ctx, ctx.seller)

    ok = (seller_after_dry == seller_before                                   # dry moved nothing
          and live["state"] == store_mod.RECON_RESOLVED_FORWARDED             # confirmed only
          and seller_after_live - seller_before == net                        # net forwarded
          and not again["moved_funds"] and seller_after_again == seller_after_live)  # no double pay
    tx = live.get("action_tx_hash")
    _record(ctx, "F", "Reconciliation forward (M-3)", ok,
            f"dry_moved={dry['moved_funds']} state={live['state']} net_forwarded={seller_after_live-seller_before} "
            f"idempotent_no_double={not again['moved_funds']}",
            tx=tx, explorer=_explorer_tx(ctx, tx))


# -------------------------------------------------------------------- main ----

def _die(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def main() -> None:
    net = config.NETWORK
    print(f"=== Real Testnet Gate A–F · network={net} · mode={config.SETTLEMENT_MODE} · fee_bps={FEE_BPS} ===")
    ctx = _setup_testnet() if net == "whitechain_testnet" else _setup_local()
    print(f"router={ctx.router_addr} teurc={ctx.teurc_addr} seller={ctx.seller} treasury={ctx.treasury}")
    print(f"buyer_ok={ctx.buyer_ok.address} buyer_no_kya={ctx.buyer_no_kya.address}\n")

    for gate in (gate_a, gate_b, gate_c, gate_d, gate_e, gate_f):
        try:
            gate(ctx)
        except Exception as exc:  # noqa: BLE001 — a gate crashing is itself a FAIL to report, not a traceback
            _record(ctx, gate.__name__[-1].upper(), gate.__doc__.split("—")[0].strip() if gate.__doc__ else gate.__name__,
                    False, f"harness error: {type(exc).__name__}: {exc}")

    passed = sum(1 for e in ctx.evidence if e["passed"])
    total = len(ctx.evidence)
    out_dir = Path(__file__).resolve().parent.parent / "gate_evidence"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"gate_{net}_{int(time.time())}.json"
    out_file.write_text(json.dumps(
        {"network": net, "mode": config.SETTLEMENT_MODE, "fee_bps": FEE_BPS, "chain_id": ctx.chain_id,
         "router": ctx.router_addr, "teurc": ctx.teurc_addr, "passed": passed, "total": total,
         "evidence": ctx.evidence}, indent=2))

    print(f"\n=== {passed}/{total} gates passed · evidence → {out_file} ===")
    if passed != total:
        sys.exit(1)


if __name__ == "__main__":
    main()
