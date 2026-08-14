"""Наскрізний тест атомарного settlement-шляху (SETTLEMENT_MODE=atomic).

Гонить ЖИВИЙ шлях facilitator.verify_and_settle через AgentPayRouter на
локальному eth-tester: підпис клієнта (ReceiveWithAuthorization + похідний
nonce) -> валідатор (atomic) -> AtomicSettlementEngine -> router.settlePaymentAtomic.

Перевіряє:
  - happy: продавець отримує net, скарбниця (owner роутера) — комісію, покупця списано;
  - комісія йде саме на treasury (owner()), а не на facilitator;
  - floor-залишок комісії дістається продавцю (той самий інваріант, що в контракті);
  - replay: повторний підпис -> чистий збій (SettlementError), кошти не рухалися;
  - НЕМАЄ стану funds-held: у atomic немає окремого форварду, тож
    SettlementForwardError неможливий, а list_held_settlements лишається порожнім.
"""

import sys
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import chain  # noqa: E402
import config  # noqa: E402
from facilitator.settlement import SettlementError  # noqa: E402


class AtomicFixture:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture()
def atomic_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "local")
    # Уся тестова сесія ділить ОДИН кешований eth-tester (chain.get_w3). PyEVM
    # рухає block.timestamp ~на 1с за блок, а цей тест майнить десятки блоків
    # (деплої+мінти+сеттли), тож накопичений timestamp може випхати legacy-тести
    # за їхнє 300с вікно авторизації. Даємо наступному тесту СВІЖИЙ ланцюг:
    # скидаємо кеш і до, і після — власна ізоляція, без правок спільного conftest.
    chain._local_w3 = None
    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]

    deployer = Account.create()      # owner роутера = скарбниця (отримувач комісії)
    facilitator_acct = Account.create()
    service_provider = Account.create()  # seller
    buyer = Account.create()

    for acct in (deployer, facilitator_acct):
        w3.eth.send_transaction({"from": faucet, "to": acct.address, "value": Web3.to_wei(10, "ether")})

    # tEURC + WB Soul (атрибутний) для Python identity-гейту.
    teurc_addr = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    is_verified_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
    sbt_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
    soul_registry_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulRegistry", is_verified_addr, sbt_addr)

    # Роутер + KYA-заглушка під його isVerified(uint256).
    router_kya_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockRouterKYA")
    router_addr = chain.deploy_contract(w3, deployer.key.hex(), "AgentPayRouter", teurc_addr, router_kya_addr)

    for name, val in {
        "TEURC_ADDRESS": teurc_addr,
        "SOUL_REGISTRY_ADDRESS": soul_registry_addr,
        "SOUL_ATTRIBUTE_REGISTRY_ADDRESS": soul_registry_addr,
        "SOUL_BOUND_TOKEN_REGISTRY_ADDRESS": soul_registry_addr,
        "IS_VERIFIED_ATTRIBUTE_ADDRESS": is_verified_addr,
        "SBT_COLLECTION_ADDRESS": sbt_addr,
        "FACILITATOR_WALLET_ADDRESS": facilitator_acct.address,
        "FACILITATOR_WALLET_PRIVATE_KEY": facilitator_acct.key.hex(),
        "SERVICE_PROVIDER_WALLET_ADDRESS": service_provider.address,
        "SERVICE_PROVIDER_WALLET_PRIVATE_KEY": service_provider.key.hex(),
        "CHAIN_ID": w3.eth.chain_id,
        "FACILITATOR_FEE_BPS": 50,
        "SETTLEMENT_MODE": "atomic",
        "ROUTER_ADDRESS": router_addr,
        "ROUTER_KYA_ADDRESS": router_kya_addr,
        "TREASURY_ADDRESS": deployer.address,
    }.items():
        monkeypatch.setattr(config, name, val)

    teurc = chain.get_contract(w3, "tEURC", teurc_addr)
    soul_registry = chain.get_contract(w3, "MockSoulRegistry", soul_registry_addr)
    router = chain.get_contract(w3, "AgentPayRouter", router_addr)
    router_kya = chain.get_contract(w3, "MockRouterKYA", router_kya_addr)

    # Facilitator у allow-list релеєрів.
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), router.functions.setRelayer(facilitator_acct.address, True)))

    # Покупцю: tEURC + verified Soul (identity) + verified у router-KYA (ончейн-гейт).
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), teurc.functions.mint(buyer.address, 10_000_000)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), soul_registry.functions.registerSoul(buyer.address)))
    soul_id = soul_registry.functions.soulOf(buyer.address).call()
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), soul_registry.functions.setVerified(soul_id, True)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), router_kya.functions.setSoul(buyer.address, 1)))
    _wait(w3, chain.send_contract_tx(w3, deployer.key.hex(), router_kya.functions.setVerified(1, True)))

    from facilitator.store import Store
    from facilitator.whitechain_facilitator import WhitechainFacilitator

    facilitator = WhitechainFacilitator(w3=w3, store=Store(":memory:"))

    yield AtomicFixture(
        w3=w3, teurc=teurc, router=router, facilitator=facilitator,
        deployer=deployer, facilitator_acct=facilitator_acct,
        service_provider=service_provider, buyer=buyer,
    )
    # Скидаємо кеш ланцюга, щоб блоки цього важкого тесту не «текли» timestamp-ом
    # у наступні legacy-тести (спільний eth-tester) — вони стартують зі свіжого.
    chain._local_w3 = None


