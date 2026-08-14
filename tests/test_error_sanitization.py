"""F-01 регресія: текст винятку розрахунку НЕ витікає клієнту.

Раніше гілка `except SettlementError` повертала клієнту `f"Розрахунок не вдався:
{exc}"`, тобто внутрішній RPC/revert-текст ішов у 402-відповідь. Після фіксу
клієнт бачить лише узагальнену причину, а повний exc — тільки в лог.

Цей тест мокає settlement.settle так, щоб він кинув SettlementError із
«секретним» текстом, і перевіряє, що цього тексту немає в reason назовні.
Легітимні валідаційні reason-и (payment.py) навмисно НЕ чіпалися — перевіряємо,
що вони й далі йдуть як є.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import agent_client  # noqa: E402
import chain  # noqa: E402
from facilitator.settlement import SettlementError  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_shared_chain():
    # facilitator_setup деплоїть контракти на СПІЛЬНИЙ кешований eth-tester;
    # блоки цих тестів рухають block.timestamp і можуть випхати наступні
    # legacy-тести за їхнє 300с вікно. Скидаємо кеш після — наступний бере свіжий.
    yield
    chain._local_w3 = None

SECRET = "SECRET_RPC_LEAK_http://internal-node:8545_revert_xyz"


def _sign(fx, agent, resource, value_wei):
    return agent_client.build_and_sign_authorization(
        agent.key.hex(), fx.facilitator_acct.address, value_wei, resource, fx.teurc.address, fx.w3.eth.chain_id
    )


def test_settlement_exception_text_not_surfaced_to_client(facilitator_setup, monkeypatch):
    fx = facilitator_setup
    # Валідна авторизація верифікованого агента — доходить до кроку settlement.
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei)

    def _boom(*_args, **_kwargs):
        raise SettlementError(SECRET)

    monkeypatch.setattr(fx.facilitator.settlement, "settle", _boom)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )

    assert result["valid"] is False
    # Ключова властивість: сирий текст винятку НЕ в reason, що йде клієнту.
    assert SECRET not in result["reason"]
    assert "internal-node" not in result["reason"]
    # Узагальнена причина присутня (клієнт розуміє, що це збій розрахунку).
    assert "розрахунк" in result["reason"].lower()


def test_legitimate_validation_reasons_are_unchanged(facilitator_setup):
    """Санітизація стосується ЛИШЕ шляху exception→reason. Валідаційні причини
    payment.py (не містять секретів) лишаються інформативними — напр. overpayment."""
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra", value_wei=fx.price_wei + 10_000)
    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is False
    assert "переплат" in result["reason"].lower()  # інформативний reason не «з'їдено»
