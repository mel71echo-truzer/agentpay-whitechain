"""Phase 7 H-3 — atomic KYA: the unified pipeline settles through AgentPayRouter,
where the ON-CHAIN security boundary lives (isRelayer + _requireKYA + derived-nonce
binding). Real atomic execution on a local eth-tester EVM (the router actually
runs). Real Whitechain testnet is blocked by this environment's egress — that is
the separate A–F step.

Proves: successful KYA atomic settlement; a buyer who passes the APP-level TrustGate
but is NOT KYA'd on-chain is rejected by the router (TrustGate is only a pre-check,
the router is the guarantee); an unauthorized relayer is rejected; replay is
rejected; and even bypassing the validator, a substituted seller is rejected
on-chain. Money never moves on any rejection.
"""

import os
import sys
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import chain  # noqa: E402
import config  # noqa: E402
from facilitator.atomic_settlement import AtomicSettlementEngine  # noqa: E402
from unified.adapters import StandardX402Adapter  # noqa: E402
from unified.adapters.payment_validator import UnifiedPaymentValidator  # noqa: E402
from unified.adapters.settlement import AtomicFacilitatorSettlementEngine  # noqa: E402
from unified.adapters.trust import FacilitatorTrustGate  # noqa: E402
from unified.models import PaymentAsset, Provider, Service  # noqa: E402
from unified.payment import PaymentAuthorization  # noqa: E402
from unified.pipeline import UnifiedResourceServer  # noqa: E402

FEE_BPS = 50
PRICE = 20_000
RESOURCE = "/photo/kyiv-lavra"


def _wait(w3, tx):
    return w3.eth.wait_for_transaction_receipt(tx, timeout=120)


class _Fix:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture()
def atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "local")
    chain._local_w3 = None
    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]
    deployer, facilitator_acct, seller, buyer, buyer_app_only = (Account.create() for _ in range(5))
    for a in (deployer, facilitator_acct):
        w3.eth.send_transaction({"from": faucet, "to": a.address, "value": Web3.to_wei(10, "ether")})

    teurc_addr = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    isv = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulRegistry", isv, sbt)
    router_kya = chain.deploy_contract(w3, deployer.key.hex(), "MockRouterKYA")
    router_addr = chain.deploy_contract(w3, deployer.key.hex(), "AgentPayRouter", teurc_addr, router_kya)

    for name, val in {
        "TEURC_ADDRESS": teurc_addr, "SOUL_REGISTRY_ADDRESS": soul_addr,
        "SOUL_ATTRIBUTE_REGISTRY_ADDRESS": soul_addr, "SOUL_BOUND_TOKEN_REGISTRY_ADDRESS": soul_addr,
        "IS_VERIFIED_ATTRIBUTE_ADDRESS": isv, "SBT_COLLECTION_ADDRESS": sbt,
        "FACILITATOR_WALLET_ADDRESS": facilitator_acct.address,
        "FACILITATOR_WALLET_PRIVATE_KEY": facilitator_acct.key.hex(),
        "SERVICE_PROVIDER_WALLET_ADDRESS": seller.address,
        "SERVICE_PROVIDER_WALLET_PRIVATE_KEY": seller.key.hex(),
        "CHAIN_ID": w3.eth.chain_id, "FACILITATOR_FEE_BPS": FEE_BPS, "SETTLEMENT_MODE": "atomic",
        "ROUTER_ADDRESS": router_addr, "ROUTER_KYA_ADDRESS": router_kya, "TREASURY_ADDRESS": deployer.address,
    }.items():
        monkeypatch.setattr(config, name, val)

    teurc = chain.get_contract(w3, "tEURC", teurc_addr)
    soul = chain.get_contract(w3, "MockSoulRegistry", soul_addr)
    router = chain.get_contract(w3, "AgentPayRouter", router_addr)
    kya = chain.get_contract(w3, "MockRouterKYA", router_kya)

    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), router.functions.setRelayer(facilitator_acct.address, True)))

    def _verify_app(acct, sid):
        _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), teurc.functions.mint(acct.address, 10_000_000)))
        _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), soul.functions.registerSoul(acct.address)))
        soul_id = soul.functions.soulOf(acct.address).call()
        _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), soul.functions.setVerified(soul_id, True)))
        return soul_id

    # buyer: verified app-level AND router-KYA
    _verify_app(buyer, 1)
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), kya.functions.setSoul(buyer.address, 1)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), kya.functions.setVerified(1, True)))
    # buyer_app_only: verified app-level but NOT in router-KYA (router must reject)
    _verify_app(buyer_app_only, 2)

    from facilitator.store import Store
    from facilitator.whitechain_facilitator import WhitechainFacilitator
    fac = WhitechainFacilitator(w3=w3, store=Store(":memory:"))

    yield _Fix(w3=w3, teurc=teurc, router=router, fac=fac, deployer=deployer, facilitator_acct=facilitator_acct,
               seller=seller, buyer=buyer, buyer_app_only=buyer_app_only, teurc_addr=teurc_addr, router_addr=router_addr)
    chain._local_w3 = None


