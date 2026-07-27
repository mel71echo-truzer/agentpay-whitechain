"""Reputation Engine — офчейн-розрахунок репутації агента (Фаза 2, Компонент 3).

Єдина відповідальність: за поведінковими даними агента порахувати score
(0..100) і tier (0/1/2) за ФІКСОВАНОЮ формулою (не вигадуємо свою):

  score =
      30 * completed_norm     # min(completed / N_target, 1),  N_target=20 за замовч.
    + 25 * (1 - dispute_ratio)
    + 20 * (1 - refund_ratio)
    + 15 * tenure_norm        # min(days_active / 365, 1)
    + 10 * sbt_bonus          # 1.0 якщо має довірчий SBT, інакше 0
    -  fraud_penalty          # 25 * fraud_flags
  score клампиться в [0, 100].

  tiers: 0..39 -> tier 0, 40..69 -> tier 1, 70..100 -> tier 2

Модуль чистий (без Web3/мережі/сховища) — легко юніт-тестувати. Джерело
поведінкових даних (лічильники completed/disputes/refunds/first_seen/fraud)
живе у facilitator/store.py і оновлюється на подіях settlement; конвертація
з сирих лічильників у входи цієї формули — stats_to_reputation() нижче.
"""

from __future__ import annotations

FRAUD_PENALTY_PER_FLAG = 25.0
DEFAULT_N_TARGET = 20


def tier_for_score(score: float) -> int:
    """0..39 -> 0, 40..69 -> 1, 70..100 -> 2."""
    if score >= 70:
        return 2
    if score >= 40:
        return 1
    return 0


def compute_reputation(
    *,
    completed_payments: int = 0,
    dispute_ratio: float = 0.0,
    refund_ratio: float = 0.0,
    days_active: float = 0.0,
    has_sbt: bool = False,
    fraud_flags: int = 0,
    n_target: int = DEFAULT_N_TARGET,
) -> dict:
    """Рахує {"score": float 0..100, "tier": int} за фіксованою формулою вище.

    dispute_ratio / refund_ratio очікуються вже нормалізованими в [0, 1].
    """
    completed_norm = min(completed_payments / n_target, 1.0) if n_target > 0 else 0.0
    tenure_norm = min(days_active / 365.0, 1.0)
    sbt_bonus = 1.0 if has_sbt else 0.0
    fraud_penalty = FRAUD_PENALTY_PER_FLAG * max(fraud_flags, 0)

    score = (
        30.0 * completed_norm
        + 25.0 * (1.0 - _clamp01(dispute_ratio))
        + 20.0 * (1.0 - _clamp01(refund_ratio))
        + 15.0 * tenure_norm
        + 10.0 * sbt_bonus
        - fraud_penalty
    )
    score = max(0.0, min(100.0, score))

    return {"score": round(score, 2), "tier": tier_for_score(score)}


def stats_to_reputation(stats: dict, has_sbt: bool, *, now_ts: float, n_target: int = DEFAULT_N_TARGET) -> dict:
    """Конвертує сирі лічильники з store.agent_stats у входи формули і рахує репутацію.

    stats: {"completed_payments", "disputes", "refunds", "first_seen_ts", "fraud_flags"}
    Ratio визначаємо як disputes / max(completed, 1) (клампимо ≤ 1) — щоб один
    dispute на одну оплату не давав ratio > 1.
    """
    completed = int(stats.get("completed_payments", 0) or 0)
    disputes = int(stats.get("disputes", 0) or 0)
    refunds = int(stats.get("refunds", 0) or 0)
    fraud_flags = int(stats.get("fraud_flags", 0) or 0)
    first_seen = stats.get("first_seen_ts") or now_ts

    denom = max(completed, 1)
    dispute_ratio = min(disputes / denom, 1.0)
    refund_ratio = min(refunds / denom, 1.0)
    days_active = max(0.0, (now_ts - first_seen) / 86400.0)

    return compute_reputation(
        completed_payments=completed,
        dispute_ratio=dispute_ratio,
        refund_ratio=refund_ratio,
        days_active=days_active,
        has_sbt=has_sbt,
        fraud_flags=fraud_flags,
        n_target=n_target,
    )


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))
