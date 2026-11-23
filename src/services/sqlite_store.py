"""
SQLite-бэкенд для сессий (замена Redis) и звонков (замена Supabase).

Один файл БД (settings.sqlite_path), только stdlib — ни Docker, ни внешних
сервисов. Блокирующие вызовы sqlite3 уходят в поток (asyncio.to_thread), чтобы
не держать event loop. Схемы `calls` и `handoff_cards` повторяют docs/website_side_bar/DATABASE_SCHEMA.md.
"""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional

SQLITE_TIMEOUT = 10

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT UNIQUE NOT NULL,
    caller_phone TEXT NOT NULL,
    transcript TEXT NOT NULL,
    agent_response TEXT NOT NULL,
    action TEXT NOT NULL,
    department TEXT,
    confidence REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS calls_caller_phone_idx ON calls(caller_phone);
CREATE INDEX IF NOT EXISTS calls_created_at_idx ON calls(created_at DESC);
CREATE TABLE IF NOT EXISTS handoff_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    name TEXT,
    need TEXT NOT NULL,
    budget TEXT,
    urgency TEXT,
    contact_phone TEXT NOT NULL,
    comment TEXT
);
CREATE INDEX IF NOT EXISTS handoff_cards_call_id_idx ON handoff_cards(call_id);
CREATE TABLE IF NOT EXISTS handoff_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    card_json TEXT NOT NULL,
    legacy_row_id INTEGER UNIQUE,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS delivery_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE NOT NULL,
    call_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL DEFAULT 0,
    lease_until REAL NOT NULL DEFAULT 0,
    owner TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    delivered_at REAL
);
CREATE INDEX IF NOT EXISTS delivery_outbox_due_idx ON delivery_outbox(status, available_at);
CREATE TABLE IF NOT EXISTS service_requests (
    call_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'new',
    assigned_to TEXT,
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS service_request_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    status TEXT NOT NULL,
    manager TEXT,
    result TEXT,
    created_at REAL NOT NULL
);
"""

HANDOFF_COLUMNS = ("name", "need", "budget", "urgency", "contact_phone", "comment")


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


@contextmanager
def _db(path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: str) -> str:
    """Создать файл и таблицы, если их нет. Возвращает абсолютный путь."""
    full_path = resolve_path(path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with _db(full_path) as conn:
        # Changing the journal mode can return SQLITE_BUSY before the
        # connection's busy handler runs, when another worker opens a new DB.
        deadline = time.monotonic() + SQLITE_TIMEOUT
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL").fetchone()
                break
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)
        conn.executescript(_SCHEMA)
        has_index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='handoff_cards_one_per_call_idx'"
        ).fetchone()
        if not has_index:
            # Preserve legacy duplicates before applying the one-card-per-call index.
            duplicates = conn.execute(
                "SELECT * FROM handoff_cards WHERE call_id IN "
                "(SELECT call_id FROM handoff_cards GROUP BY call_id HAVING COUNT(*) > 1)"
            ).fetchall()
            for row in duplicates:
                conn.execute(
                    "INSERT OR IGNORE INTO handoff_revisions (call_id, card_json, legacy_row_id) VALUES (?, ?, ?)",
                    (row['call_id'], json.dumps(dict(row), ensure_ascii=False), row['id']),
                )
            conn.execute("DELETE FROM handoff_cards WHERE id NOT IN (SELECT MAX(id) FROM handoff_cards GROUP BY call_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS handoff_cards_one_per_call_idx ON handoff_cards(call_id)")
    return full_path


class SqliteCache:
    def __init__(self, path: str):
        self.path = init_db(path)

    async def get(self, key: str) -> Optional[dict]:
        def op():
            with _db(self.path) as conn:
                row = conn.execute(
                    "SELECT value FROM cache WHERE key = ? AND expires_at > ?",
                    (key, time.time()),
                ).fetchone()
                return json.loads(row["value"]) if row else None

        return await asyncio.to_thread(op)

    async def set(self, key: str, value: dict, ttl: int = 3600) -> None:
        def op():
            now = time.time()
            with _db(self.path) as conn:
                conn.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
                conn.execute(
                    "INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "expires_at = excluded.expires_at",
                    (key, json.dumps(value), now + ttl),
                )

        await asyncio.to_thread(op)

    async def delete(self, key: str) -> None:
        def op():
            with _db(self.path) as conn:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))

        await asyncio.to_thread(op)

    async def increment(self, key: str, amount: int = 1) -> int:
        def op():
            with _db(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
                new_value = (int(row["value"]) if row else 0) + amount
                conn.execute(
                    "INSERT INTO cache (key, value, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(new_value), time.time() + 3600),
                )
                return new_value

        return await asyncio.to_thread(op)


class SqliteStorage:
    def __init__(self, path: str):
        self.path = init_db(path)

    def _fetch_call(self, conn: sqlite3.Connection, call_id: str) -> dict:
        row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        return dict(row) if row else {}

    async def save_call(
        self, call_id: str, caller_phone: str, transcript: str, agent_response: str, action: str
    ) -> dict:
        def op():
            with _db(self.path) as conn:
                conn.execute(
                    "INSERT INTO calls (call_id, caller_phone, transcript, agent_response, action) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (call_id, caller_phone, transcript, agent_response, action),
                )
                return self._fetch_call(conn, call_id)

        return await asyncio.to_thread(op)

    async def upsert_call(
        self, call_id: str, caller_phone: str, transcript: str, agent_response: str, action: str
    ) -> dict:
        def op():
            with _db(self.path) as conn:
                conn.execute(
                    "INSERT INTO calls (call_id, caller_phone, transcript, agent_response, action) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(call_id) DO UPDATE SET caller_phone = excluded.caller_phone, "
                    "transcript = excluded.transcript, agent_response = excluded.agent_response, "
                    "action = excluded.action, updated_at = datetime('now')",
                    (call_id, caller_phone, transcript, agent_response, action),
                )
                return self._fetch_call(conn, call_id)

        return await asyncio.to_thread(op)

    async def get_call(self, call_id: str) -> Optional[dict]:
        def op():
            with _db(self.path) as conn:
                return self._fetch_call(conn, call_id) or None

        return await asyncio.to_thread(op)

    async def get_caller_history(self, caller_phone: str, limit: int = 10) -> List[dict]:
        def op():
            with _db(self.path) as conn:
                rows = conn.execute(
                    "SELECT * FROM calls WHERE caller_phone = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (caller_phone, limit),
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(op)

    async def save_handoff_card(self, call_id: str, card: dict) -> dict:
        """Patch supplied nonblank fields; retain earlier facts and immutable revisions."""
        def op():
            with _db(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                return self._save_card(conn, call_id, card)

        return await asyncio.to_thread(op)

    def _save_card(self, conn: sqlite3.Connection, call_id: str, card: dict) -> dict:
        previous = conn.execute("SELECT * FROM handoff_cards WHERE call_id = ?", (call_id,)).fetchone()
        merged = {c: previous[c] if previous else None for c in HANDOFF_COLUMNS}
        merged.update({c: str(card[c]).strip() for c in HANDOFF_COLUMNS
                       if card.get(c) is not None and str(card[c]).strip()})
        conn.execute(
            "INSERT INTO handoff_cards (call_id, name, need, budget, urgency, contact_phone, comment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(call_id) DO UPDATE SET "
            "name=excluded.name, need=excluded.need, budget=excluded.budget, "
            "urgency=excluded.urgency, contact_phone=excluded.contact_phone, comment=excluded.comment",
            (call_id, *(merged[c] for c in HANDOFF_COLUMNS)),
        )
        if previous is None or any(previous[c] != merged[c] for c in HANDOFF_COLUMNS):
            conn.execute(
                "INSERT INTO handoff_revisions (call_id, card_json) VALUES (?, ?)",
                (call_id, json.dumps(merged, ensure_ascii=False)),
            )
        return dict(conn.execute("SELECT * FROM handoff_cards WHERE call_id = ?", (call_id,)).fetchone())

    async def save_turn(self, call_id: str, caller_phone: str, transcript: str,
                        agent_response: str, action: str, card: Optional[dict] = None,
                        notification: Optional[dict] = None) -> dict:
        """Commit call, card, audit and manager notification in one transaction."""
        def op():
            with _db(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO calls (call_id, caller_phone, transcript, agent_response, action) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(call_id) DO UPDATE SET "
                    "caller_phone=excluded.caller_phone, transcript=excluded.transcript, "
                    "agent_response=excluded.agent_response, action=excluded.action, updated_at=datetime('now')",
                    (call_id, caller_phone, transcript, agent_response, action),
                )
                changed_card = False
                if card:
                    previous = conn.execute('SELECT * FROM handoff_cards WHERE call_id=?', (call_id,)).fetchone()
                    saved = self._save_card(conn, call_id, card)
                    changed_card = previous is None or any(previous[c] != saved[c] for c in HANDOFF_COLUMNS)
                if notification:
                    event_key = notification['event_key']
                    if card:
                        revision = conn.execute(
                            'SELECT MAX(id) FROM handoff_revisions WHERE call_id=?', (call_id,),
                        ).fetchone()[0]
                        event_key += f':revision:{revision}'
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO service_requests (call_id, created_at, updated_at) VALUES (?, ?, ?)",
                        (call_id, time.time(), time.time()),
                    )
                    if cursor.rowcount:
                        conn.execute(
                            "INSERT INTO service_request_events (call_id, status, created_at) VALUES (?, 'new', ?)",
                            (call_id, time.time()),
                        )
                    elif changed_card:
                        reopened = conn.execute(
                            "UPDATE service_requests SET status='new', assigned_to=NULL, result=NULL, updated_at=? "
                            "WHERE call_id=? AND status='completed'", (time.time(), call_id),
                        )
                        if reopened.rowcount:
                            conn.execute(
                                "INSERT INTO service_request_events (call_id,status,result,created_at) "
                                "VALUES (?, 'new', 'Клиент уточнил завершённую заявку', ?)", (call_id, time.time()),
                            )
                    conn.execute(
                        "INSERT OR IGNORE INTO delivery_outbox "
                        "(event_key, call_id, chat_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                        (event_key, call_id, notification['chat_id'], notification['message'], time.time()),
                    )
                return self._fetch_call(conn, call_id)
        return await asyncio.to_thread(op)

    async def get_handoff_cards(self, call_id: str) -> List[dict]:
        def op():
            with _db(self.path) as conn:
                rows = conn.execute(
                    "SELECT * FROM handoff_cards WHERE call_id = ? ORDER BY id", (call_id,)
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(op)
