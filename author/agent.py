"""Агент-Автор (покупець) — тут Claude через tool use САМ вирішує, коли платити.

Як це працює: ми даємо Claude інструмент "buy_photo". Claude бачить
завдання ("напиши статтю про Київ"), список доступних фото в AI Service
Provider-і і сам вирішує, які саме купити — модель не знає заздалегідь, що
"треба заплатити": вона просто викликає інструмент buy_photo, а вже
всередині (agent_client.pay_and_fetch) відбувається: запит -> 402 Payment
Required -> офчейн-підпис EIP-3009 authorization -> facilitator валідує
KYA/reputation/підпис і релеїть оплату в tEURC -> фото. Claude про сам факт
оплати навіть не думає — деталі x402/EIP-3009/KYA сховані за інструментом.
"""

import json
import os
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from agent_client import PaymentFailed, SpendLedger, SpendLimitExceeded, pay_and_fetch  # noqa: E402

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

BUY_PHOTO_TOOL = {
    "name": "buy_photo",
    "description": (
        "Купує одне фото в AI Service Provider-і за назвою. Автоматично підписує "
        "офчейн-авторизацію оплати в tEURC (KYA/reputation-перевірку і релей у "
        "мережу робить facilitator). Повертає підтвердження покупки: чи вдалось, "
        "скільки заплачено, reputation_tier, причину відмови (якщо не KYA-верифікований "
        "або немає потрібної репутації для преміум-ресурсу)."
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
        # on_event(dict) — необов'язковий callback для гарного виводу в scripts/demo.py
        self.on_event = on_event or (lambda event: None)

    def _emit(self, **event) -> None:
        self.on_event(event)

    def _buy_photo_tool(self, name: str) -> dict:
        url = f"{config.SERVICE_PROVIDER_BASE_URL}/photo/{name}"
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
                reputation_tier=result.reputation_tier,
                fee_teurc=result.fee_teurc,
                relay_tx_hash=result.relay_tx_hash,
            )
            out_dir = Path("downloaded_photos")
            out_dir.mkdir(exist_ok=True)
            out_path = out_dir / f"{name}.png"
            out_path.write_bytes(result.content)
            self.purchases.append(
                {
                    "photo": name,
                    "reputation_tier": result.reputation_tier,
                    "fee_teurc": result.fee_teurc,
                    "relay_tx_hash": result.relay_tx_hash,
                    "saved_to": str(out_path),
                }
            )
            self._emit(type="delivered", photo=name, path=str(out_path))

        return {
            "success": True,
            "reputation_tier": result.reputation_tier,
            "relay_tx_hash": result.relay_tx_hash,
        }

    def run(self, task: str) -> dict:
        """Виконує завдання: пише статтю, купуючи потрібні фото через buy_photo."""
        import requests

        photos_catalog = requests.get(f"{config.SERVICE_PROVIDER_BASE_URL}/photos", timeout=10).json()

        system_prompt = (
            "Ти — Агент-Автор. Тобі дають завдання написати коротку статтю. "
            "Для ілюстрації статті тобі потрібні фотографії з AI Service Provider-а. "
            "У тебе є інструмент buy_photo(name) — він автоматично підписує оплату в tEURC "
            "(деталі KYA/reputation/EIP-3009 сховані за інструментом, тобі не треба про них думати). "
            f"Доступні фото за ціною {photos_catalog['price_teurc']} tEURC кожне: "
            f"{', '.join(photos_catalog['photos'])}. Один з ресурсів — преміум "
            f"({', '.join(photos_catalog['premium_resources'])}) і може бути недоступний, "
            "якщо в тебе немає потрібної репутації — це нормально, спробуй інше фото. "
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
                return {"article": article, "purchases": self.purchases, "spent_teurc": self.ledger.spent_teurc}

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
    print(f"\nВитрачено: {outcome['spent_teurc']} tEURC, куплено фото: {len(outcome['purchases'])}")
