"""Юніт-тести Reputation Engine (Фаза 2, Компонент 3).

Перевіряють, що ФІКСОВАНА формула дає очікувані score/tier на кількох
вхідних наборах. Модуль чистий — тести без мережі/сховища.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facilitator import reputation  # noqa: E402


def test_max_score_is_100_tier_2():
    r = reputation.compute_reputation(
        completed_payments=20, dispute_ratio=0.0, refund_ratio=0.0, days_active=365, has_sbt=True, fraud_flags=0
    )
    assert r["score"] == pytest.approx(100.0)
    assert r["tier"] == 2


def test_veteran_tier_2():
    # 30 + 25 + 20 + 15*(200/365) + 10 = 93.22
    r = reputation.compute_reputation(
        completed_payments=30, dispute_ratio=0.0, refund_ratio=0.0, days_active=200, has_sbt=True, fraud_flags=0
    )
    assert r["score"] == pytest.approx(93.22, abs=0.05)
    assert r["tier"] == 2


def test_mid_tier_1():
    # 15 + 22.5 + 20 + 15*(100/365) = 61.61
    r = reputation.compute_reputation(
        completed_payments=10, dispute_ratio=0.1, refund_ratio=0.0, days_active=100, has_sbt=False, fraud_flags=0
    )
    assert r["score"] == pytest.approx(61.61, abs=0.05)
    assert r["tier"] == 1


def test_flagged_agent_tier_0():
    # 3 + 12.5 + 14 + 15*(5/365) - 25 = 4.71
    r = reputation.compute_reputation(
        completed_payments=2, dispute_ratio=0.5, refund_ratio=0.3, days_active=5, has_sbt=False, fraud_flags=1
    )
    assert r["score"] == pytest.approx(4.71, abs=0.05)
    assert r["tier"] == 0


def test_fraud_penalty_clamps_to_zero():
    r = reputation.compute_reputation(
        completed_payments=20, dispute_ratio=0.0, refund_ratio=0.0, days_active=365, has_sbt=True, fraud_flags=10
    )
    assert r["score"] == 0.0
    assert r["tier"] == 0


def test_cold_start_quirk_documented():
    # Свіжий агент без історії отримує 45 (бо (1-0)=1 у dispute/refund
    # доданках) -> tier 1. Це відома слабкість заданої формули; identity.py
    # тому НЕ застосовує формулу до агента без записаної історії (cold start),
    # а бере суто SBT-attested tier. Тест фіксує саме поведінку формули.
    r = reputation.compute_reputation()
    assert r["score"] == pytest.approx(45.0)
    assert r["tier"] == 1


def test_tier_boundaries():
    assert reputation.tier_for_score(39.9) == 0
    assert reputation.tier_for_score(40.0) == 1
    assert reputation.tier_for_score(69.9) == 1
    assert reputation.tier_for_score(70.0) == 2


def test_stats_to_reputation_derives_ratios():
    # 4 completed, 2 disputes -> dispute_ratio 0.5; 1 refund -> 0.25.
    now = 1_000_000_000.0
    stats = {
        "completed_payments": 4,
        "disputes": 2,
        "refunds": 1,
        "fraud_flags": 0,
        "first_seen_ts": now - 365 * 86400,  # рівно рік -> tenure_norm 1
    }
    r = reputation.stats_to_reputation(stats, has_sbt=False, now_ts=now)
    # 30*(4/20) + 25*(1-0.5) + 20*(1-0.25) + 15*1 + 0 = 6 + 12.5 + 15 + 15 = 48.5
    assert r["score"] == pytest.approx(48.5, abs=0.05)
    assert r["tier"] == 1
