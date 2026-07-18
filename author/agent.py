"""Агент-Автор (покупець) — Частина 4 з ТЗ. Найцікавіша частина: тут Claude
через tool use САМ вирішує, коли платити.

Як це працює: ми даємо Claude інструмент "buy_photo". Claude бачить
завдання ("напиши статтю про Київ"), список доступних фото у Фотобанку і
сам вирішує, які саме купити і скільки — модель не знає заздалегідь, що
"треба заплатити": вона просто викликає інструмент buy_photo, а вже
всередині цього інструменту (x402_client.pay_and_fetch) відбувається:
запит -> 402 Payment Required -> автономна оплата WBT на Whitechain ->
повторний запит -> фото. Claude про сам факт оплати навіть не думає —
для нього це просто "купівля фото", деталі x402/блокчейну сховані за
інструментом. Це і є суть демо: агент платить агенту без участі людини.
"""

import json
import os
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from author.x402_client import PaymentFailed, SpendLedger, SpendLimitExceeded, pay_and_fetch  # noqa: E402

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

BUY_PHOTO_TOOL = {
    "name": "buy_photo",
    "description": (
        "Купує одне фото у Фотобанку за адресою PHOTOBANK_BASE_URL/photo/{name}. "
        "Автоматично оплачує WBT на Whitechain, якщо сервер вимагає оплату (402). "
        "Повертає підтвердження покупки: чи вдалось, скільки заплачено, хеш транзакції."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Ідентифікатор фото зі списку доступних (поле 'photos' з /photos).",
            }
        },
        "required": ["name"],
    },
}


class AuthorAgent:
    """Claude-агент з tool use, що автономно купує фото для статті."""

    def __init__(self, api_key: str | None = None, on_event=None):
        self.client = anthropic.Anthropic(api_key=api_key or config.ANTHROPIC_API_KEY)
        self.ledger = SpendLedger()
        self.ledger.reset()
        self.purchases: list[dict] = []
        # on_event(dict) — необов'язковий callback для гарного виводу в run_demo.py
        self.on_event = on_event or (lambda event: None)

    def _emit(self, **event) -> None:
        self.on_event(event)

    def _buy_photo_tool(self, name: str) -> dict:
        url = f"{config.PHOTOBANK_BASE_URL}/photo/{name}"
        self._emit(type="requesting", photo=name)
        try:
            result = pay_and_fetch(url, ledger=self.ledger)
        except SpendLimitExceeded as exc:
            self._emit(type="limit_exceeded", photo=name, error=str(exc))
            return {"success": False, "error": str(exc)}
        except PaymentFailed as exc:
            self._emit(type="payment_failed", photo=name, error=str(exc))
            return {"success": False, "error": str(exc)}

        if not result.already_had_it:
            self._emit(
                type="paid",
                photo=name,
                amount_wbt=result.amount_wbt,
                tx_hash=result.tx_hash,
                explorer_link=result.explorer_link,
            )
            out_dir = Path("downloaded_photos")
            out_dir.mkdir(exist_ok=True)
            out_path = out_dir / f"{name}.png"
            out_path.write_bytes(result.content)
            self.purchases.append(
                {
                    "photo": name,
                    "amount_wbt": result.amount_wbt,
                    "tx_hash": result.tx_hash,
                    "explorer_link": result.explorer_link,
                    "saved_to": str(out_path),
                }
            )
            self._emit(type="delivered", photo=name, path=str(out_path))

        return {
            "success": True,
            "amount_wbt": result.amount_wbt,
            "tx_hash": result.tx_hash,
        }

    def run(self, task: str) -> dict:
        """Виконує завдання: пише статтю, купуючи потрібні фото через buy_photo."""
        import requests

        photos_catalog = requests.get(f"{config.PHOTOBANK_BASE_URL}/photos", timeout=10).json()

        system_prompt = (
            "Ти — Агент-Автор. Тобі дають завдання написати коротку статтю. "
            "Для ілюстрації статті тобі потрібні фотографії з Фотобанку. "
            "У тебе є інструмент buy_photo(name) — він автоматично купує фото "
            "(оплата в WBT на Whitechain відбувається сама, тобі не треба про неї думати). "
            f"Доступні фото за ціною {photos_catalog['price_wbt']} WBT кожне: "
            f"{', '.join(photos_catalog['photos'])}. "
            "Обери 2-3 найбільш доречні фото для теми статті і купи їх по черзі "
            "через buy_photo. Коли всі потрібні фото куплено, напиши коротку статтю "
            "(3-5 речень) українською, яка згадує ці фото."
        )

        messages = [{"role": "user", "content": task}]

        while True:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=system_prompt,
                tools=[BUY_PHOTO_TOOL],
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                article = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                return {"article": article, "purchases": self.purchases, "spent_wbt": self.ledger.spent_wbt}

            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "buy_photo":
                    result = self._buy_photo_tool(**block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    task_text = sys.argv[1] if len(sys.argv) > 1 else "Напиши статтю про Київ"
    agent = AuthorAgent(on_event=lambda e: print(e))
    outcome = agent.run(task_text)
    print("\n=== Стаття ===")
    print(outcome["article"])
    print(f"\nВитрачено: {outcome['spent_wbt']} WBT, куплено фото: {len(outcome['purchases'])}")
