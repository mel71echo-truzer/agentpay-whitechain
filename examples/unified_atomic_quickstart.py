"""examples/unified_atomic_quickstart.py — the PRODUCTION unified path (atomic).

    X-PAYMENT → PaymentValidator → TrustGate → AtomicSettlementEngine → AgentPayRouter → tEURC

Same pipeline as unified_quickstart.py, but settlement goes through AgentPayRouter,
so KYA is enforced ON-CHAIN (isRelayer + _requireKYA) and the resource/seller/fee
binding is bound into the buyer's signature (C-1). Self-contained on a local EVM —
real router execution. (Real Whitechain testnet is a separate step.)

Run:  python examples/unified_atomic_quickstart.py
"""

import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client
import chain
import config
from facilitator.atomic_settlement import AtomicSettlementEngine
from facilitator.store import Store
from facilitator.whitechain_facilitator import WhitechainFacilitator
from unified.adapters import StandardX402Adapter
from unified.adapters.payment_validator import UnifiedPaymentValidator
from unified.adapters.settlement import AtomicFacilitatorSettlementEngine
from unified.adapters.trust import FacilitatorTrustGate
from unified.models import PaymentAsset, Provider, Service
from unified.payment import PaymentAuthorization
from unified.pipeline import UnifiedResourceServer

FEE_BPS = 50
PRICE = 20_000
RESOURCE = "/photo/kyiv-lavra"


def _wait(w3, tx):
    return w3.eth.wait_for_transaction_receipt(tx, timeout=120)


def _setup(w3):
    config.NETWORK = "local"
    config.SETTLEMENT_MODE = "atomic"
    faucet = w3.eth.accounts[0]
    deployer, facilitator, seller, buyer = (Account.create() for _ in range(4))
    for a in (deployer, facilitator):
        w3.eth.send_transaction({"from": faucet, "to": a.address, "value": Web3.to_wei(10, "ether")})
    teurc = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    isv = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulRegistry", isv, sbt)
    kya = chain.deploy_contract(w3, deployer.key.hex(), "MockRouterKYA")
    router = chain.deploy_contract(w3, deployer.key.hex(), "AgentPayRouter", teurc, kya)

    config.TEURC_ADDRESS = teurc
    config.SOUL_REGISTRY_ADDRESS = config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS = config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = soul
    config.IS_VERIFIED_ATTRIBUTE_ADDRESS = isv
    config.SBT_COLLECTION_ADDRESS = sbt
    config.CHAIN_ID = w3.eth.chain_id
    # WhitechainFacilitator(atomic) builds its own engine from config — set the rest
    # so its construction succeeds (we only borrow fac.identity for the TrustGate).
    config.FACILITATOR_WALLET_ADDRESS = facilitator.address
    config.FACILITATOR_WALLET_PRIVATE_KEY = facilitator.key.hex()
    config.SERVICE_PROVIDER_WALLET_ADDRESS = seller.address
    config.SERVICE_PROVIDER_WALLET_PRIVATE_KEY = seller.key.hex()
    config.FACILITATOR_FEE_BPS = FEE_BPS
    config.ROUTER_ADDRESS = router
    config.ROUTER_KYA_ADDRESS = kya
    config.TREASURY_ADDRESS = deployer.address

    teurc_c = chain.get_contract(w3, "tEURC", teurc)
    soul_c = chain.get_contract(w3, "MockSoulRegistry", soul)
    router_c = chain.get_contract(w3, "AgentPayRouter", router)
    kya_c = chain.get_contract(w3, "MockRouterKYA", kya)
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), router_c.functions.setRelayer(facilitator.address, True)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), teurc_c.functions.mint(buyer.address, 10_000_000)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), soul_c.functions.registerSoul(buyer.address)))
    sid = soul_c.functions.soulOf(buyer.address).call()
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), soul_c.functions.setVerified(sid, True)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), kya_c.functions.setSoul(buyer.address, 1)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), kya_c.functions.setVerified(1, True)))

    fac = WhitechainFacilitator(w3=w3, store=Store(":memory:"))
    return deployer, facilitator, seller, buyer, teurc, router, teurc_c, fac


def _atomic_header(buyer, seller, teurc, router, chain_id):
    payload = agent_client.build_and_sign_authorization(
        buyer.key.hex(), router, PRICE, RESOURCE, teurc, chain_id,
        settlement_mode="atomic", seller=seller.address, fee_bps=FEE_BPS)
    a = payload["authorization"]
    sig = bytes.fromhex(a["r"][2:]) + bytes.fromhex(a["s"][2:]) + bytes([a["v"]])
    auth = PaymentAuthorization(from_address=a["from"], to_address=a["to"], value_units=int(a["value"]),
                                valid_after=int(a["validAfter"]), valid_before=int(a["validBefore"]), nonce=a["nonce"],
                                signature="0x" + sig.hex(), asset=PaymentAsset.TEURC, network="whitechain-testnet",
                                mode="atomic", salt=payload["resource_salt"])
    return StandardX402Adapter().encode_x_payment_header(auth)


def main() -> None:
    w3 = chain.get_w3()
    deployer, facilitator, seller, buyer, teurc, router, teurc_c, fac = _setup(w3)

    validator = UnifiedPaymentValidator(chain_id=w3.eth.chain_id, asset_address=teurc, settlement_mode="atomic",
                                        seller=seller.address, fee_bps=FEE_BPS, router_address=router, token=teurc_c)
    settlement = AtomicFacilitatorSettlementEngine(
        AtomicSettlementEngine(w3, teurc_c, chain.get_contract(w3, "AgentPayRouter", router),
                               facilitator_private_key=facilitator.key.hex(), treasury_address=deployer.address,
                               fee_bps=FEE_BPS, teurc_decimals=6),
        seller=seller.address, fee_bps=FEE_BPS, router_address=router)
    service = Service(name="photo", category="image", price_units=PRICE, currency="tEURC",
                      network="whitechain-testnet", provider=Provider(provider_id=seller.address, pay_to=router))
    server = UnifiedResourceServer(adapter=StandardX402Adapter(), trust_gate=FacilitatorTrustGate(fac.identity),
                                   settlement=settlement, payment_validator=validator, service=service,
                                   resource=RESOURCE, asset_address=teurc, min_reputation_tier=0)

    print("Production unified path: X-PAYMENT → Validator → TrustGate → AtomicSettlementEngine → Router → tEURC")
    status, body = server.fulfill(_atomic_header(buyer, seller, teurc, router, w3.eth.chain_id))
    if status == 200:
        s = body["settlement"]
        print(f"✅ RESULT: {body['result']} | ATOMIC {s['status']} {s['asset']} "
              f"net={s['net_units']} fee={s['fee_units']} tx={s['relay_tx_hash'][:12]}…")
        print(f"   on-chain: seller={teurc_c.functions.balanceOf(seller.address).call()} "
              f"treasury={teurc_c.functions.balanceOf(deployer.address).call()} "
              f"buyer={teurc_c.functions.balanceOf(buyer.address).call()}")
    else:
        print(f"❌ {status}: {body}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