def _atomic_engine(fx, relayer_key=None):
    return AtomicSettlementEngine(
        fx.w3, fx.teurc, fx.router,
        facilitator_private_key=relayer_key or fx.facilitator_acct.key.hex(),
        treasury_address=fx.deployer.address, fee_bps=FEE_BPS, teurc_decimals=6,
        wait_for_confirmation=True, confirmation_timeout=120,
    )


def _server(fx, *, relayer_key=None):
    validator = UnifiedPaymentValidator(chain_id=fx.w3.eth.chain_id, asset_address=fx.teurc_addr,
                                        settlement_mode="atomic", seller=fx.seller.address, fee_bps=FEE_BPS,
                                        router_address=fx.router_addr, token=fx.teurc)
    settlement = AtomicFacilitatorSettlementEngine(_atomic_engine(fx, relayer_key), seller=fx.seller.address,
                                                   fee_bps=FEE_BPS, router_address=fx.router_addr)
    service = Service(name="photo", category="image", price_units=PRICE, currency="tEURC",
                      network="whitechain-testnet", provider=Provider(provider_id=fx.seller.address, pay_to=fx.router_addr))
    return UnifiedResourceServer(adapter=StandardX402Adapter(), trust_gate=FacilitatorTrustGate(fx.fac.identity),
                                 settlement=settlement, payment_validator=validator, service=service,
                                 resource=RESOURCE, asset_address=fx.teurc_addr, min_reputation_tier=0)


def _atomic_header(fx, buyer, *, resource=RESOURCE, price=PRICE):
    payload = agent_client.build_and_sign_authorization(
        buyer.key.hex(), fx.router_addr, price, resource, fx.teurc_addr, fx.w3.eth.chain_id,
        settlement_mode="atomic", seller=fx.seller.address, fee_bps=FEE_BPS,
    )
    a = payload["authorization"]
    sig = bytes.fromhex(a["r"][2:]) + bytes.fromhex(a["s"][2:]) + bytes([a["v"]])
    auth = PaymentAuthorization(from_address=a["from"], to_address=a["to"], value_units=int(a["value"]),
                                valid_after=int(a["validAfter"]), valid_before=int(a["validBefore"]),
                                nonce=a["nonce"], signature="0x" + sig.hex(), asset=PaymentAsset.TEURC,
                                network="whitechain-testnet", mode="atomic", salt=payload["resource_salt"])
    return StandardX402Adapter().encode_x_payment_header(auth)


# ---- successful KYA atomic settlement (real router execution) ----
def test_atomic_happy_path_settles_through_router(atomic):
    fx = atomic
    seller_before = fx.teurc.functions.balanceOf(fx.seller.address).call()
    treasury_before = fx.teurc.functions.balanceOf(fx.deployer.address).call()
    buyer_before = fx.teurc.functions.balanceOf(fx.buyer.address).call()

    status, body = _server(fx).fulfill(_atomic_header(fx, fx.buyer))

    assert status == 200, body
    assert body["settlement"]["status"] == "confirmed"
    # on-chain split: seller +net, treasury(owner) +fee, buyer -price
    assert fx.teurc.functions.balanceOf(fx.seller.address).call() == seller_before + 19_900
    assert fx.teurc.functions.balanceOf(fx.deployer.address).call() == treasury_before + 100
    assert fx.teurc.functions.balanceOf(fx.buyer.address).call() == buyer_before - PRICE


