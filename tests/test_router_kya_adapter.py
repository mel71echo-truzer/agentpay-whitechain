"""Тести підключуваного KYA-адаптера роутера (facilitator/router_kya_adapter.py).

Перевіряють, що:
  - MockRouterKYAAdapter реально читає/сіє MockRouterKYA (робочий шлях сьогодні);
  - фабрика обирає адаптер за прапорцем use_mock (USE_MOCK_SOUL);
  - реальний WB Soul-адаптер СВІДОМО не готовий — кидає NotImplementedError із
    TODO, а не тихо вдає верифікацію проти непідтвердженої схеми.
"""

import sys
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chain  # noqa: E402
from facilitator.router_kya_adapter import (  # noqa: E402
    MockRouterKYAAdapter,
    WBSoulRouterKYAAdapter,
    get_router_kya_adapter,
)


@pytest.fixture()
def kya_on_chain():
    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]
    deployer = Account.create()
    w3.eth.send_transaction({"from": faucet, "to": deployer.address, "value": Web3.to_wei(10, "ether")})
    kya_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockRouterKYA")
    return w3, deployer.key.hex(), kya_addr


def test_mock_adapter_seeds_and_reads(kya_on_chain):
    w3, deployer_key, kya_addr = kya_on_chain
    adapter = MockRouterKYAAdapter(w3, kya_addr)

    verified = Account.create().address
    unseeded = Account.create().address

    # Спочатку нікого не засіяно: soulOf == 0, isVerified(0) == False.
    assert adapter.soul_of(verified) == 0
    assert adapter.is_verified(0) is False

    adapter.seed_verified(deployer_key, [verified])

    soul_id = adapter.soul_of(verified)
    assert soul_id == 1  # soul_id стартує з 1 (0 = «немає душі» у роутері)
    assert adapter.is_verified(soul_id) is True
    # Незасіяний агент лишається без душі.
    assert adapter.soul_of(unseeded) == 0
    assert adapter.address == kya_addr


def test_factory_use_mock_true_returns_mock(kya_on_chain):
    w3, _deployer_key, kya_addr = kya_on_chain
    adapter = get_router_kya_adapter(w3, kya_addr, use_mock=True)
    assert isinstance(adapter, MockRouterKYAAdapter)


def test_factory_use_mock_false_returns_real_stub(kya_on_chain):
    w3, _deployer_key, kya_addr = kya_on_chain
    adapter = get_router_kya_adapter(w3, kya_addr, use_mock=False)
    assert isinstance(adapter, WBSoulRouterKYAAdapter)
    assert adapter.address == kya_addr


def test_real_adapter_refuses_until_schema_confirmed(kya_on_chain):
    """Реальний WB Soul-адаптер має ЯВНО падати з TODO, а не мовчати."""
    w3, deployer_key, kya_addr = kya_on_chain
    adapter = WBSoulRouterKYAAdapter(w3, kya_addr)

    with pytest.raises(NotImplementedError):
        adapter.is_verified(1)
    with pytest.raises(NotImplementedError):
        adapter.soul_of(Account.create().address)
    # Сіяти реальний WB Soul з коду теж не можна (це WhiteBIT KYC).
    with pytest.raises(NotImplementedError):
        adapter.seed_verified(deployer_key, [Account.create().address])
