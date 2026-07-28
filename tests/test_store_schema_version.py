"""Фаза 2 (follow-up) — схема store версіонована; старий файл БД відхиляється
гучно на старті, а не падає пізніше на першому записі.

Контекст: money-type міграція змінила capabilities.price REAL -> price_wei
INTEGER. Наявний .db зі старою схемою раніше відкривався мовчки й падав аж на
першому upsert_capability (`no column named price_wei`). Тепер — StoreSchemaError
на старті з інструкцією.

Детерміновано: без мережі/часу.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from facilitator.store import CURRENT_SCHEMA_VERSION, Store, StoreSchemaError  # noqa: E402


def _write_old_schema_db(path: str) -> None:
    """Відтворює схему до міграції: capabilities.price REAL, user_version=0."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE capabilities (
            id TEXT PRIMARY KEY, capability_type TEXT NOT NULL, provider_url TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0, min_reputation_tier INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute("INSERT INTO capabilities VALUES ('c1','image-generation','http://x',0.02,0,1)")
    conn.commit()
    conn.close()


def test_old_schema_db_rejected_loudly(tmp_path):
    db = str(tmp_path / "legacy.db")
    _write_old_schema_db(db)
    with pytest.raises(StoreSchemaError) as exc:
        Store(db)
    # Помилка дієва: згадує STORE_DB_PATH / шлях до відновлення.
    assert "STORE_DB_PATH" in str(exc.value) or "схем" in str(exc.value).lower()


def test_fresh_db_gets_current_version_and_reopens(tmp_path):
    db = str(tmp_path / "fresh.db")
    s1 = Store(db)
    s1.upsert_capability(
        {"id": "c1", "capability_type": "t", "provider_url": "u", "price_wei": 20_000}
    )
    s1.close()

    # Версію проставлено.
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    conn.close()

    # Той самий (сумісний) файл відкривається без помилки.
    s2 = Store(db)
    assert s2.list_capabilities(capability_type="t")[0]["price_wei"] == 20_000
    s2.close()


def test_memory_store_is_fresh_and_versioned():
    s = Store(":memory:")
    assert s._conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    s.close()
