"""Локальний інтеграційний тест усього пайплайна — БЕЗ реального Whitechain
RPC і БЕЗ реального ANTHROPIC_API_KEY.

Навіщо: п'ять частин ТЗ (гаманці, facilitator, Фотобанк, Автор, run_demo)
залежать одна від одної, і кожна наступна спирається на попередню. Перш
ніж витрачати testnet WBT і виклики Claude API, цей скрипт перевіряє, що
вся логіка коректна:
- локальний EVM-ланцюжок (web3 EthereumTesterProvider) замість Whitechain RPC
- підроблений (fake) Anthropic-клієнт, що імітує рішення Claude купити 3 фото,
  замість реального виклику API

Якщо цей тест проходить — фундамент робочий, лишається тільки підключити
справжній RPC, справжні ключі й faucet (див. README).

Запуск: python tests/local_integration_test.py
"""

import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from web3 import EthereumTesterProvider, Web3  # noqa: E402

import config  # noqa: E402


class FakeBlock:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    """Імітує anthropic.messages.create: спершу купує 3 фото по черзі, потім завершує."""

    def __init__(self, photo_names):
        self.calls = 0
        self.photo_names = photo_names

    def create(self, model, max_tokens, system, tools, messages):
        self.calls += 1
        if self.calls <= len(self.photo_names):
            name = self.photo_names[self.calls - 1]
            return FakeResponse(
                [FakeBlock("tool_use", id=f"t{self.calls}", name="buy_photo", input={"name": name})],
                "tool_use",
            )
        return FakeResponse(
            [FakeBlock("text", text="Готова стаття про Київ з фотографіями (тестовий прогін).")],
            "end_turn",
        )


class FakeAnthropicClient:
    def __init__(self, photo_names):
        self.messages = FakeMessages(photo_names)


def main() -> None:
    tmp_dir = Path(".local_integration_test_tmp")
    tmp_dir.mkdir(exist_ok=True)
    ledger_path = tmp_dir / "spend_ledger.json"
    used_tx_path = tmp_dir / "facilitator_used_tx.json"
    downloaded_dir = Path("downloaded_photos")

    try:
        # 1. Локальний EVM-ланцюжок замість Whitechain RPC
        w3 = Web3(EthereumTesterProvider())
        faucet = w3.eth.accounts[0]

        import wallets.setup_wallets as sw

        author_addr, author_key = sw.generate_wallet()
        photobank_addr, _ = sw.generate_wallet()
        w3.eth.send_transaction({"from": faucet, "to": author_addr, "value": Web3.to_wei(5, "ether")})

        config.WHITECHAIN_CHAIN_ID = w3.eth.chain_id
        config.AUTHOR_WALLET_ADDRESS = author_addr
        config.AUTHOR_WALLET_PRIVATE_KEY = author_key
        config.PHOTOBANK_WALLET_ADDRESS = photobank_addr
        config.PHOTOBANK_PORT = 8813
        config.PHOTOBANK_BASE_URL = "http://127.0.0.1:8813"
        config.PHOTO_PRICE_WBT = 0.02
        config.AUTHOR_MAX_SPEND_WBT = 1.0
        config.SPEND_LEDGER_PATH = str(ledger_path)

        # x402_client.send_wbt має підписувати транзакції на наш тестовий ланцюжок,
        # а не на реальний RPC (якого тут немає) — підміняємо w3 за замовчуванням.
        import author.x402_client as x402_client

        _real_send_wbt = sw.send_wbt
        x402_client.send_wbt = lambda pk, to, amt, w3=None, _test_w3=w3: _real_send_wbt(
            pk, to, amt, w3=w3 or _test_w3
        )

        # 2. Піднімаємо справжній Фотобанк-сервер (FastAPI/uvicorn), тільки локально
        import photobank.server as photobank_module

        photobank_module.facilitator = photobank_module.WhitechainFacilitator(
            w3=w3, used_tx_store=str(used_tx_path)
        )
        server = uvicorn.Server(
            uvicorn.Config(photobank_module.app, host="127.0.0.1", port=8813, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        time.sleep(1.0)

        # 3. Агент-Автор із підробленим Claude-клієнтом (без реального API-ключа)
        from author.agent import AuthorAgent

        photo_names = ["kyiv-sofia-cathedral", "kyiv-lavra", "kyiv-independence-square"]
        agent = AuthorAgent(api_key="fake-key-for-local-test", on_event=lambda e: print("  ", e))
        agent.client = FakeAnthropicClient(photo_names)

        author_before = sw.check_balance(author_addr, w3)
        photobank_before = sw.check_balance(photobank_addr, w3)

        print("=== Локальний інтеграційний тест: run_demo.py без реального RPC/API ===\n")
        outcome = agent.run("Напиши статтю про Київ")

        author_after = sw.check_balance(author_addr, w3)
        photobank_after = sw.check_balance(photobank_addr, w3)

        print(f"\nСтаття: {outcome['article']}")
        print(f"Куплено фото: {len(outcome['purchases'])}")
        print(f"Витрачено: {outcome['spent_wbt']} WBT")
        print(f"Баланс Автора: {author_before} -> {author_after} WBT")
        print(f"Баланс Фотобанку: {photobank_before} -> {photobank_after} WBT")

        assert len(outcome["purchases"]) == 3, "мало бути 3 покупки"
        assert outcome["spent_wbt"] == 0.06, "мало бути витрачено 0.06 WBT"
        assert round(photobank_after - photobank_before, 6) == 0.06, "Фотобанк мав заробити 0.06 WBT"
        # Автор віддав щонайменше 0.06 WBT за фото — плюс трохи gas зверху.
        assert author_before - author_after >= 0.06 - 1e-9

        for name in photo_names:
            assert (downloaded_dir / f"{name}.png").exists(), f"фото {name} не збереглось локально"

        server.should_exit = True
        time.sleep(0.3)

        print("\n✅ Усі перевірки пройшли: гаманці, facilitator, Фотобанк і Агент-Автор працюють разом.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(downloaded_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
