"""Identity — читання WB Soul (KYA) + агрегація репутації (Фаза 2, Компонент 1).

ЄДИНЕ місце в коді, що знає, як звертатися до WB Soul (soulOf +
IsVerified-атрибут + SBT). Ніхто інший не читає Soul напряму. Так у Фазі
2.5+ можна замінити джерело identity (реальний Soul, інший registry, тощо)
не чіпаючи policy/payment/settlement.

get_agent_identity(addr) -> {
    "soul_id": int,
    "has_soul": bool,
    "verified": bool,
    "sbt_count": int,
    "reputation_tier": int,   # 0/1/2
    "reputation_score": float # 0..100
}

Reputation tier рахується як max(двох сигналів):
  - on-chain "attested" tier за кількістю SBT (0 SBT -> 0, 1 -> 1, 2+ -> 2):
    SBT — це якір, який мінтиться, КОЛИ агент перетнув поріг tier
    (див. reputation-anchor у README/Компонент 3), тож наявний SBT
    засвідчує вже досягнутий tier;
  - freshly computed behavioral tier за формулою reputation.py, АЛЕ лише
    якщо в store є поведінкові дані про агента. Для агента без записаної
    історії (cold start) беремо суто SBT-attested tier — це зберігає
    поведінку Фази 1 (1 SBT == tier 1) і не дає "порожньому" агенту
    безкоштовний tier через (1 - 0)=1 доданки формули.
"""

from __future__ import annotations

import logging
import time

from web3 import Web3

from facilitator import reputation

logger = logging.getLogger(__name__)


def sbt_attested_tier(sbt_count: int) -> int:
    """On-chain attested tier за кількістю SBT — мапінг з Фази 1."""
    if sbt_count <= 0:
        return 0
    if sbt_count == 1:
        return 1
    return 2


class IdentityReader:
    def __init__(self, soul_registry, attribute_registry, sbt_registry, is_verified_attribute, store, *, n_target: int):
        self.soul_registry = soul_registry
        self.attribute_registry = attribute_registry
        self.sbt_registry = sbt_registry
        self.is_verified_attribute = Web3.to_checksum_address(is_verified_attribute)
        self.store = store
        self.n_target = n_target

    def get_agent_identity(self, address: str) -> dict:
        address = Web3.to_checksum_address(address)

        soul_id = self.soul_registry.functions.soulOf(address).call()
        if soul_id == 0:
            return {
                "soul_id": 0,
                "has_soul": False,
                "verified": False,
                "sbt_count": 0,
                "reputation_tier": 0,
                "reputation_score": 0.0,
            }

        raw_value = self.attribute_registry.functions.soulAttributeValue(
            soul_id, self.is_verified_attribute
        ).call()
        verified = bytes(raw_value) != b"\x00" * 20

        sbt_count = self.sbt_registry.functions.tokensCountBySoul(soul_id).call()
        attested_tier = sbt_attested_tier(sbt_count)

        # Поведінкова репутація — лише якщо в store є історія про агента.
        stats = self.store.get_agent_stats(address) if self.store is not None else None
        if stats is not None:
            rep = reputation.stats_to_reputation(
                stats, has_sbt=sbt_count > 0, now_ts=time.time(), n_target=self.n_target
            )
            behavioral_tier = rep["tier"]
            score = rep["score"]
        else:
            behavioral_tier = 0
            score = float(attested_tier * 35)  # грубий проксі для показу, не для гейту

        reputation_tier = max(attested_tier, behavioral_tier)

        return {
            "soul_id": soul_id,
            "has_soul": True,
            "verified": verified,
            "sbt_count": sbt_count,
            "reputation_tier": reputation_tier,
            "reputation_score": score,
        }
