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

import fcntl
import sqlite3
import threading
import time
from pathlib import Path


# Версія схеми store. Піднімай при БУДЬ-ЯКІй зміні структури таблиць, щоб старий
# файл БД не відкривався мовчки й не падав пізніше на першому несумісному записі.
#   1 — початкова схема Фази 2 (capabilities.price REAL).
#   2 — money-type міграція: capabilities.price -> price_wei INTEGER (09b3a4c).
#   3 — підписана реєстрація (F4 #A): capabilities += owner_address, pay_to,
#       signature, created_at; вибір провайдера за серверним created_at/rowid.
CURRENT_SCHEMA_VERSION = 3


class StoreLockError(RuntimeError):
    """Файл БД уже відкрито іншим процесом/інстансом Store.

    Кидається на старті, коли ексклюзивний файловий лок узяти не вдалося —
    ознака, що запущено кілька воркерів/процесів на тому самому файлі БД.
    Див. інваріант «один процес» нижче.
    """


class StoreSchemaError(RuntimeError):
    """Наявний файл БД має НЕСУМІСНУ (стару) схему.

    Store не мігрує автоматично (свідомо: локальний кеш — похідний стан, істина
    по коштах живе on-chain). Замість тихого падіння пізніше на першому записі —
    гучна, дієва помилка на старті. Див. README → «Store schema / breaking change».
    """


