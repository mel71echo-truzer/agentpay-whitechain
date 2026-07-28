"""Фаза 2, money-type міграція (F1/F5, рішення №4): гроші — int wei всюди
всередині; float у грошових шляхах не існує.

Ці тести фіксують нові інваріанти (падали б на дореформеному коді, де ціни
були float, а *_teurc у відповіді — float):
  - config парсить ціни Decimal→int wei на завантаженні;
  - money.teurc_to_wei відхиляє суб-wei (>6 знаків) і float-вхід;
  - money round-trip int↔рядок точний;
  - відповідь facilitator несе int *_wei + рядкові *_teurc (жодного float);
  - SpendLedger рахує в int wei з ТОЧНОЮ межею (без float-епсилону).

Детерміновано: без мережі там, де можливо; фікстура — локальний eth-tester.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import config  # noqa: E402
import money  # noqa: E402


# ---------- money boundary (без мережі) ----------

def test_config_prices_are_int_wei_on_load():
    assert isinstance(config.RESOURCE_PRICE_WEI, int)
    assert config.RESOURCE_PRICE_WEI == 20_000  # 0.02 * 1e6
    assert isinstance(config.PREMIUM_RESOURCE_PRICE_WEI, int)
    assert config.PREMIUM_RESOURCE_PRICE_WEI == 100_000  # 0.10 * 1e6
    assert isinstance(config.AUTHOR_MAX_SPEND_WEI, int)


@pytest.mark.parametrize(
    "amount,expected",
    [("0.02", 20_000), ("0.10", 100_000), ("1.0", 1_000_000), ("0.000001", 1), ("0", 0)],
)
def test_teurc_to_wei_exact(amount, expected):
    assert money.teurc_to_wei(amount, 6) == expected
    assert money.teurc_to_wei(Decimal(amount), 6) == expected


@pytest.mark.parametrize("bad", ["0.0000001", "2.6755555", "0.1234567"])
def test_teurc_to_wei_rejects_sub_wei(bad):
    # >6 знаків = тонше за точність токена -> гучна відмова, не тихе округлення.
    with pytest.raises(ValueError):
        money.teurc_to_wei(bad, 6)


def test_teurc_to_wei_rejects_float_input():
    # float — джерело похибки; свідомо не приймаємо.
    with pytest.raises(TypeError):
        money.teurc_to_wei(0.02, 6)


@pytest.mark.parametrize(
    "wei,text",
    [(20_000, "0.020000"), (100_000, "0.100000"), (1_000_000, "1.000000"), (1, "0.000001"), (0, "0.000000")],
)
def test_wei_to_teurc_str_roundtrip(wei, text):
    assert money.wei_to_teurc_str(wei, 6) == text
    assert money.teurc_to_wei(money.wei_to_teurc_str(wei, 6), 6) == wei


# ---------- SpendLedger в int wei ----------

def test_spend_ledger_is_int_wei_with_exact_boundary(tmp_path):
    path = str(tmp_path / "ledger.json")
    ledger = agent_client.SpendLedger(path=path, max_spend_wei=100_000)
    ledger.reset()

    ledger.ensure_can_spend(60_000)
    ledger.record(60_000, to="0xabc", relay_tx_hash=None)
    assert ledger.spent_wei == 60_000
    assert isinstance(ledger.spent_wei, int)

    # Рівно до межі — дозволено (точна ціла арифметика, без епсилону).
    ledger.ensure_can_spend(40_000)
    # На один wei більше межі — відхилено.
    with pytest.raises(agent_client.SpendLimitExceeded):
        ledger.ensure_can_spend(40_001)


# ---------- відповідь facilitator: int wei + рядкові teurc, без float ----------

def _sign(fx, agent, resource, value_wei=None):
    value_wei = fx.price_wei if value_wei is None else value_wei
    return agent_client.build_and_sign_authorization(
        agent.key.hex(), fx.facilitator_acct.address, value_wei, resource, fx.teurc.address, fx.w3.eth.chain_id
    )


def test_settlement_response_has_int_wei_and_string_teurc(facilitator_setup):
    fx = facilitator_setup
    payload = _sign(fx, fx.verified_no_sbt_agent, "/photo/kyiv-lavra")
    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei
    )
    assert result["valid"] is True
    # Авторитетні суми — int wei.
    for k in ("amount_wei", "fee_wei", "net_wei"):
        assert isinstance(result[k], int), k
    # Людські поля — рядок (НЕ float).
    for k in ("amount_teurc", "fee_teurc", "net_to_service_provider_teurc"):
        assert isinstance(result[k], str), k
    # Узгодженість: fee + net == amount (точно, ціла арифметика).
    assert result["fee_wei"] + result["net_wei"] == result["amount_wei"] == fx.price_wei
    # Рядок відповідає wei.
    assert result["amount_teurc"] == money.wei_to_teurc_str(fx.price_wei, 6)
