"""Тести event-моделі + race-fix (Фаза 2, Компонент 4).

Ключовий критерій приймання: ресурс видається (valid=True + подія
AccessGranted) лише ПІСЛЯ SettlementConfirmed, коли WAIT_FOR_CONFIRMATION=true.
Якщо розрахунок не підтверджується — доступу немає.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
from facilitator import events as ev  # noqa: E402
from facilitator.settlement import SettlementError  # noqa: E402


def _sign(fx, agent, resource, value_teurc=None):
    value_teurc = fx.price_teurc if value_teurc is None else value_teurc
    return agent_client.build_and_sign_authorization(
        agent.key.hex(), fx.facilitator_acct.address, value_teurc, resource, fx.teurc.address, fx.w3.eth.chain_id
    )


def _event_types(result):
    return [e["event_type"] for e in result["events"]]


def test_access_granted_after_settlement_confirmed(facilitator_setup):
    fx = facilitator_setup
    assert fx.facilitator.settlement.wait_for_confirmation is True  # default
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_teurc
    )

    assert result["valid"] is True
    assert result["settlement_status"] == "confirmed"
    types = _event_types(result)
    assert ev.SETTLEMENT_CONFIRMED in types
    assert ev.ACCESS_GRANTED in types
    # AccessGranted СТРОГО після SettlementConfirmed.
    assert types.index(ev.ACCESS_GRANTED) > types.index(ev.SETTLEMENT_CONFIRMED)


def test_resource_not_granted_if_settlement_never_confirms(facilitator_setup, monkeypatch):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    # Імітуємо розрахунок, що НЕ підтверджується (релей відкотився / не
    # замайнився): при WAIT_FOR_CONFIRMATION=true settle() кидає SettlementError.
    def failing_settle(_message, _auth):
        raise SettlementError("relay reverted / not confirmed")

    monkeypatch.setattr(fx.facilitator.settlement, "settle", failing_settle)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_teurc
    )

    assert result["valid"] is False  # доступу немає
    types = _event_types(result)
    assert ev.ACCESS_GRANTED not in types  # ресурс НЕ виданий
    assert ev.SETTLEMENT_CONFIRMED not in types


def test_fast_path_no_confirmation_wait(facilitator_setup):
    fx = facilitator_setup
    # WAIT_FOR_CONFIRMATION=false: швидкий шлях, статус submitted, без події Confirmed.
    fx.facilitator.settlement.wait_for_confirmation = False
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_teurc
    )

    assert result["valid"] is True
    assert result["settlement_status"] == "submitted"
    types = _event_types(result)
    assert ev.SETTLEMENT_CONFIRMED not in types
    assert ev.ACCESS_GRANTED in types  # у швидкому режимі доступ на broadcast


def test_events_persisted_to_store(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")
    fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_teurc
    )
    stored = fx.facilitator.store.list_events(agent=fx.verified_no_sbt_agent.address)
    stored_types = {e["event_type"] for e in stored}
    assert ev.PAYMENT_REQUESTED in stored_types
    assert ev.ACCESS_GRANTED in stored_types
