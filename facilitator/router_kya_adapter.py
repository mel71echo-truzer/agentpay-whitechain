"""router_kya_adapter.py — підключуваний KYA-джерело для AgentPayRouter.

`AgentPayRouter` (atomic-режим) читає on-chain KYA-гейт через ОДИН інтерфейс:

    soulOf(address) -> uint256
    isVerified(uint256) -> bool

Атрибутний WB Soul (`ISoulAttributeRegistry` + IS_VERIFIED-атрибут), який читає
off-chain `facilitator/identity.py`, цього `isVerified(uint256)` НЕ експонує —
тому роутер деплоїться проти окремого реєстру. Цей модуль робить вибір цього
реєстру ПІДКЛЮЧУВАНИМ, а не захардкодженим у demo/deploy:

  - MockRouterKYAAdapter — працює вже сьогодні проти `MockRouterKYA` (заглушка).
  - WBSoulRouterKYAAdapter — реальна інтеграція. Свідомо НЕ вгадується: точна
    схема верифікації WB Soul (який контракт/атрибут дає `isVerified`, і чи
    потрібен on-chain shim, що приводить атрибутний реєстр до `isVerified(uint256)`)
    має бути ПІДТВЕРДЖЕНА в WhiteBIT перед mainnet. До того — явний
    NotImplementedError з чітким TODO, а не мовчазна заглушка.

Обидва адаптери реалізують один інтерфейс `RouterKYAAdapter`, тож перемикання
mock ↔ real — це зміна конфігу (`USE_MOCK_SOUL`), а не переписування коду.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chain  # noqa: E402


class RouterKYAAdapter(ABC):
    """Спільний seam: як роутер (і demo/deploy) бачать KYA-джерело.

    read-методи (`soul_of`, `is_verified`) дзеркалять рівно те, що роутер читає
    on-chain. seed-методи потрібні лише для тестових/mock-реєстрів (реальний WB
    Soul засіюється WhiteBIT-івським KYC, а не нами) — тому в базі вони кидають.
    """

    @property
    @abstractmethod
    def address(self) -> str:
        """Адреса KYA-реєстру, з яким деплоїться роутер (2-й арг конструктора)."""

    @abstractmethod
    def soul_of(self, account: str) -> int:
        """soulOf(account) — 0, якщо душі немає."""

    @abstractmethod
    def is_verified(self, soul_id: int) -> bool:
        """isVerified(soulId) — чи верифікована ця душа."""

    def seed_verified(self, deployer_key: str, accounts: list[str]) -> None:
        """Засіяти verified-souls (лише mock). Реальний WB Soul так робити не можна."""
        raise NotImplementedError(
            f"{type(self).__name__} не можна засіювати з коду — верифікація на "
            "реальному WB Soul робиться через WhiteBIT KYC, а не цим процесом."
        )


class MockRouterKYAAdapter(RouterKYAAdapter):
    """Проти `MockRouterKYA` (contracts/mocks/MockRouterKYA.sol) — заглушка WB
    Soul, що реалізує рівно soulOf(address)+isVerified(uint256), потрібні роутеру.
    Використовується на local/testnet, доки WhiteBIT не опублікує WB Soul на
    testnet (див. .env: USE_MOCK_SOUL=true)."""

    def __init__(self, w3, kya_address: str):
        self._w3 = w3
        self._address = kya_address
        self._c = chain.get_contract(w3, "MockRouterKYA", kya_address)

    @property
    def address(self) -> str:
        return self._address

    def soul_of(self, account: str) -> int:
        return self._c.functions.soulOf(account).call()

    def is_verified(self, soul_id: int) -> bool:
        return self._c.functions.isVerified(soul_id).call()

    def seed_verified(self, deployer_key: str, accounts: list[str]) -> None:
        """setSoul(agent, i)+setVerified(i, True) для кожного агента. soul_id
        стартує з 1 (0 у роутері означає «немає душі»)."""
        for i, account in enumerate(accounts, start=1):
            tx = chain.send_contract_tx(self._w3, deployer_key, self._c.functions.setSoul(account, i))
            self._w3.eth.wait_for_transaction_receipt(tx)
            tx = chain.send_contract_tx(self._w3, deployer_key, self._c.functions.setVerified(i, True))
            self._w3.eth.wait_for_transaction_receipt(tx)


class WBSoulRouterKYAAdapter(RouterKYAAdapter):
    """РЕАЛЬНА інтеграція WB Soul для роутерового KYA-гейту — НЕ реалізована
    навмисно (правило: не вигадувати невідому схему).

    ────────────────────────────────────────────────────────────────────────
    TODO (перед mainnet, коли схему WB Soul підтверджено в WhiteBIT):

      Проблема: роутер читає `isVerified(uint256)`, а атрибутний WB Soul
      (ISoulAttributeRegistry) віддає верифікацію як АТРИБУТ душі, а не як
      функцію `isVerified(uint256)`. Тому напряму приставити роутер до реального
      WB Soul НЕ можна.

      Варіант A (рекомендований) — on-chain shim-контракт
      `WBSoulRouterKYAShim.sol`, що імплементує soulOf(address)+isVerified(uint256),
      делегуючи в реальний ISoulRegistry.soulOf і читаючи IS_VERIFIED-атрибут
      через ISoulAttributeRegistry. Роутер деплоїться проти цього shim.
      Цей адаптер тоді просто читає shim (як Mock вище).

      Варіант B — приймати верифікований soul_id від facilitator-а (identity.py
      вже читає атрибутний WB Soul off-chain) і кешувати в простий on-chain
      registry. Слабший (довіра до facilitator-а), лишений як fallback.

      Що треба ПІДТВЕРДИТИ, а не гадати:
        - точна адреса IS_VERIFIED-атрибута на цільовій мережі;
        - сигнатура читання атрибута (bool? uint? threshold?) в
          ISoulAttributeRegistry для «verified»;
        - чи є вже канонічний `isVerified`-view десь у WB Soul, який можна
          читати напряму (тоді shim не потрібен).

      До підтвердження USE_MOCK_SOUL=true → MockRouterKYAAdapter.
    ────────────────────────────────────────────────────────────────────────
    """

    def __init__(self, w3, kya_address: str):
        self._w3 = w3
        self._address = kya_address

    @property
    def address(self) -> str:
        return self._address

    def _not_ready(self):
        return NotImplementedError(
            "WBSoulRouterKYAAdapter не готовий: схема верифікації реального WB "
            "Soul не підтверджена. Потрібен on-chain shim isVerified(uint256) над "
            "ISoulAttributeRegistry — див. TODO у router_kya_adapter.py. Поки що "
            "USE_MOCK_SOUL=true (MockRouterKYAAdapter)."
        )

    def soul_of(self, account: str) -> int:
        raise self._not_ready()

    def is_verified(self, soul_id: int) -> bool:
        raise self._not_ready()


def get_router_kya_adapter(w3, kya_address: str, *, use_mock: bool) -> RouterKYAAdapter:
    """Фабрика: обирає KYA-адаптер за конфігом (USE_MOCK_SOUL).

    use_mock=True  -> MockRouterKYAAdapter (local/testnet, доки нема реального WB Soul).
    use_mock=False -> WBSoulRouterKYAAdapter (кине NotImplementedError із TODO,
                      доки схему WB Soul не підтверджено — навмисно, щоб не
                      вдавати робочу інтеграцію проти невідомої схеми).
    """
    if use_mock:
        return MockRouterKYAAdapter(w3, kya_address)
    return WBSoulRouterKYAAdapter(w3, kya_address)
