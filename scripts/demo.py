"""scripts/demo.py — наскрізний сценарій Фази 1: KYA + reputation + EIP-3009.

Показує саме те, чого немає в x402-на-Base: без верифікованого WB Soul
оплата не проходить незалежно від суми чи підпису, і без потрібного
SBT-tier преміум-ресурс недоступний навіть верифікованому агенту.

Працює і локально (NETWORK=local, за замовчуванням — деплоїть tEURC і,
якщо USE_MOCK_SOUL=true, MockSoulRegistry щоразу заново на EthereumTesterProvider),
і на реальному Whitechain testnet (NETWORK=whitechain_testnet — адреси й
ключі з .env, нічого не деплоїться тут; спочатку `npx hardhat run
deploy/deploy.ts --network whitechain_testnet`, див. DEPLOY_WHITECHAIN.md).

Сценарій:
  1. Роздає tEURC трьом гаманцям-агентам.
  2. Реєструє й верифікує Soul для двох з них; видає SBT-бейдж лише одному.
  3. Агент БЕЗ Soul намагається купити фото -> відмова (KYA).
  4. Агент із Soul (без SBT) купує звичайне фото -> миттєвий доступ,
     видно комісію facilitator-а і reputation_tier=0.
  5. Той самий агент намагається купити ПРЕМІУМ-фото -> відмова
     (insufficient reputation, tier 0 < 1).
  6. Агент із Soul І SBT купує те саме преміум-фото -> успіх (tier 1).
  7. Повторне пред'явлення авторизації з кроку 4 -> відмова (replay).

Запуск: python scripts/demo.py
"""

import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn
from eth_account import Account
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import chain  # noqa: E402
import config  # noqa: E402

console = Console()


def _ok(label: str) -> None:
    console.print(f"[bold green][OK][/bold green] {label}")


def _fail_step(label: str) -> None:
    console.print(f"[bold red][FAIL][/bold red] {label}")
    sys.exit(1)


def setup_local_chain() -> str:
    """NETWORK=local: деплоїть tEURC (+ MockSoulRegistry, якщо USE_MOCK_SOUL)
    свіжо на EthereumTesterProvider і заповнює config.* адресами.

    Повертає приватний ключ деплоєра (== owner контрактів) — виклик має
    перевикористати САМЕ ЦЕЙ ключ для подальших owner-only викликів
    (mint/registerSoul/setVerified/issueSBT), інакше вони впадуть з
    OwnableUnauthorizedAccount.
    """
    w3 = chain.get_w3()
    faucet = w3.eth.accounts[0]

    deployer = Account.create()
    facilitator_acct = Account.create()
    service_provider_acct = Account.create()

    for acct in (deployer, facilitator_acct):
        w3.eth.send_transaction({"from": faucet, "to": acct.address, "value": Web3.to_wei(10, "ether")})

    console.print("Деплою tEURC локально...")
    teurc_addr = chain.deploy_contract(w3, deployer.key.hex(), "tEURC")
    config.TEURC_ADDRESS = teurc_addr

    if config.USE_MOCK_SOUL:
        console.print("USE_MOCK_SOUL=true — деплою MockSoulRegistry локально...")
        is_verified_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulAttribute")
        sbt_addr = chain.deploy_contract(w3, deployer.key.hex(), "MockSoulBoundTokenCollection")
        soul_registry_addr = chain.deploy_contract(
            w3, deployer.key.hex(), "MockSoulRegistry", is_verified_addr, sbt_addr
        )
        config.SOUL_REGISTRY_ADDRESS = soul_registry_addr
        config.SOUL_ATTRIBUTE_REGISTRY_ADDRESS = soul_registry_addr
        config.SOUL_BOUND_TOKEN_REGISTRY_ADDRESS = soul_registry_addr
        config.IS_VERIFIED_ATTRIBUTE_ADDRESS = is_verified_addr
        config.SBT_COLLECTION_ADDRESS = sbt_addr
    else:
        missing = [
            name
            for name in (
                "SOUL_REGISTRY_ADDRESS",
                "SOUL_ATTRIBUTE_REGISTRY_ADDRESS",
                "SOUL_BOUND_TOKEN_REGISTRY_ADDRESS",
                "IS_VERIFIED_ATTRIBUTE_ADDRESS",
            )
            if not getattr(config, name)
        ]
        if missing:
            _fail_step(f"USE_MOCK_SOUL=false, але в .env бракує: {', '.join(missing)}")

    config.FACILITATOR_WALLET_ADDRESS = facilitator_acct.address
    config.FACILITATOR_WALLET_PRIVATE_KEY = facilitator_acct.key.hex()
    config.SERVICE_PROVIDER_WALLET_ADDRESS = service_provider_acct.address
    config.CHAIN_ID = w3.eth.chain_id
    config.SERVICE_PROVIDER_BASE_URL = f"http://{config.SERVICE_PROVIDER_HOST}:{config.SERVICE_PROVIDER_PORT}"

    return deployer.key.hex()


