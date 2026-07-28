"""Фаза 2, M1 (store.py) — MAJOR: «один процес» був незадокументованим і
незахищеним інваріантом (див. рішення №1 автора).

`threading.Lock` у Store серіалізує записи ЛИШЕ в межах процесу. Якщо
підняти кілька воркерів/процесів на тому самому файлі БД, кожен матиме
свій Lock і свою серіалізацію — атомарність лічильників втрачається.

Ці тести фіксують явну перевірку на старті: другий Store на тому самому
файлі БД падає з зрозумілою помилкою. Для `:memory:` перевірки немає
(окрема БД на з'єднання — конкуренції між процесами не існує).

Детерміновано: без мережі, без sleep, без часу.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facilitator.store import Store, StoreLockError  # noqa: E402


def test_second_store_on_same_file_raises(tmp_path):
    db = str(tmp_path / "agentpay.db")
    s1 = Store(db)
    try:
        with pytest.raises(StoreLockError):
            Store(db)
    finally:
        s1.close()


def test_lock_released_after_close_allows_reopen(tmp_path):
    db = str(tmp_path / "agentpay.db")
    s1 = Store(db)
    s1.close()
    # Після close() блок звільнено — легітимний рестарт того ж процесу.
    s2 = Store(db)
    s2.close()


def test_memory_store_has_no_cross_instance_lock():
    # :memory: — окрема БД на з'єднання, конкуренції між процесами немає.
    a = Store(":memory:")
    b = Store(":memory:")
    a.close()
    b.close()
