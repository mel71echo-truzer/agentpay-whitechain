"""Оркестрація і "вітрина" — Частина 5 з ТЗ.

Один скрипт, що піднімає Фотобанк, запускає Агента-Автора із завданням і
красиво показує в терміналі кожен крок: хто кому заплатив, скільки, де
подивитись транзакцію в Whitechain explorer.

Запуск:
    python run_demo.py "Напиши статтю про Київ"

Перед запуском потрібен заповнений .env (гаманці — wallets/setup_wallets.py,
ANTHROPIC_API_KEY, WHITECHAIN_RPC_URL — див. README).
"""

import sys
import threading
import time

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from author.agent import AuthorAgent
from photobank.server import app as photobank_app
from wallets.setup_wallets import check_balance

console = Console()


def _check_config() -> list[str]:
    problems = []
    if not config.WHITECHAIN_RPC_URL:
        problems.append("WHITECHAIN_RPC_URL не заданий у .env")
    if not config.AUTHOR_WALLET_PRIVATE_KEY or not config.AUTHOR_WALLET_ADDRESS:
        problems.append("Гаманець Автора не заданий у .env — спочатку запусти wallets/setup_wallets.py")
    if not config.PHOTOBANK_WALLET_ADDRESS:
        problems.append("Гаманець Фотобанку не заданий у .env — спочатку запусти wallets/setup_wallets.py")
    if not config.ANTHROPIC_API_KEY:
        problems.append("ANTHROPIC_API_KEY не заданий у .env")
    return problems


def start_photobank_server():
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            photobank_app,
            host=config.PHOTOBANK_HOST,
            port=config.PHOTOBANK_PORT,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            requests.get(f"{config.PHOTOBANK_BASE_URL}/photos", timeout=1)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.2)
    else:
        raise RuntimeError("Фотобанк не піднявся за 10 секунд.")
    return server


def on_agent_event(event: dict) -> None:
    etype = event["type"]
    if etype == "requesting":
        console.print(f"🔍 Автор просить фото у Фотобанку: [bold]{event['photo']}[/bold]")
        console.print(f"💰 Фотобанк: потрібна оплата {config.PHOTO_PRICE_WBT} WBT (~$0.02)")
    elif etype == "paid":
        link = event["explorer_link"]
        console.print(f"✅ Автор заплатив {event['amount_wbt']} WBT. TX: [link={link}]{link}[/link]")
    elif etype == "delivered":
        console.print(f"🖼️  Фото отримано і збережено: {event['path']}\n")
    elif etype == "limit_exceeded":
        console.print(f"[red]⛔ Ліміт витрат вичерпано: {event['error']}[/red]")
    elif etype == "payment_failed":
        console.print(f"[red]❌ Оплата не пройшла: {event['error']}[/red]")


def main() -> None:
    task = sys.argv[1] if len(sys.argv) > 1 else "Напиши статтю про Київ"

    console.print(
        Panel.fit(
            "[bold]AgentPay on Whitechain[/bold] — демо: один AI-агент платить іншому",
            border_style="cyan",
        )
    )

    problems = _check_config()
    if problems:
        console.print("[red]Демо не може стартувати, бракує налаштувань у .env:[/red]")
        for p in problems:
            console.print(f"  • {p}")
        sys.exit(1)

    console.print(f"🤖 Агент-Автор отримав завдання: [italic]{task}[/italic]\n")

    console.print("Стартові баланси:")
    author_before = check_balance(config.AUTHOR_WALLET_ADDRESS)
    photobank_before = check_balance(config.PHOTOBANK_WALLET_ADDRESS)
    console.print(f"  Автор:     {author_before} WBT")
    console.print(f"  Фотобанк:  {photobank_before} WBT\n")

    console.print("Піднімаю сервер Фотобанку...")
    server = start_photobank_server()
    console.print(f"Фотобанк слухає на {config.PHOTOBANK_BASE_URL}\n")

    agent = AuthorAgent(on_event=on_agent_event)
    outcome = agent.run(task)

    console.print(Panel.fit(outcome["article"], title="📰 Стаття", border_style="green"))

    author_after = check_balance(config.AUTHOR_WALLET_ADDRESS)
    photobank_after = check_balance(config.PHOTOBANK_WALLET_ADDRESS)

    table = Table(title="📊 Підсумок")
    table.add_column("Метрика")
    table.add_column("Значення")
    table.add_row("Транзакцій", str(len(outcome["purchases"])))
    table.add_row("Витрачено", f"{outcome['spent_wbt']} WBT")
    table.add_row("Баланс Автора", f"{author_before} → {author_after} WBT")
    table.add_row("Баланс Фотобанку", f"{photobank_before} → {photobank_after} WBT")
    console.print(table)

    console.print("\n[bold cyan]Жодна людина не натиснула кнопку «оплатити».[/bold cyan]")

    server.should_exit = True


if __name__ == "__main__":
    main()