def check_testnet_config() -> None:
    """NETWORK=whitechain_testnet: нічого не деплоїть — усе має вже бути в .env."""
    required = [
        "WHITECHAIN_TESTNET_RPC",
        "TEURC_ADDRESS",
        "FACILITATOR_WALLET_ADDRESS",
        "FACILITATOR_WALLET_PRIVATE_KEY",
        "SERVICE_PROVIDER_WALLET_ADDRESS",
        "SOUL_REGISTRY_ADDRESS",
        "SOUL_ATTRIBUTE_REGISTRY_ADDRESS",
        "SOUL_BOUND_TOKEN_REGISTRY_ADDRESS",
        "IS_VERIFIED_ATTRIBUTE_ADDRESS",
    ]
    missing = [name for name in required if not getattr(config, name)]
    if missing:
        _fail_step(
            f"NETWORK=whitechain_testnet, але в .env бракує: {', '.join(missing)}. "
            "Див. DEPLOY_WHITECHAIN.md."
        )


def start_service_provider(w3) -> uvicorn.Server:
    import service_provider.server as sp_server
    from facilitator.whitechain_facilitator import WhitechainFacilitator

    sp_server.facilitator = WhitechainFacilitator(w3=w3)

    server = uvicorn.Server(
        uvicorn.Config(
            sp_server.app, host=config.SERVICE_PROVIDER_HOST, port=config.SERVICE_PROVIDER_PORT, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            requests.get(f"{config.SERVICE_PROVIDER_BASE_URL}/photos", timeout=1)
            return server
        except requests.exceptions.ConnectionError:
            time.sleep(0.2)
    _fail_step("AI Service Provider не піднявся за 10 секунд.")


def main() -> None:
    console.print(
        Panel.fit(
            "[bold]AgentPay on Whitechain[/bold] — Фаза 1: KYA + reputation + EIP-3009",
            border_style="cyan",
        )
    )

    if config.NETWORK == "whitechain_testnet":
        console.print(f"Мережа: [bold]{config.NETWORK}[/bold] ({config.WHITECHAIN_TESTNET_RPC})")
        check_testnet_config()
        w3 = chain.get_w3()
        if not config.DEPLOYER_PRIVATE_KEY:
            _fail_step("NETWORK=whitechain_testnet, але DEPLOYER_PRIVATE_KEY не заданий у .env.")
        deployer_key = config.DEPLOYER_PRIVATE_KEY
    else:
        console.print(f"Мережа: [bold]{config.NETWORK}[/bold] (локальний EthereumTesterProvider)")
        w3 = chain.get_w3()
        deployer_key = setup_local_chain()

    teurc = chain.get_contract(w3, "tEURC", config.TEURC_ADDRESS)
    soul_registry = chain.get_contract(w3, "MockSoulRegistry", config.SOUL_REGISTRY_ADDRESS) if config.USE_MOCK_SOUL else None

    console.print("\n[bold]Крок 1 — гаманці трьох агентів[/bold]")
    agent_no_soul = Account.create()
    agent_with_soul = Account.create()
    agent_with_soul_and_sbt = Account.create()
    for agent, label in (
        (agent_no_soul, "без Soul"),
        (agent_with_soul, "Soul, без SBT"),
        (agent_with_soul_and_sbt, "Soul + SBT"),
    ):
        console.print(f"  {label}: {agent.address}")

    console.print("\n[bold]Крок 2 — роздача tEURC[/bold]")
    for agent in (agent_no_soul, agent_with_soul, agent_with_soul_and_sbt):
        tx = chain.send_contract_tx(w3, deployer_key, teurc.functions.mint(agent.address, 10_000_000))
        w3.eth.wait_for_transaction_receipt(tx)
    console.print("  Кожен агент отримав 10.0 tEURC.")

    if config.USE_MOCK_SOUL:
        console.print("\n[bold]Крок 3 — верифікація WB Soul (Mock) + видача SBT[/bold]")
        for agent in (agent_with_soul, agent_with_soul_and_sbt):
            tx = chain.send_contract_tx(w3, deployer_key, soul_registry.functions.registerSoul(agent.address))
            w3.eth.wait_for_transaction_receipt(tx)
            soul_id = soul_registry.functions.soulOf(agent.address).call()
            tx = chain.send_contract_tx(w3, deployer_key, soul_registry.functions.setVerified(soul_id, True))
            w3.eth.wait_for_transaction_receipt(tx)
            console.print(f"  {agent.address[:10]}... -> soul_id={soul_id}, verified=True")

        sbt_soul_id = soul_registry.functions.soulOf(agent_with_soul_and_sbt.address).call()
        tx = chain.send_contract_tx(w3, deployer_key, soul_registry.functions.issueSBT(sbt_soul_id, 1))
        w3.eth.wait_for_transaction_receipt(tx)
        console.print(f"  SBT-бейдж видано soul_id={sbt_soul_id} (reputation_tier -> 1)")
        console.print(f"  {agent_no_soul.address[:10]}... — Soul НЕ реєструється (навмисно).")
    else:
        console.print(
            "\n[yellow]USE_MOCK_SOUL=false — цей демо-скрипт очікує, що агенти вже "
            "верифіковані реальним WB Soul на мережі; кроки реєстрації/SBT пропущено.[/yellow]"
        )

    console.print("\n[bold]Крок 4 — піднімаю AI Service Provider[/bold]")
    server = start_service_provider(w3)
    console.print(f"  Слухає на {config.SERVICE_PROVIDER_BASE_URL}")

    console.print("\n[bold]Крок 5 — агент БЕЗ Soul намагається купити фото[/bold]")
    try:
        agent_client.pay_and_fetch(
            f"{config.SERVICE_PROVIDER_BASE_URL}/photo/kyiv-lavra", private_key=agent_no_soul.key.hex()
        )
        _fail_step("агент без Soul НЕ мав отримати фото")
    except agent_client.PaymentFailed as exc:
        if "KYA" in str(exc) or "Soul" in str(exc):
            _ok("агент без Soul — платіж відхилено з причиною KYA")
        else:
            _fail_step(f"відхилено з неочікуваної причини: {exc}")

    console.print("\n[bold]Крок 6 — агент із Soul (без SBT) купує звичайне фото[/bold]")
    result = agent_client.pay_and_fetch(
        f"{config.SERVICE_PROVIDER_BASE_URL}/photo/kyiv-lavra", private_key=agent_with_soul.key.hex()
    )
    _ok("агент із Soul — офчейн-підпис прийнято без очікування майнінгу")
    _ok(
        f"контент видано ({len(result.content)} байт), комісія {result.fee_teurc} tEURC "
        f"списана на facilitator, reputation_tier={result.reputation_tier}"
    )
    replay_payload_url = f"{config.SERVICE_PROVIDER_BASE_URL}/photo/kyiv-lavra"

    console.print("\n[bold]Крок 7 — той самий агент намагається купити ПРЕМІУМ-фото[/bold]")
    try:
        agent_client.pay_and_fetch(
            f"{config.SERVICE_PROVIDER_BASE_URL}/photo/kyiv-motherland-monument",
            private_key=agent_with_soul.key.hex(),
        )
        _fail_step("агент без потрібного SBT-tier НЕ мав отримати преміум-фото")
    except agent_client.PaymentFailed as exc:
        if "reputation" in str(exc).lower() or "репутац" in str(exc).lower():
            _ok("агент без потрібного SBT-tier — преміум-ресурс відхилено (insufficient reputation)")
        else:
            _fail_step(f"відхилено з неочікуваної причини: {exc}")

    console.print("\n[bold]Крок 8 — агент із Soul І SBT купує те саме преміум-фото[/bold]")
    premium_result = agent_client.pay_and_fetch(
        f"{config.SERVICE_PROVIDER_BASE_URL}/photo/kyiv-motherland-monument",
        private_key=agent_with_soul_and_sbt.key.hex(),
    )
    _ok(f"преміум-ресурс видано агенту з SBT (reputation_tier={premium_result.reputation_tier})")

    console.print("\n[bold]Крок 9 — повторне пред'явлення тієї ж авторизації (replay)[/bold]")
    # Підписуємо нову авторизацію й одразу пробуємо пред'явити її двічі поспіль.
    payload = agent_client.build_and_sign_authorization(
        agent_with_soul.key.hex(),
        config.FACILITATOR_WALLET_ADDRESS,
        config.RESOURCE_PRICE_TEURC,
        "/photo/kyiv-sofia-cathedral",
        config.TEURC_ADDRESS,
        w3.eth.chain_id,
    )
    first = requests.post(replay_payload_url.replace("kyiv-lavra", "kyiv-sofia-cathedral"), json=payload, timeout=10)
    second = requests.post(replay_payload_url.replace("kyiv-lavra", "kyiv-sofia-cathedral"), json=payload, timeout=10)
    if first.status_code == 200 and second.status_code == 402 and "replay" in second.json().get("error", "").lower():
        _ok("повторний підпис (nonce replay) відхилено")
    else:
        _fail_step(f"replay-захист не спрацював: перший={first.status_code}, другий={second.status_code}")

    console.print("\n[bold]Підсумок[/bold]")
    balance = requests.get(f"{config.SERVICE_PROVIDER_BASE_URL}/balance", timeout=10).json()
    table = Table()
    table.add_column("Метрика")
    table.add_column("Значення")
    table.add_row("Транзакцій", str(balance["sales_count"]))
    table.add_row("Заробіток AI Service Provider (нетто)", f"{balance['earned_teurc']} tEURC")
    table.add_row("Комісія facilitator-а", f"{config.FACILITATOR_FEE_BPS / 100}%")
    console.print(table)

    server.should_exit = True
    time.sleep(0.3)


if __name__ == "__main__":
    main()
