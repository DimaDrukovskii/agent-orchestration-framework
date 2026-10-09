"""
SQLite-бэкенд для сессий (замена Redis) и звонков (замена Supabase).

Один файл БД (settings.sqlite_path), только stdlib — ни Docker, ни внешних
сервисов. Блокирующие вызовы sqlite3 уходят в поток (asyncio.to_thread), чтобы
не держать event loop. Схема `calls` повторяет docs/DATABASE_SCHEMA.md.
"""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional

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
"""


def resolve_path(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


@contextmanager
def _db(path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path, timeout=10)
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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
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
