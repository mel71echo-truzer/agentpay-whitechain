"""Юніт-тести IdentityReader (Фаза 2). Читання WB Soul на локальному ланцюжку.

Використовує спільну фікстуру facilitator_setup (tests/conftest.py):
verified_agent (Soul+IsVerified+1 SBT), verified_no_sbt_agent (Soul+IsVerified),
unverified_agent (без Soul).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chain  # noqa: E402
from eth_account import Account  # noqa: E402


def test_no_soul_identity(facilitator_setup):
    fx = facilitator_setup
    ident = fx.facilitator.identity.get_agent_identity(fx.unverified_agent.address)
    assert ident["has_soul"] is False
    assert ident["verified"] is False
    assert ident["reputation_tier"] == 0


def test_verified_no_sbt_identity_tier_0(facilitator_setup):
    fx = facilitator_setup
    ident = fx.facilitator.identity.get_agent_identity(fx.verified_no_sbt_agent.address)
    assert ident["has_soul"] is True
    assert ident["verified"] is True
    assert ident["sbt_count"] == 0
    assert ident["reputation_tier"] == 0  # cold start -> attested-tier only


def test_verified_with_sbt_identity_tier_1(facilitator_setup):
    fx = facilitator_setup
    ident = fx.facilitator.identity.get_agent_identity(fx.verified_agent.address)
    assert ident["has_soul"] is True
    assert ident["verified"] is True
    assert ident["sbt_count"] == 1
    assert ident["reputation_tier"] == 1  # attested by the SBT


def test_soul_but_not_verified(facilitator_setup):
    fx = facilitator_setup
    agent = Account.create()
    tx = chain.send_contract_tx(fx.w3, fx.deployer.key.hex(), fx.soul_registry.functions.registerSoul(agent.address))
    fx.w3.eth.wait_for_transaction_receipt(tx)
    # НЕ викликаємо setVerified -> Soul є, але KYA не активний.
    ident = fx.facilitator.identity.get_agent_identity(agent.address)
    assert ident["has_soul"] is True
    assert ident["verified"] is False


def test_behavioral_stats_drive_tier_without_sbt(facilitator_setup):
    fx = facilitator_setup
    # Засіваємо "veteran" статистику для агента БЕЗ SBT -> формула дає tier 2.
    now = time.time()
    fx.facilitator.store.upsert_agent_stats(
        fx.verified_no_sbt_agent.address,
        completed_payments=30,
        disputes=0,
        refunds=0,
        fraud_flags=0,
        first_seen_ts=now - 200 * 86400,
    )
    ident = fx.facilitator.identity.get_agent_identity(fx.verified_no_sbt_agent.address)
    assert ident["sbt_count"] == 0
    assert ident["reputation_tier"] == 2  # driven by the reputation formula, not an SBT
