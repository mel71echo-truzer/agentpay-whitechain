"""examples/unified_quickstart.py — the unified pipeline, end-to-end, runnable.

    Agent → Discovery → Quality/Price/TaskFit → TrustGate → X-PAYMENT → SettlementEngine → Result

Self-contained: local in-memory chain, deploys the contracts, seeds a KYA-verified
buyer, then drives the UNIFIED pipeline (registry + marketplace scoring + trust
gate + standard x402 + settlement) in-process. No testnet, no keys, no money.

Run:  python examples/unified_quickstart.py
"""

import os
import sys
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client
import chain
import config
from unified.adapters import StandardX402Adapter
from unified.adapters.settlement import FacilitatorSettlementEngine
from unified.adapters.trust import FacilitatorTrustGate
from unified.payment import PaymentAuthorization
from unified.pipeline import MarketplaceSelector, UnifiedResourceServer
from unified.registry import QualityMetrics, UnifiedRegistry, sign_listing

RESOURCE = "/weather"


def _bootstrap(w3):
    config.NETWORK = "local"
    config.SETTLEMENT_MODE = "legacy"
    config.USE_MOCK_SOUL = True
    faucet = w3.eth.accounts[0]
    deployer, facilitator, seller, buyer = (Account.create() for _ in range(4))
    for acct in (deployer, facilitator):
        w3.eth.send_transaction({"from": faucet, "to": acct.address, "value": Web3.to_wei(10, "ether")})

    teurc = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    isv = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulRegistry", isv, sbt)

    config.TEURC_ADDRESS = teurc
    config.SOUL_REGISTRY_ADDRESS = config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS = config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = soul
    config.IS_VERIFIED_ATTRIBUTE_ADDRESS = isv
    config.SBT_COLLECTION_ADDRESS = sbt
    config.FACILITATOR_WALLET_ADDRESS = facilitator.address
    config.FACILITATOR_WALLET_PRIVATE_KEY = facilitator.key.hex()
    config.SERVICE_PROVIDER_WALLET_ADDRESS = seller.address
    config.SERVICE_PROVIDER_WALLET_PRIVATE_KEY = seller.key.hex()
    config.CHAIN_ID = w3.eth.chain_id

    teurc_c = chain.get_contract(w3, "tEURC", teurc)
    soul_c = chain.get_contract(w3, "MockSoulRegistry", soul)
    w3.eth.wait_for_transaction_receipt(chain.send_contract_tx(w3, deployer.key.hex(), teurc_c.functions.mint(buyer.address, 10_000_000)))
    w3.eth.wait_for_transaction_receipt(chain.send_contract_tx(w3, deployer.key.hex(), soul_c.functions.registerSoul(buyer.address)))
    sid = soul_c.functions.soulOf(buyer.address).call()
    w3.eth.wait_for_transaction_receipt(chain.send_contract_tx(w3, deployer.key.hex(), soul_c.functions.setVerified(sid, True)))
    return deployer, seller, facilitator, buyer, teurc


def _sign_x_payment(w3, agent, teurc_addr, value_units, pay_to) -> str:
    chain_ts = w3.eth.get_block("latest")["timestamp"]
    salt = os.urandom(32)
    nonce = Web3.keccak(RESOURCE.encode() + salt)
    message = {"from": agent.address, "to": Web3.to_checksum_address(pay_to), "value": int(value_units),
               "validAfter": 0, "validBefore": chain_ts + 3600, "nonce": nonce}
    domain = {"name": "Test EURC", "version": "1", "chainId": w3.eth.chain_id, "verifyingContract": teurc_addr}
    signed = Account.sign_message(encode_typed_data(domain, agent_client.TRANSFER_AUTH_TYPES, message), agent.key.hex())
    sig = signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([signed.v])
    auth = PaymentAuthorization(from_address=message["from"], to_address=message["to"], value_units=message["value"],
                                valid_after=0, valid_before=message["validBefore"], nonce="0x" + nonce.hex(),
                                signature="0x" + sig.hex())
    return StandardX402Adapter().encode_x_payment_header(auth)


def main() -> None:
    from facilitator.store import Store
    from facilitator.whitechain_facilitator import WhitechainFacilitator

    w3 = chain.get_w3()
    _deployer, seller, facilitator, buyer, teurc = _bootstrap(w3)
    fac = WhitechainFacilitator(w3=w3, store=Store(":memory:"))

    # --- DISCOVERY: two signed weather services (registry attests quality) ---
    reg = UnifiedRegistry()
    base = {"id": seller.address, "capability_type": "weather", "provider_url": "http://sp:8000",
            "pay_to": facilitator.address, "min_reputation_tier": 0}
    cheap = {**base, "price_wei": 20_000}
    premium = {**base, "price_wei": 60_000}
    reg.register(cheap, sign_listing(cheap, seller.key.hex()), QualityMetrics(4.2, 0.96, 180))
    reg.register(premium, sign_listing(premium, seller.key.hex()), QualityMetrics(4.9, 0.998, 90))
    services = reg.discover(category="weather", max_price_units=100_000)
    print(f"1. Discovery: {len(services)} weather services")

    # --- MARKETPLACE SCORING (trust NOT in the number) ---
    print("2. Scoring (Quality/Price/TaskFit):")
    for c in MarketplaceSelector().rank(services, "weather", budget_units=100_000):
        b = c.breakdown
        print(f"   {c.service.price_units} units | quality={b.quality_score:.3f} price={b.price_score:.3f} "
              f"task_fit={b.task_fit:.3f} → final={b.final_score:.3f}  (trust={b.trust_score:.3f}, separate)")
    chosen = MarketplaceSelector().select(services, "weather", budget_units=100_000)
    print(f"   → selected {chosen.service.price_units} units (final={chosen.breakdown.final_score:.3f})")

    # --- SERVER: standard x402 + TrustGate + SettlementEngine ---
    server = UnifiedResourceServer(
        adapter=StandardX402Adapter(),
        trust_gate=FacilitatorTrustGate(fac.identity),
        settlement=FacilitatorSettlementEngine(fac.settlement),
        service=chosen.service, resource=RESOURCE, asset_address=teurc, min_reputation_tier=0,
    )
    accept = server.payment_required()["accepts"][0]
    print(f"3. 402 (standard x402): maxAmountRequired={accept['maxAmountRequired']} payTo={accept['payTo'][:10]}…")

    # --- BUYER signs X-PAYMENT; server runs TrustGate → Settlement ---
    header = _sign_x_payment(w3, buyer, teurc, chosen.service.price_units, accept["payTo"])
    print("4. X-PAYMENT signed → TrustGate → SettlementEngine")
    status, body = server.fulfill(header)

    if status == 200:
        s = body["settlement"]
        print(f"5. ✅ RESULT: {body['result']} | settled {s['status']} {s['asset']} "
              f"net={s['net_units']} fee={s['fee_units']} tx={s['relay_tx_hash'][:12]}…")
    else:
        print(f"5. ❌ denied ({status}): {body}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
