"""Persistent callback queue and the contract for a telephony adapter."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.services.sqlite_store import _db, init_db
from src.utils import logger

Outcome = Literal["answered", "busy", "no_answer"]
OUTCOMES = ("answered", "busy", "no_answer")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS callbacks (
    id TEXT PRIMARY KEY,
    source_call_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at REAL NOT NULL,
    lease_until REAL,
    lease_token TEXT,
    outcome TEXT
);
CREATE INDEX IF NOT EXISTS callbacks_due ON callbacks(status, due_at);
CREATE TABLE IF NOT EXISTS simulated_dials (
    attempt_id TEXT PRIMARY KEY,
    source_call_id TEXT NOT NULL,
    outcome TEXT NOT NULL
);
"""


class CallbackAdapter(Protocol):
    async def dial(self, source_call_id: str, attempt_id: str) -> Outcome:
        """Return the existing outcome on re-delivery of the same attempt ID."""
        ...


@dataclass(frozen=True)
class CallingHours:
    start: int
    end: int
    zone: ZoneInfo

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end <= 24:
            raise ValueError("Задайте окно обзвона в пределах одних суток")

    def next_allowed(self, timestamp: float) -> float:
        local = datetime.fromtimestamp(timestamp, self.zone)
        if self.start <= local.hour < self.end:
            return timestamp
        if local.hour >= self.end:
            local += timedelta(days=1)
        return local.replace(hour=self.start, minute=0, second=0, microsecond=0).timestamp()


@dataclass(frozen=True)
class CallbackAttempt:
    job_id: str
    source_call_id: str
    number: int
    lease_token: str

    @property
    def id(self) -> str:
        return f"{self.job_id}:{self.number}"


class CallbackQueue:
    def __init__(self, db_path: str):
        self.path = init_db(db_path)
        with _db(self.path) as conn:
            conn.executescript(_SCHEMA)

    def enqueue(self, source_call_id: str, request_id: str, due_at: float) -> dict:
        with _db(self.path) as conn:
            conn.execute(
                """INSERT INTO callbacks(id, source_call_id, request_id, due_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(request_id) DO NOTHING""",
                (uuid4().hex, source_call_id, request_id, due_at),
            )
            row = conn.execute("SELECT * FROM callbacks WHERE request_id = ?", (request_id,)).fetchone()
            if row["source_call_id"] != source_call_id:
                raise ValueError("Ключ запроса уже использован для другого звонка")
            return dict(row)

    def get(self, job_id: str) -> dict | None:
        with _db(self.path) as conn:
            row = conn.execute("SELECT * FROM callbacks WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def claim(self, now: float, lease_seconds: float) -> CallbackAttempt | None:
        with _db(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM callbacks
                   WHERE (status = 'queued' AND due_at <= ?)
                      OR (status = 'dialing' AND lease_until <= ?)
                   ORDER BY due_at, id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            token = uuid4().hex
            # Recovery uses the same attempt ID. The adapter may already have placed the call.
            attempts = row["attempts"] + (row["status"] == "queued")
            conn.execute(
                """UPDATE callbacks SET status = 'dialing', attempts = ?, lease_until = ?,
                   lease_token = ? WHERE id = ?""",
                (attempts, now + lease_seconds, token, row["id"]),
            )
            return CallbackAttempt(row["id"], row["source_call_id"], attempts, token)

    def finish(
        self, attempt: CallbackAttempt, outcome: Outcome, retry_at: float | None,
    ) -> bool:
        if outcome not in OUTCOMES:
            raise ValueError(f"Неизвестный исход звонка: {outcome}")
        status = "queued" if retry_at is not None else "finished"
        with _db(self.path) as conn:
            result = conn.execute(
                """UPDATE callbacks SET status = ?, outcome = ?, due_at = coalesce(?, due_at),
                   lease_token = NULL, lease_until = NULL
                   WHERE id = ? AND status = 'dialing' AND lease_token = ?""",
                (status, outcome, retry_at, attempt.job_id, attempt.lease_token),
            )
            return result.rowcount == 1


class SimulatedCallbackAdapter:
    def __init__(self, queue: CallbackQueue, outcome: Outcome = "answered"):
        self.path = queue.path
        self.outcome = outcome

    async def dial(self, source_call_id: str, attempt_id: str) -> Outcome:
        return await asyncio.to_thread(self._record, source_call_id, attempt_id)

    def _record(self, source_call_id: str, attempt_id: str) -> Outcome:
        with _db(self.path) as conn:
            conn.execute(
                """INSERT INTO simulated_dials VALUES (?, ?, ?)
                   ON CONFLICT(attempt_id) DO NOTHING""",
                (attempt_id, source_call_id, self.outcome),
            )
            return conn.execute(
                "SELECT outcome FROM simulated_dials WHERE attempt_id = ?", (attempt_id,),
            ).fetchone()["outcome"]


class CallbackWorker:
    def __init__(
        self, queue: CallbackQueue, adapter: CallbackAdapter, hours: CallingHours, *,
        max_attempts: int, retry_seconds: float, dial_timeout: float = 30,
        clock: Callable[[], float] = time.time,
    ):
        if max_attempts < 1 or retry_seconds <= 0 or dial_timeout <= 0:
            raise ValueError("Укажите положительные лимиты обзвона")
        self.queue = queue
        self.adapter = adapter
        self.hours = hours
        self.max_attempts = max_attempts
        self.retry_seconds = retry_seconds
        self.dial_timeout = dial_timeout
        self.clock = clock

    async def run_once(self) -> bool:
        now = self.clock()
        if self.hours.next_allowed(now) != now:
            return False
        attempt = await asyncio.to_thread(self.queue.claim, now, self.dial_timeout + 5)
        if attempt is None:
            return False
        try:
            outcome = await asyncio.wait_for(
                self.adapter.dial(attempt.source_call_id, attempt.id), timeout=self.dial_timeout,
            )
        except Exception as exc:
            # Delivery is uncertain. Keep the lease and recover the same attempt after it expires.
            logger.warning(f"Callback attempt {attempt.id}: {type(exc).__name__}")
            return True
        retry_at = None
        if outcome != "answered" and attempt.number < self.max_attempts:
            retry_at = self.hours.next_allowed(self.clock() + self.retry_seconds)
        await asyncio.to_thread(self.queue.finish, attempt, outcome, retry_at)
        return True