# ---- non-KYA on-chain: passes APP TrustGate, rejected by the ROUTER ----
def test_non_router_kya_buyer_rejected_on_chain(atomic):
    fx = atomic
    buyer_before = fx.teurc.functions.balanceOf(fx.buyer_app_only.address).call()
    # buyer_app_only IS verified in MockSoulRegistry (app TrustGate allows) but NOT in MockRouterKYA.
    status, body = _server(fx).fulfill(_atomic_header(fx, fx.buyer_app_only))
    assert status == 402                               # router _requireKYA reverted
    assert body["settlement_status"] == "failed"
    assert fx.teurc.functions.balanceOf(fx.buyer_app_only.address).call() == buyer_before  # money not moved


# ---- unauthorized relayer rejected on-chain ----
def test_unauthorized_relayer_rejected(atomic):
    fx = atomic
    outsider = Account.create()
    fx.w3.eth.send_transaction({"from": fx.w3.eth.accounts[0], "to": outsider.address, "value": Web3.to_wei(5, "ether")})
    buyer_before = fx.teurc.functions.balanceOf(fx.buyer.address).call()
    # settlement engine submits as a relayer NOT on the router allow-list
    status, body = _server(fx, relayer_key=outsider.key.hex()).fulfill(_atomic_header(fx, fx.buyer))
    assert status == 402 and body["settlement_status"] == "failed"   # router NotRelayer
    assert fx.teurc.functions.balanceOf(fx.buyer.address).call() == buyer_before


# ---- replay rejected (nonce is single-use on-chain) ----
def test_atomic_replay_rejected(atomic):
    fx = atomic
    server = _server(fx)
    header = _atomic_header(fx, fx.buyer)
    assert server.fulfill(header)[0] == 200
    seller_after_first = fx.teurc.functions.balanceOf(fx.seller.address).call()
    status, _ = server.fulfill(header)                 # same authorization again
    assert status == 402
    assert fx.teurc.functions.balanceOf(fx.seller.address).call() == seller_after_first  # not settled twice


# ---- router is the boundary: even bypassing the validator, a wrong seller reverts ----
def test_wrong_seller_bypassing_validator_reverts_on_chain(atomic):
    fx = atomic
    # Build a canonical atomic authorization honestly, then settle it with an engine
    # whose seller is a DIFFERENT address (simulating a relayer trying to redirect).
    payload = agent_client.build_and_sign_authorization(
        fx.buyer.key.hex(), fx.router_addr, PRICE, RESOURCE, fx.teurc_addr, fx.w3.eth.chain_id,
        settlement_mode="atomic", seller=fx.seller.address, fee_bps=FEE_BPS)
    a = payload["authorization"]
    sig = bytes.fromhex(a["r"][2:]) + bytes.fromhex(a["s"][2:]) + bytes([a["v"]])
    auth = PaymentAuthorization(from_address=a["from"], to_address=a["to"], value_units=int(a["value"]),
                                valid_after=int(a["validAfter"]), valid_before=int(a["validBefore"]), nonce=a["nonce"],
                                signature="0x" + sig.hex(), asset=PaymentAsset.TEURC, network="whitechain-testnet",
                                mode="atomic", salt=payload["resource_salt"], resource=RESOURCE)
    attacker = Account.create().address
    rogue = AtomicFacilitatorSettlementEngine(_atomic_engine(fx), seller=attacker, fee_bps=FEE_BPS,
                                              router_address=fx.router_addr)
    seller_before = fx.teurc.functions.balanceOf(fx.seller.address).call()
    result = rogue.settle(auth)                        # router recomputes nonce with attacker -> tEURC reverts
    assert result.ok is False
    assert fx.teurc.functions.balanceOf(fx.seller.address).call() == seller_before  # nothing moved
