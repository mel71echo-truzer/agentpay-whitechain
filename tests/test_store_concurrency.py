"""Фаза 2, самоперевірка (B) — конкурентність: атомарність лічильника store.

increment_completed_payment робить read-modify-write через SQL `+1` під
threading.Lock. Тест перевіряє, що при паралельних викликах з кількох потоків
жоден інкремент не губиться (final == сумі всіх інкрементів) — тобто в межах
ОДНОГО процесу лічильник атомарний.

Межа задокументована у store.py: Lock захищає лише в межах процесу; кілька
процесів форсовано заборонені файловим локом (test_store_single_process).

Детермінований наслідок: підсумковий лічильник ТОЧНО дорівнює N*threads,
незалежно від планування потоків (у цьому й суть перевірки на втрачені записи).
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facilitator.store import Store  # noqa: E402


def test_increment_is_atomic_under_concurrency():
    store = Store(":memory:")
    try:
        addr = "0x000000000000000000000000000000000000dEaD"
        threads_n = 8
        per_thread = 200
        barrier = threading.Barrier(threads_n)

        def worker():
            barrier.wait()  # максимізуємо перекриття
            for _ in range(per_thread):
                store.increment_completed_payment(addr)

        threads = [threading.Thread(target=worker) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = store.get_agent_stats(addr)
        # Жодного втраченого інкремента.
        assert stats["completed_payments"] == threads_n * per_thread
    finally:
        store.close()
