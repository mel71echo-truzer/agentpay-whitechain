"""Спільні фікстури для pytest: розгортає tEURC + MockSoulRegistry на
локальному EthereumTesterProvider (config.NETWORK="local") і готує трьох
агентів у різних станах KYA/reputation:

  - verified_agent        — Soul + IsVerified + 1 SBT (reputation_tier=1)
  - verified_no_sbt_agent — Soul + IsVerified, без SBT (reputation_tier=0)
  - unverified_agent      — без Soul узагалі

Ніякого реального RPC чи ключів — усе на eth-tester, як і решта офлайн-тестів
у цьому репозиторії.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chain  # noqa: E402
import config  # noqa: E402
import money  # noqa: E402


@dataclass
class FacilitatorFixture:
    w3: Web3
    teurc: object
    soul_registry: object
    facilitator: object
    deployer: LocalAccount
    facilitator_acct: LocalAccount
    service_provider_acct: LocalAccount
    verified_agent: LocalAccount
    verified_no_sbt_agent: LocalAccount
    unverified_agent: LocalAccount
    # Гроші — int wei (рішення №4). Ціни зберігаємо як wei; teurc-рядки лишаємо
    # лише для читабельності намірів тесту.
    price_wei: int = 20_000  # 0.02 tEURC
    premium_price_wei: int = 100_000  # 0.10 tEURC
    premium_min_reputation_tier: int = 1


@pytest.fixture()
def facilitator_setup(tmp_path):
    config.NETWORK = "local"
    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]

    deployer = Account.create()
    facilitator_acct = Account.create()
    service_provider_acct = Account.create()
    verified_agent = Account.create()
    verified_no_sbt_agent = Account.create()
    unverified_agent = Account.create()

    for acct in (deployer, facilitator_acct):
        w3.eth.send_transaction({"from": faucet, "to": acct.address, "value": Web3.to_wei(10, "ether")})

    teurc_addr = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    is_verified_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul_registry_addr = chain.deploy_contract(
        w3, deployer.key.hex(), "MockSoulRegistry", is_verified_addr, sbt_addr
    )

    config.TEURC_ADDRESS = teurc_addr
    config.SOUL_REGISTRY_ADDRESS = soul_registry_addr
    config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS = soul_registry_addr
    config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = soul_registry_addr
    config.IS_VERIFIED_ATTRIBUTE_ADDRESS = is_verified_addr
    config.SBT_COLLECTION_ADDRESS = sbt_addr
    config.FACILITATOR_WALLET_ADDRESS = facilitator_acct.address
    config.FACILITATOR_WALLET_PRIVATE_KEY = facilitator_acct.key.hex()
    config.SERVICE_PROVIDER_WALLET_ADDRESS = service_provider_acct.address
    config.CHAIN_ID = w3.eth.chain_id
    config.FACILITATOR_FEE_BPS = 50
    config.SPEND_LEDGER_PATH = str(tmp_path / "spend_ledger.json")
    config.AUTHOR_MAX_SPEND_WEI = money.teurc_to_wei("1.0", config.TEURC_DECIMALS)

    teurc = chain.get_contract(w3, "tEURC", teurc_addr)
    soul_registry = chain.get_contract(w3, "MockSoulRegistry", soul_registry_addr)

    for agent in (verified_agent, verified_no_sbt_agent, unverified_agent):
        tx = chain.send_contract_tx(w3, deployer.key.hex(), teurc.functions.mint(agent.address, 10_000_000))
        w3.eth.wait_for_transaction_receipt(tx)

    for agent in (verified_agent, verified_no_sbt_agent):
        tx = chain.send_contract_tx(w3, deployer.key.hex(), soul_registry.functions.registerSoul(agent.address))
        w3.eth.wait_for_transaction_receipt(tx)
        soul_id = soul_registry.functions.soulOf(agent.address).call()
        tx = chain.send_contract_tx(w3, deployer.key.hex(), soul_registry.functions.setVerified(soul_id, True))
        w3.eth.wait_for_transaction_receipt(tx)

    verified_soul_id = soul_registry.functions.soulOf(verified_agent.address).call()
    tx = chain.send_contract_tx(w3, deployer.key.hex(), soul_registry.functions.issueSBT(verified_soul_id, 1))
    w3.eth.wait_for_transaction_receipt(tx)

    from facilitator.store import Store
    from facilitator.whitechain_facilitator import WhitechainFacilitator

    # Ізольоване in-memory сховище на кожен тест (agent_stats/events/capabilities),
    # щоб completed_payments одного тесту не текли в інший.
    store = Store(":memory:")
    facilitator = WhitechainFacilitator(w3=w3, store=store)

    return FacilitatorFixture(
        w3=w3,
        teurc=teurc,
        soul_registry=soul_registry,
        facilitator=facilitator,
        deployer=deployer,
        facilitator_acct=facilitator_acct,
        service_provider_acct=service_provider_acct,
        verified_agent=verified_agent,
        verified_no_sbt_agent=verified_no_sbt_agent,
        unverified_agent=unverified_agent,
    )
