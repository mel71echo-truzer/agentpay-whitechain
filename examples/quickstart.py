"""examples/quickstart.py — buy a resource with the AgentPay SDK, end-to-end.

Self-contained: spins up a local in-memory chain, deploys the contracts, seeds a
KYA-verified buyer, starts the AI Service Provider + facilitator in a thread, and
then uses ONLY the public SDK to purchase a photo. No testnet, no keys, no money.

Run:  python examples/quickstart.py

The point is the last ~5 lines — everything above the "=== SDK ===" banner is the
one-time environment a real integrator would already have (a running provider on
Whitechain testnet). The SDK usage itself is tiny.
"""

import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn
from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chain
import config


def _bootstrap_local_provider() -> str:
    """Deploy contracts + start the provider/facilitator locally. Returns the
    buyer's private key. (In production this whole function is replaced by
    'point the SDK at a running provider on Whitechain testnet'.)"""
    config.NETWORK = "local"
    config.SETTLEMENT_MODE = "legacy"  # simplest path for a demo; SDK is identical either way
    config.USE_MOCK_SOUL = True

    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]

    deployer = Account.create()
    facilitator_acct = Account.create()
    seller = Account.create()
    buyer = Account.create()
    for acct in (deployer, facilitator_acct):
        w3.eth.send_transaction({"from": faucet, "to": acct.address, "value": Web3.to_wei(10, "ether")})

    # Deploy tEURC + the WB Soul mocks.
    teurc_addr = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    is_verified_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulRegistry", is_verified_addr, sbt_addr)

    config.TEURC_ADDRESS = teurc_addr
    config.SOUL_REGISTRY_ADDRESS = soul_addr
    config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS = soul_addr
    config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = soul_addr
    config.IS_VERIFIED_ATTRIBUTE_ADDRESS = is_verified_addr
    config.SBT_COLLECTION_ADDRESS = sbt_addr
    config.FACILITATOR_WALLET_ADDRESS = facilitator_acct.address
    config.FACILITATOR_WALLET_PRIVATE_KEY = facilitator_acct.key.hex()
    config.SERVICE_PROVIDER_WALLET_ADDRESS = seller.address
    config.SERVICE_PROVIDER_WALLET_PRIVATE_KEY = seller.key.hex()
    config.CHAIN_ID = w3.eth.chain_id

    teurc = chain.get_contract(w3, "tEURC", teurc_addr)
    soul = chain.get_contract(w3, "MockSoulRegistry", soul_addr)

    # Fund + KYA-verify the buyer.
    w3.eth.wait_for_transaction_receipt(
        chain.send_contract_tx(w3, deployer.key.hex(), teurc.functions.mint(buyer.address, 10_000_000))
    )
    w3.eth.wait_for_transaction_receipt(
        chain.send_contract_tx(w3, deployer.key.hex(), soul.functions.registerSoul(buyer.address))
    )
    soul_id = soul.functions.soulOf(buyer.address).call()
    w3.eth.wait_for_transaction_receipt(
        chain.send_contract_tx(w3, deployer.key.hex(), soul.functions.setVerified(soul_id, True))
    )

    # Start the provider + facilitator in a background thread.
    import service_provider.server as sp
    from facilitator.store import Store
    from facilitator.whitechain_facilitator import WhitechainFacilitator

    sp.init_facilitator(WhitechainFacilitator(w3=w3, store=Store(":memory:")))
    server = uvicorn.Server(
        uvicorn.Config(sp.app, host=config.SERVICE_PROVIDER_HOST, port=config.SERVICE_PROVIDER_PORT, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            requests.get(f"{config.SERVICE_PROVIDER_BASE_URL}/registry/capabilities", timeout=1)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.2)
    return buyer.key.hex()


def main() -> None:
    buyer_key = _bootstrap_local_provider()

    # ============================ SDK ============================
    from agentpay_sdk import AgentPayClient, PaymentFailed

    client = AgentPayClient(
        private_key=buyer_key,
        registry_url=config.SERVICE_PROVIDER_BASE_URL,
        chain_id=config.CHAIN_ID,
    )

    try:
        result = client.purchase(config.CAPABILITY_TYPE, "/photo/kyiv-lavra")
    except PaymentFailed as exc:
        print(f"payment refused: {exc}")
        raise SystemExit(1)

    out = Path("kyiv-lavra.png")
    out.write_bytes(result.content)
    print("✅ purchased /photo/kyiv-lavra via the SDK")
    print(f"   {len(result.content)} bytes → {out}")
    print(f"   reputation_tier={result.reputation_tier}  fee={result.fee_teurc} tEURC  relay_tx={result.relay_tx_hash}")


if __name__ == "__main__":
    main()
