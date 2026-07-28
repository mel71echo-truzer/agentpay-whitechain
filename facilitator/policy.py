"""Policy Engine — вирішує allow/deny за identity + вимогами ресурсу.

Чиста функція (жодного Web3/мережі): бере вже готовий agent_identity
(з identity.py) і опис ресурсу, повертає {"allow": bool, "reason": str}.

Порядок і тексти причин збережені з Фази 1, щоб зовнішня поведінка
(і тести) не змінилися:
  1. немає Soul            -> "not KYA-verified"
  2. Soul є, але не verified -> "KYA not active"
  3. reputation_tier < min  -> "insufficient reputation"
  4. spend-limit (заглушка Фази 2) -> завжди allow (TODO: реальні ліміти)
"""

from __future__ import annotations


def check_policy(agent_identity: dict, resource: dict) -> dict:
    """resource: {"min_reputation_tier": int, "price_wei": int, ...}."""
    if not agent_identity.get("has_soul"):
        return {"allow": False, "reason": "Агент не має верифікованого WB Soul (not KYA-verified)."}

    if not agent_identity.get("verified"):
        return {"allow": False, "reason": "KYC агента не активний (KYA not active)."}

    min_tier = int(resource.get("min_reputation_tier", 0) or 0)
    tier = int(agent_identity.get("reputation_tier", 0) or 0)
    if tier < min_tier:
        return {
            "allow": False,
            "reason": (
                f"Недостатня репутація: tier {tier} < потрібно {min_tier} (insufficient reputation)."
            ),
        }

    # Spend-limit gate — заглушка Фази 2. Реальні per-agent/per-day ліміти —
    # Roadmap (потребують стану у store + політик; поки завжди дозволяємо).
    if not _within_spend_limits(agent_identity, resource):  # pragma: no cover - завжди True поки заглушка
        return {"allow": False, "reason": "Перевищено ліміт витрат агента (spend limit)."}

    return {"allow": True, "reason": "OK"}


def _within_spend_limits(agent_identity: dict, resource: dict) -> bool:
    """TODO(Phase 3): реальні ліміти витрат на агента/період. Поки — no-op."""
    return True
