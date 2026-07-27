"""Локальне сховище (SQLite) — Фаза 2.

Єдине місце, що тримає офчейн-стан, якого немає в блокчейні:
  - agent_stats  : поведінкові лічильники для Reputation Engine
  - events       : журнал подій платіжного потоку (Компонент 4)
  - capabilities : реєстр можливостей / service discovery (Компонент 2)

НЕ шина повідомлень і НЕ ончейн — просто таблиці. Свідомо мінімально
(SQLite з коробки, без ORM). Шлях — з config.STORE_DB_PATH; тести
використовують окремий тимчасовий файл або ":memory:".
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: uvicorn обслуговує sync-роут у тред-пулі.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_stats (
                    address TEXT PRIMARY KEY,
                    completed_payments INTEGER NOT NULL DEFAULT 0,
                    disputes INTEGER NOT NULL DEFAULT 0,
                    refunds INTEGER NOT NULL DEFAULT 0,
                    fraud_flags INTEGER NOT NULL DEFAULT 0,
                    first_seen_ts REAL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    agent TEXT,
                    resource TEXT,
                    event_type TEXT NOT NULL,
                    tx_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS capabilities (
                    id TEXT PRIMARY KEY,
                    capability_type TEXT NOT NULL,
                    provider_url TEXT NOT NULL,
                    price REAL NOT NULL DEFAULT 0,
                    min_reputation_tier INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                );
                """
            )

    # -------------------- agent_stats --------------------

    def get_agent_stats(self, address: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_stats WHERE address = ?", (address.lower(),)
            ).fetchone()
        return dict(row) if row else None

    def upsert_agent_stats(
        self,
        address: str,
        *,
        completed_payments: int = 0,
        disputes: int = 0,
        refunds: int = 0,
        fraud_flags: int = 0,
        first_seen_ts: float | None = None,
    ) -> None:
        """Задає (перезаписує) лічильники агента — для seed у demo/тестах."""
        address = address.lower()
        first_seen_ts = first_seen_ts if first_seen_ts is not None else time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO agent_stats (address, completed_payments, disputes, refunds, fraud_flags, first_seen_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                    completed_payments=excluded.completed_payments,
                    disputes=excluded.disputes,
                    refunds=excluded.refunds,
                    fraud_flags=excluded.fraud_flags,
                    first_seen_ts=excluded.first_seen_ts
                """,
                (address, completed_payments, disputes, refunds, fraud_flags, first_seen_ts),
            )

    def increment_completed_payment(self, address: str) -> None:
        """Атомарно +1 до completed_payments (створює рядок, якщо немає)."""
        address = address.lower()
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO agent_stats (address, completed_payments, first_seen_ts)
                VALUES (?, 1, ?)
                ON CONFLICT(address) DO UPDATE SET completed_payments = completed_payments + 1
                """,
                (address, now),
            )

    # -------------------- events --------------------

    def add_event(self, event_type: str, agent: str | None, resource: str | None, tx_hash: str | None) -> dict:
        ts = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (ts, agent, resource, event_type, tx_hash) VALUES (?, ?, ?, ?, ?)",
                (ts, agent, resource, event_type, tx_hash),
            )
            event_id = cur.lastrowid
        return {"id": event_id, "ts": ts, "agent": agent, "resource": resource, "event_type": event_type, "tx_hash": tx_hash}

    def list_events(self, *, agent: str | None = None, limit: int = 100) -> list[dict]:
        with self._lock:
            if agent:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE agent = ? ORDER BY id DESC LIMIT ?", (agent, limit)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    # -------------------- capabilities --------------------

    def upsert_capability(self, record: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO capabilities (id, capability_type, provider_url, price, min_reputation_tier, active)
                VALUES (:id, :capability_type, :provider_url, :price, :min_reputation_tier, :active)
                ON CONFLICT(id) DO UPDATE SET
                    capability_type=excluded.capability_type,
                    provider_url=excluded.provider_url,
                    price=excluded.price,
                    min_reputation_tier=excluded.min_reputation_tier,
                    active=excluded.active
                """,
                {
                    "id": record["id"],
                    "capability_type": record["capability_type"],
                    "provider_url": record["provider_url"],
                    "price": record.get("price", 0),
                    "min_reputation_tier": record.get("min_reputation_tier", 0),
                    "active": 1 if record.get("active", True) else 0,
                },
            )

    def list_capabilities(self, *, capability_type: str | None = None, active_only: bool = True) -> list[dict]:
        query = "SELECT * FROM capabilities"
        conditions, params = [], []
        if capability_type:
            conditions.append("capability_type = ?")
            params.append(capability_type)
        if active_only:
            conditions.append("active = 1")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["active"] = bool(d["active"])
            result.append(d)
        return result

    def close(self) -> None:
        with self._lock:
            self._conn.close()
