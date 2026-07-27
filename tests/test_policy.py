"""Юніт-тести Policy Engine (Фаза 2). Чиста функція — без мережі."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facilitator import policy  # noqa: E402


def _identity(has_soul=True, verified=True, tier=0):
    return {"has_soul": has_soul, "verified": verified, "reputation_tier": tier}


def test_no_soul_denied():
    d = policy.check_policy(_identity(has_soul=False), {"min_reputation_tier": 0})
    assert d["allow"] is False
    assert "not KYA-verified" in d["reason"] or "Soul" in d["reason"]


def test_not_verified_denied():
    d = policy.check_policy(_identity(verified=False), {"min_reputation_tier": 0})
    assert d["allow"] is False
    assert "KYA not active" in d["reason"] or "не активний" in d["reason"]


def test_allow_when_tier_meets_min():
    d = policy.check_policy(_identity(tier=1), {"min_reputation_tier": 1})
    assert d["allow"] is True


def test_deny_when_tier_below_min():
    d = policy.check_policy(_identity(tier=0), {"min_reputation_tier": 1})
    assert d["allow"] is False
    assert "insufficient reputation" in d["reason"] or "репутація" in d["reason"]


def test_higher_tier_allowed_on_lower_requirement():
    d = policy.check_policy(_identity(tier=2), {"min_reputation_tier": 1})
    assert d["allow"] is True
