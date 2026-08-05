"""Аудит: покриття шляху pay_and_fetch(ledger=...), який раніше падав з
NameError (agent_client.py:322 посилався на неіснуючий `price_teurc` замість
`price_wei` — залишок money-міграції). Цей шлях використовує author/agent.py,
але жоден тест його не зачіпав, тож баг був латентним.

Тест мокає HTTP (без мережі/сервера): 402 -> підпис офчейн -> 200 -> запис у
ledger. Падав би до фікса, проходить після.
"""

import base64
import json
import sys
from pathlib import Path

from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402

PAY_TO = "0x000000000000000000000000000000000000dEaD"
TEURC = "0x0000000000000000000000000000000000000001"
PRICE_WEI = 20_000


class _Resp402:
    status_code = 402

    def json(self):
        return {
            "accepts": [
                {"payTo": PAY_TO, "price_wei": PRICE_WEI, "price_teurc": "0.020000",
                 "resource": "/photo/x", "asset_address": TEURC}
            ]
        }


class _Resp200:
    status_code = 200
    content = b"PNGDATA"

    def __init__(self):
        settlement = {"relay_tx_hash": "0xrelay", "fee_teurc": "0.000100",
                      "reputation_tier": 0, "forward_tx_hash": "0xfwd"}
        enc = base64.b64encode(json.dumps(settlement).encode()).decode()
        self.headers = {"content-type": "image/png", "X-Settlement": enc}


def test_pay_and_fetch_records_price_wei_in_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_client.requests, "get", lambda *a, **k: _Resp402())
    monkeypatch.setattr(agent_client.requests, "post", lambda *a, **k: _Resp200())

    ledger = agent_client.SpendLedger(path=str(tmp_path / "l.json"), max_spend_wei=10**9)
    ledger.reset()

    key = Account.create().key.hex()
    result = agent_client.pay_and_fetch(
        "http://sp/photo/x", private_key=key, ledger=ledger, chain_id=1, expected_pay_to=PAY_TO
    )

    # Ключова перевірка: ledger отримав price_wei (int), а не впав з NameError.
    assert ledger.spent_wei == PRICE_WEI
    assert ledger._state["payments"][0]["amount_wei"] == PRICE_WEI
    assert result.content == b"PNGDATA"
    assert result.relay_tx_hash == "0xrelay"
