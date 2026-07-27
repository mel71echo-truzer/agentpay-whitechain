"""Фаза 2, M2 (settlement.py) — MAJOR: частковий збій сеттлменту (F3).

Сценарій: релей `transferWithAuthorization` пройшов (кошти вже у facilitator),
а форвард нетто сервісу відкотився. Раніше обидва збої зливались у один
`SettlementError` → мовчазний `_deny` без журналу і без сліду для звірки.

Рішення автора №3 (БЕЗ авто-ретраю):
  - журнал ПЕРЕД бродкастом (SettlementInitiated);
  - при реверті форварду — явний стан «кошти утримані, зобов'язання не
    виконане» (SettlementFundsHeld) з tx_hash релею для звірки;
  - доступ до ресурсу НЕ видається;
  - утримані розрахунки можна перелічити (reconciliation).

Два рівні тестів:
  A. Двигун (SettlementEngine) на фейках w3/teurc — детерміновано перевіряє
     самі гілки settlement.py:92 (relay revert) і :112 (forward revert).
  B. Оркестратор (facilitator) — журнал + FundsHeld + відсутність доступу +
     можливість звірки.

Детерміновано: без мережі, без sleep, без часу.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import chain  # noqa: E402
from facilitator import events as ev  # noqa: E402
from facilitator.settlement import (  # noqa: E402
    SettlementEngine,
    SettlementError,
    SettlementForwardError,
    SettlementRelayError,
)

DEAD = "0x000000000000000000000000000000000000dEaD"


# ---------- Фейки для рівня двигуна ----------

class _FakeFunctions:
    def transferWithAuthorization(self, *a):
        return "relay_fn"

    def transfer(self, *a):
        return "forward_fn"


class _FakeTeurc:
    def __init__(self):
        self.functions = _FakeFunctions()


class _FakeEth:
    def __init__(self, statuses):
        self.statuses = statuses

    def wait_for_transaction_receipt(self, tx_hash, timeout=None):
        return SimpleNamespace(status=self.statuses[tx_hash])


class _FakeW3:
    def __init__(self, statuses):
        self.eth = _FakeEth(statuses)


def _engine(statuses):
    return SettlementEngine(
        _FakeW3(statuses),
        _FakeTeurc(),
        facilitator_private_key="0x" + "11" * 32,
        service_provider_address=DEAD,
        fee_bps=50,  # 0.5%
        teurc_decimals=6,
        wait_for_confirmation=True,
    )


_MESSAGE = {
    "from": DEAD,
    "to": DEAD,
    "value": 1_000_000,  # 1.0 tEURC
    "validAfter": 0,
    "validBefore": 2**63,
    "nonce": b"\x00" * 32,
}
_AUTH = {"v": 27, "r": b"\x00" * 32, "s": b"\x00" * 32}


@pytest.fixture
def patched_send(monkeypatch):
    def fake_send(w3, key, fn, *, value_wei=0):
        return "0xRELAY" if fn == "relay_fn" else "0xFORWARD"

    monkeypatch.setattr(chain, "send_contract_tx", fake_send)


def test_engine_forward_revert_raises_forward_error(patched_send):
    # relay ok (1), forward revert (0) -> покриває settlement.py:112.
    eng = _engine({"0xRELAY": 1, "0xFORWARD": 0})
    with pytest.raises(SettlementForwardError) as exc:
        eng.settle(_MESSAGE, _AUTH)
    assert exc.value.relay_tx_hash == "0xRELAY"
    # net = value - floor(value*50/10000) = 1_000_000 - 5_000
    assert exc.value.net_wei == 995_000
    assert isinstance(exc.value, SettlementError)  # лишається підтипом базового


def test_engine_relay_revert_raises_relay_error(patched_send):
    # relay revert (0) -> покриває settlement.py:92; форвард не пробується.
    eng = _engine({"0xRELAY": 0})
    with pytest.raises(SettlementRelayError):
        eng.settle(_MESSAGE, _AUTH)


def test_engine_happy_path_confirmed(patched_send):
    eng = _engine({"0xRELAY": 1, "0xFORWARD": 1})
    result = eng.settle(_MESSAGE, _AUTH)
    assert result["status"] == "confirmed"
    assert result["fee_wei"] == 5_000
    assert result["net_wei"] == 995_000


# ---------- Рівень оркестратора ----------

def _sign(fx, agent, resource):
    return agent_client.build_and_sign_authorization(
        agent.key.hex(), fx.facilitator_acct.address, fx.price_teurc, resource, fx.teurc.address, fx.w3.eth.chain_id
    )


def test_partial_failure_holds_funds_and_denies_access(facilitator_setup, monkeypatch):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    def forward_failed(_message, _auth):
        raise SettlementForwardError(
            "форвард відкотився", relay_tx_hash="0xHELDRELAY", net_wei=995_000
        )

    monkeypatch.setattr(fx.facilitator.settlement, "settle", forward_failed)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_teurc
    )

    # Доступу немає.
    assert result["valid"] is False
    types = [e["event_type"] for e in result["events"]]
    assert ev.ACCESS_GRANTED not in types
    # Журнал ПЕРЕД бродкастом присутній.
    assert ev.SETTLEMENT_INITIATED in types
    # Явний стан «кошти утримані» з tx_hash релею.
    assert ev.SETTLEMENT_FUNDS_HELD in types

    # Стан персистовано і придатний для звірки.
    held = fx.facilitator.list_held_settlements()
    assert len(held) == 1
    assert held[0]["tx_hash"] == "0xHELDRELAY"
    assert held[0]["event_type"] == ev.SETTLEMENT_FUNDS_HELD


def test_clean_relay_failure_is_distinct_from_held(facilitator_setup, monkeypatch):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")

    def relay_failed(_message, _auth):
        raise SettlementRelayError("релей відкотився, кошти не рухалися")

    monkeypatch.setattr(fx.facilitator.settlement, "settle", relay_failed)

    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_teurc
    )

    assert result["valid"] is False
    types = [e["event_type"] for e in result["events"]]
    assert ev.SETTLEMENT_FAILED in types
    assert ev.SETTLEMENT_FUNDS_HELD not in types  # кошти НЕ утримані
    assert fx.facilitator.list_held_settlements() == []  # звірка порожня