class Store:
    # ІНВАРІАНТ КОНКУРЕНТНОСТІ (свідома межа, не недогляд):
    # self._lock (threading.Lock) серіалізує записи ТІЛЬКИ в межах ОДНОГО
    # процесу. Кілька процесів (напр. `uvicorn --workers N` або кілька
    # інстансів) мали б кожен свій Lock і свою серіалізацію — атомарність
    # лічильників (increment_completed_payment тощо) втратилася б, бо
    # read-modify-write у різних процесах не координується Python-локом.
    # Тому система розрахована на ОДИН процес, і це тут форсується
    # ексклюзивним файловим локом (fcntl.flock) на сайдкар-файлі `<db>.lock`:
    # другий процес/інстанс на тому самому файлі БД падає зі StoreLockError
    # на старті — гучно, а не тихо ламаючи інваріанти.
    # Для горизонтального масштабування (кілька процесів) цього НЕ досить —
    # знадобляться транзакції рівня БД (напр. SELECT ... FOR UPDATE у
    # Postgres) або міжпроцесні блокування. Це свідомо поза скоупом PoC:
    # межу зафіксовано, БД не мігровано.
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._flock_fd = None
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._acquire_single_process_lock(db_path)
        # check_same_thread=False: uvicorn обслуговує sync-роут у тред-пулі.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._guard_schema_version()
        self._init_schema()

    def _guard_schema_version(self) -> None:
        """Гучно відхиляє наявний файл БД зі старою/несумісною схемою.

        Використовує `PRAGMA user_version`. Якщо наші таблиці вже існують, а
        версія не збігається з CURRENT_SCHEMA_VERSION — це старий файл (напр.
        до money-type міграції), який мовчки відкрився б і впав пізніше на
        першому записі (`no column named price_wei`). Замість цього — чиста
        відмова на старті. Свіжій БД проставляємо поточну версію."""
        with self._lock, self._conn:
            existing_version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='capabilities'"
            ).fetchone()
            tables_pre_exist = row is not None
            if tables_pre_exist and existing_version != CURRENT_SCHEMA_VERSION:
                raise StoreSchemaError(
                    f"Файл БД '{self.db_path}' має несумісну схему "
                    f"(version={existing_version}, потрібно {CURRENT_SCHEMA_VERSION}). "
                    "Store не мігрує автоматично — це локальний похідний кеш "
                    "(agent_stats/events/capabilities), істина по коштах on-chain. "
                    "Видали файл (і сайдкар .lock) або вкажи новий STORE_DB_PATH; "
                    "стан відновиться з ланцюга. Деталі: README → Store schema."
                )
            # Свіжа або вже поточна БД — фіксуємо версію (idempotent).
            self._conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    def _acquire_single_process_lock(self, db_path: str) -> None:
        """Бере ексклюзивний неблокуючий файловий лок на `<db>.lock`.

        Якщо лок уже тримає інший процес/інстанс — flock поверне EAGAIN,
        і ми падаємо зі StoreLockError замість тихо запуститися другим
        воркером і зламати in-process-серіалізацію. `:memory:` сюди не
        потрапляє (окрема БД на з'єднання).
        """
        lock_path = f"{db_path}.lock"
        fd = open(lock_path, "w")  # noqa: SIM115 — тримаємо fd весь час життя Store
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fd.close()
            raise StoreLockError(
                f"Файл БД '{db_path}' уже відкрито іншим процесом/інстансом Store. "
                "Система розрахована на ОДИН процес — не запускайте кілька воркерів "
                "(`uvicorn --workers N`) чи інстансів на тому самому STORE_DB_PATH. "
                "Для масштабування потрібні транзакції рівня БД, а не Python-Lock."
            ) from exc
        self._flock_fd = fd

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
                    pay_to TEXT NOT NULL DEFAULT '',
                    owner_address TEXT NOT NULL DEFAULT '',
                    price_wei INTEGER NOT NULL DEFAULT 0,
                    min_reputation_tier INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    signature TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0
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

    def list_events(
        self, *, agent: str | None = None, event_type: str | None = None, limit: int = 100
    ) -> list[dict]:
        # Параметризовані умови (без конкатенації значень у SQL).
        conditions, params = [], []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        query = "SELECT * FROM events"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # -------------------- capabilities --------------------

    def upsert_capability(self, record: dict) -> None:
        with self._lock, self._conn:
            # created_at ставимо ЛИШЕ на першому вставленні; при оновленні запису
            # зберігаємо оригінальний час реєстрації (порядок вибору стабільний).
            self._conn.execute(
                """
                INSERT INTO capabilities
                    (id, capability_type, provider_url, pay_to, owner_address,
                     price_wei, min_reputation_tier, active, signature, created_at)
                VALUES
                    (:id, :capability_type, :provider_url, :pay_to, :owner_address,
                     :price_wei, :min_reputation_tier, :active, :signature, :created_at)
                ON CONFLICT(id) DO UPDATE SET
                    capability_type=excluded.capability_type,
                    provider_url=excluded.provider_url,
                    pay_to=excluded.pay_to,
                    owner_address=excluded.owner_address,
                    price_wei=excluded.price_wei,
                    min_reputation_tier=excluded.min_reputation_tier,
                    active=excluded.active,
                    signature=excluded.signature
                """,
                {
                    "id": record["id"],
                    "capability_type": record["capability_type"],
                    "provider_url": record["provider_url"],
                    "pay_to": record.get("pay_to", ""),
                    "owner_address": record.get("owner_address", ""),
                    "price_wei": int(record.get("price_wei", 0) or 0),
                    "min_reputation_tier": record.get("min_reputation_tier", 0),
                    "active": 1 if record.get("active", True) else 0,
                    "signature": record.get("signature", ""),
                    "created_at": time.time(),
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
        # Порядок — за СЕРВЕРНИМИ значеннями (created_at, потім rowid як tie-break),
        # НЕ за жодним полем, що його контролює реєстрант (напр. id/адреса, яку
        # можна грайндити вибором vanity-адреси). Див. registry_auth / F4 #A.
        query += " ORDER BY created_at ASC, rowid ASC"
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
        if self._flock_fd is not None:
            # Звільняємо файловий лок — легітимний рестарт того ж процесу
            # (напр. у тестах) зможе відкрити БД знову.
            fcntl.flock(self._flock_fd.fileno(), fcntl.LOCK_UN)
            self._flock_fd.close()
            self._flock_fd = None
