"""M-3 — reconciliation утриманих (FUNDS_HELD) розрахунків.

FUNDS_HELD виникає ЛИШЕ в legacy-режимі: релей пройшов (покупця списано, nonce
спожито), форвард нетто сервісу відкотився → facilitator фізично тримає net_wei,
продавець не отримав нічого. M-3 робить із цього ОБЛІК + повторну перевірку +
ідемпотентний стан-машину — НЕ фейкову гарантію повернення.

Стан-машина (RESOLVED_* лише після фактичного on-chain підтвердження — паритет з M-2):

    HELD ──forward──▶ FORWARD_SUBMITTED ──confirmed──▶ RESOLVED_FORWARDED
    HELD ──refund_request──▶ REFUND_PENDING ──refund_execute──▶
                             REFUND_SUBMITTED ──confirmed──▶ RESOLVED_REFUNDED

Ключові інваріанти під тестом:
  - held персиститься з достатніми полями (net/seller/buyer/nonce), не лише tx_hash;
  - dry_run НІЧОГО не рухає й не змінює;
  - forward відновлює економіку (кошти → продавцю); RESOLVED лише по підтвердженню;
  - refund — ЛИШЕ заявка; фактичний переказ — окремий свідомий refund_execute;
  - refund_execute без попередньої заявки — відхилено;
  - self-heal: якщо попередня спроба вже підтверджена on-chain — без НОВОГО переказу;
  - повторний resolve — idempotent, без повторного руху коштів.

Рівні: store (без мережі) + facilitator (реальні tEURC-перекази на eth-tester).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_client  # noqa: E402
import chain  # noqa: E402
from facilitator import events as ev  # noqa: E402
from facilitator import store as store_mod  # noqa: E402
from facilitator.settlement import SettlementForwardError  # noqa: E402
from facilitator.store import Store  # noqa: E402

RELAY = "0xHELDRELAY0000000000000000000000000000000000000000000000000000dead"
NET = 995_000


# ==================== store-рівень (без мережі) ====================

def test_schema_version_bumped_to_4():
    assert store_mod.CURRENT_SCHEMA_VERSION == 4


def test_record_held_idempotent_does_not_reset_state():
    st = Store(":memory:")
    st.record_held(RELAY, net_wei=NET, seller="0xSeller", buyer="0xBuyer", nonce="0xabc")
    # Просуваємо стан, потім повторно реєструємо той самий релей.
    st.update_reconciliation(RELAY, state=store_mod.RECON_FORWARD_SUBMITTED, action_tx_hash="0xFWD")
    again = st.record_held(RELAY, net_wei=NET, seller="0xSeller", buyer="0xBuyer", nonce="0xabc")
    # Ідемпотентно: НЕ відкотилось у held, action_tx_hash збережено, один рядок.
    assert again["state"] == store_mod.RECON_FORWARD_SUBMITTED
    assert again["action_tx_hash"] == "0xFWD"
    assert len(st.list_reconciliation()) == 1


def test_update_reconciliation_coalesces_action_tx():
    st = Store(":memory:")
    st.record_held(RELAY, net_wei=NET, seller="0xSeller", buyer="0xBuyer", nonce="0xabc")
    st.update_reconciliation(RELAY, state=store_mod.RECON_FORWARD_SUBMITTED, action_tx_hash="0xFWD")
    # Перехід стану БЕЗ нового action_tx_hash не має стерти попередній.
    row = st.update_reconciliation(RELAY, state=store_mod.RECON_RESOLVED_FORWARDED)
    assert row["state"] == store_mod.RECON_RESOLVED_FORWARDED
    assert row["action_tx_hash"] == "0xFWD"


def test_list_reconciliation_filters_by_state():
    st = Store(":memory:")
    st.record_held("0xA", net_wei=1, seller="s", buyer="b", nonce="n")
    st.record_held("0xB", net_wei=2, seller="s", buyer="b", nonce="n")
    st.update_reconciliation("0xB", state=store_mod.RECON_RESOLVED_FORWARDED)
    held = st.list_reconciliation(state=store_mod.RECON_HELD)
    assert [r["relay_tx_hash"] for r in held] == ["0xA"]


# ==================== facilitator-рівень (реальні tEURC) ====================

def _bal(fx, addr):
    from web3 import Web3
    return fx.teurc.functions.balanceOf(Web3.to_checksum_address(addr)).call()


def _mint_facilitator(fx, amount):
    tx = chain.send_contract_tx(
        fx.w3, fx.deployer.key.hex(),
        fx.teurc.functions.mint(fx.facilitator_acct.address, amount))
    fx.w3.eth.wait_for_transaction_receipt(tx)


def _seed_held(fx, *, buyer=None, seller=None, net_wei=NET, relay=RELAY):
    buyer = buyer or fx.verified_no_sbt_agent.address
    seller = seller or fx.service_provider_acct.address
    return fx.facilitator.store.record_held(relay, net_wei=net_wei, seller=seller, buyer=buyer, nonce="0xdeadbeef")


def test_verify_and_settle_persists_reconciliation_record(facilitator_setup, monkeypatch):
    """Наскрізно: справжній FUNDS_HELD через verify_and_settle тепер персиститься
    в reconciliation з АДРЕСНИМИ полями (не лише tx_hash у журналі)."""
    fx = facilitator_setup
    payload = agent_client.build_and_sign_authorization(
        fx.verified_no_sbt_agent.key.hex(), fx.facilitator_acct.address, fx.price_wei,
        "/photo/kyiv-lavra", fx.teurc.address, fx.w3.eth.chain_id)

    def forward_failed(_m, _a):
        raise SettlementForwardError("форвард відкотився", relay_tx_hash=RELAY, net_wei=NET)

    monkeypatch.setattr(fx.facilitator.settlement, "settle", forward_failed)
    result = fx.facilitator.verify_and_settle(
        payload["authorization"], payload["resource"], payload["resource_salt"], fx.price_wei)

    assert result["valid"] is False
    assert ev.SETTLEMENT_FUNDS_HELD in [e["event_type"] for e in result["events"]]
    rec = fx.facilitator.store.get_reconciliation(RELAY)
    assert rec is not None
    assert rec["state"] == store_mod.RECON_HELD
    assert rec["net_wei"] == NET
    assert rec["seller"].lower() == fx.service_provider_acct.address.lower()
    assert rec["buyer"].lower() == fx.verified_no_sbt_agent.address.lower()
    assert rec["nonce"]  # nonce збережено для звірки


def test_forward_dry_run_moves_nothing(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    before = _bal(fx, fx.service_provider_acct.address)

    res = fx.facilitator.reconcile_held(RELAY, "forward", dry_run=True)

    assert res["ok"] is True and res["dry_run"] is True
    assert res["moved_funds"] is False and res["changed"] is False
    assert _bal(fx, fx.service_provider_acct.address) == before  # нічого не рухалось
    assert fx.facilitator.store.get_reconciliation(RELAY)["state"] == store_mod.RECON_HELD


def test_forward_confirmed_pays_seller(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    before = _bal(fx, fx.service_provider_acct.address)

    res = fx.facilitator.reconcile_held(RELAY, "forward", dry_run=False)

    assert res["ok"] is True and res["moved_funds"] is True
    assert res["state"] == store_mod.RECON_RESOLVED_FORWARDED
    assert _bal(fx, fx.service_provider_acct.address) == before + NET


def test_forward_idempotent_no_double_pay(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    fx.facilitator.reconcile_held(RELAY, "forward", dry_run=False)
    after_first = _bal(fx, fx.service_provider_acct.address)

    res = fx.facilitator.reconcile_held(RELAY, "forward", dry_run=False)

    assert res["ok"] is True and res["moved_funds"] is False
    assert _bal(fx, fx.service_provider_acct.address) == after_first  # без другого переказу


def test_forward_self_heal_without_new_transfer(facilitator_setup):
    """Симулює перерваний форвард: переказ уже пройшов on-chain, стан лишився
    FORWARD_SUBMITTED. Повторна звірка має РОЗПІЗНАТИ підтвердження й закрити
    розрахунок БЕЗ нового переказу (анти-подвійне списання)."""
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    # Ручний «попередній» форвард facilitator -> seller.
    from web3 import Web3
    tx = chain.send_contract_tx(
        fx.w3, fx.facilitator_acct.key.hex(),
        fx.teurc.functions.transfer(Web3.to_checksum_address(fx.service_provider_acct.address), NET))
    fx.w3.eth.wait_for_transaction_receipt(tx)
    fx.facilitator.store.update_reconciliation(RELAY, state=store_mod.RECON_FORWARD_SUBMITTED, action_tx_hash=tx)
    seller_bal = _bal(fx, fx.service_provider_acct.address)

    res = fx.facilitator.reconcile_held(RELAY, "forward", dry_run=False)

    assert res["ok"] is True and res["moved_funds"] is False  # self-heal, без нового переказу
    assert res["state"] == store_mod.RECON_RESOLVED_FORWARDED
    assert "self-heal" in res["detail"].lower()
    assert _bal(fx, fx.service_provider_acct.address) == seller_bal  # рівно один переказ


def test_refund_request_creates_pending_without_transfer(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    buyer_before = _bal(fx, fx.verified_no_sbt_agent.address)

    res = fx.facilitator.reconcile_held(RELAY, "refund_request", dry_run=False)

    assert res["ok"] is True and res["moved_funds"] is False
    assert res["state"] == store_mod.RECON_REFUND_PENDING
    assert _bal(fx, fx.verified_no_sbt_agent.address) == buyer_before  # заявка ≠ переказ


def test_refund_execute_requires_prior_request(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    buyer_before = _bal(fx, fx.verified_no_sbt_agent.address)

    res = fx.facilitator.reconcile_held(RELAY, "refund_execute", dry_run=False)

    assert res["ok"] is False and res["moved_funds"] is False
    assert fx.facilitator.store.get_reconciliation(RELAY)["state"] == store_mod.RECON_HELD
    assert _bal(fx, fx.verified_no_sbt_agent.address) == buyer_before


def test_refund_execute_confirmed_returns_buyer(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    buyer_before = _bal(fx, fx.verified_no_sbt_agent.address)

    fx.facilitator.reconcile_held(RELAY, "refund_request", dry_run=False)
    res = fx.facilitator.reconcile_held(RELAY, "refund_execute", dry_run=False)

    assert res["ok"] is True and res["moved_funds"] is True
    assert res["state"] == store_mod.RECON_RESOLVED_REFUNDED
    assert _bal(fx, fx.verified_no_sbt_agent.address) == buyer_before + NET


def test_refund_execute_idempotent_no_double_refund(facilitator_setup):
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    fx.facilitator.reconcile_held(RELAY, "refund_request", dry_run=False)
    fx.facilitator.reconcile_held(RELAY, "refund_execute", dry_run=False)
    after_first = _bal(fx, fx.verified_no_sbt_agent.address)

    res = fx.facilitator.reconcile_held(RELAY, "refund_execute", dry_run=False)

    assert res["ok"] is True and res["moved_funds"] is False
    assert _bal(fx, fx.verified_no_sbt_agent.address) == after_first  # без другого повернення


def test_forward_rejected_after_refund_pending(facilitator_setup):
    """Після заявки на повернення forward уже неможливий (конфлікт треків) —
    щоб не переслати продавцю кошти, які призначені до повернення покупцю."""
    fx = facilitator_setup
    _mint_facilitator(fx, 2_000_000)
    _seed_held(fx)
    seller_before = _bal(fx, fx.service_provider_acct.address)

    fx.facilitator.reconcile_held(RELAY, "refund_request", dry_run=False)
    res = fx.facilitator.reconcile_held(RELAY, "forward", dry_run=False)

    assert res["ok"] is False and res["moved_funds"] is False
    assert _bal(fx, fx.service_provider_acct.address) == seller_before


def test_unknown_held_and_unknown_action(facilitator_setup):
    fx = facilitator_setup
    assert fx.facilitator.reconcile_held("0xNOPE", "forward")["ok"] is False
    _seed_held(fx)
    assert fx.facilitator.reconcile_held(RELAY, "bogus")["ok"] is False