def _wait(w3, tx_hash):
    return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)


def _pay(fx, *, resource="/photo/kyiv-lavra", price_wei=20_000):
    """Підписує atomic-authorization як клієнт і повертає (authorization, salt, price)."""
    payload = agent_client.build_and_sign_authorization(
        fx.buyer.key.hex(),
        config.ROUTER_ADDRESS,
        price_wei,
        resource,
        config.TEURC_ADDRESS,
        fx.w3.eth.chain_id,
        settlement_mode="atomic",
        seller=config.SERVICE_PROVIDER_WALLET_ADDRESS,
        fee_bps=config.FACILITATOR_FEE_BPS,
    )
    return payload["authorization"], payload["resource_salt"], resource, price_wei


def test_atomic_happy_path_splits_fee_to_treasury_net_to_seller(atomic_setup):
    fx = atomic_setup
    price = 20_000
    fee = price * config.FACILITATOR_FEE_BPS // 10_000  # 100
    net = price - fee  # 19_900

    seller_before = fx.teurc.functions.balanceOf(fx.service_provider.address).call()
    treasury_before = fx.teurc.functions.balanceOf(fx.deployer.address).call()
    buyer_before = fx.teurc.functions.balanceOf(fx.buyer.address).call()

    auth, salt, resource, _ = _pay(fx, price_wei=price)
    result = fx.facilitator.verify_and_settle(auth, resource, salt, price)

    assert result["valid"] is True
    assert result["fee_wei"] == fee
    assert result["net_wei"] == net
    # Комісія -> treasury (owner роутера), НЕ facilitator; нетто -> seller; покупця списано.
    assert fx.teurc.functions.balanceOf(fx.service_provider.address).call() == seller_before + net
    assert fx.teurc.functions.balanceOf(fx.deployer.address).call() == treasury_before + fee
    assert fx.teurc.functions.balanceOf(fx.buyer.address).call() == buyer_before - price
    # Facilitator НЕ тримає коштів у atomic (він лише релеїть).
    assert fx.teurc.functions.balanceOf(fx.facilitator_acct.address).call() == 0


def test_atomic_floor_remainder_goes_to_seller(atomic_setup):
    fx = atomic_setup
    price = 999  # fee = floor(999*50/10000) = floor(4.995) = 4; net = 995
    seller_before = fx.teurc.functions.balanceOf(fx.service_provider.address).call()

    auth, salt, resource, _ = _pay(fx, resource="/photo/kyiv-sofia-cathedral", price_wei=price)
    result = fx.facilitator.verify_and_settle(auth, resource, salt, price)

    assert result["fee_wei"] == 4
    assert result["net_wei"] == 995
    assert result["fee_wei"] + result["net_wei"] == price  # нічого не зникло
    assert fx.teurc.functions.balanceOf(fx.service_provider.address).call() == seller_before + 995


def test_atomic_replay_is_clean_failure_no_funds_held(atomic_setup):
    fx = atomic_setup
    price = 20_000
    auth, salt, resource, _ = _pay(fx, price_wei=price)

    first = fx.facilitator.verify_and_settle(auth, resource, salt, price)
    assert first["valid"] is True

    seller_after_first = fx.teurc.functions.balanceOf(fx.service_provider.address).call()

    # Повтор ТІЄЇ Ж авторизації. Валідатор ловить replay офчейн (authorizationState),
    # тож це чиста відмова — жодних рухів коштів і жодного «утриманого» стану.
    second = fx.facilitator.verify_and_settle(auth, resource, salt, price)
    assert second["valid"] is False
    assert "replay" in second["reason"].lower()

    assert fx.teurc.functions.balanceOf(fx.service_provider.address).call() == seller_after_first
    # Ключова властивість atomic: стану funds-held не існує.
    assert fx.facilitator.list_held_settlements() == []


def test_atomic_backend_never_raises_forward_error(atomic_setup):
    """AtomicSettlementEngine піднімає лише SettlementError-родину (чистий збій),
    ніколи SettlementForwardError — бо форварду як окремого кроку не існує.

    Симулюємо ончейн-revert (той самий nonce уже спожито в мережі), звертаючись
    до backend напряму з тим самим message — має піднятись SettlementError, а не
    SettlementForwardError, і продавцю нічого не додатись."""
    fx = atomic_setup
    price = 20_000
    auth, salt, resource, _ = _pay(fx, price_wei=price)
    validation = fx.facilitator.payment.validate_authorization(auth, resource, salt, price)
    assert validation["ok"] is True

    # Перший settle через backend спожив nonce у мережі.
    fx.facilitator.settlement.settle(validation["message"], auth)
    seller_after = fx.teurc.functions.balanceOf(fx.service_provider.address).call()

    from facilitator.settlement import SettlementForwardError

    with pytest.raises(SettlementError) as exc_info:
        fx.facilitator.settlement.settle(validation["message"], auth)
    assert not isinstance(exc_info.value, SettlementForwardError)
    assert fx.teurc.functions.balanceOf(fx.service_provider.address).call() == seller_after
