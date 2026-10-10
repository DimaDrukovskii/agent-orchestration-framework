"""
Журнал расходов и метрики: сколько стоит диалог и минута разговора.

Каждый платный вызов (ответ модели, озвучка, распознавание) пишется строкой в таблицу
usage_events (тот же SQLite-файл, что и сессии). Стоимость берётся из ответа провайдера
(`cost_rub`), а не считается по прайсу. Запись идёт в фоновом потоке и никогда не роняет звонок.

Сводка для панели: GET /api/analytics/summary (src/api/analytics_routes.py).
"""

import contextvars
import os
import queue
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from src.config import settings
from src.services.sqlite_store import resolve_path
from src.utils import logger

# Идентификатор разговора для записей: ставится маршрутами (диалог, озвучка, распознавание).
session_var: contextvars.ContextVar[str] = contextvars.ContextVar("analytics_session", default="")

CHARS_PER_SPEECH_SECOND = 14  # средняя скорость русской речи; озвучка в секундах не приходит

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    cost_rub REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_cached INTEGER NOT NULL DEFAULT 0,
    units REAL NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS usage_events_created_idx ON usage_events(created_at);
CREATE INDEX IF NOT EXISTS usage_events_session_idx ON usage_events(session_id);
"""

_INSERT = (
    "INSERT INTO usage_events (session_id, kind, model, cost_rub, tokens_in, tokens_out, tokens_cached, units) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class Analytics:
    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _path(self) -> str:
        path = resolve_path(settings.sqlite_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path(), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return conn

    def record(
        self,
        kind: str,
        cost_rub: Any = 0,
        *,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        tokens_cached: int = 0,
        units: float = 0,
        session_id: Optional[str] = None,
    ) -> None:
        """Поставить запись в очередь. kind: llm | tts | stt; units: символы (tts) или секунды (stt)."""
        try:
            row = (
                session_id if session_id is not None else session_var.get(),
                kind,
                model,
                _number(cost_rub),
                int(_number(tokens_in)),
                int(_number(tokens_out)),
                int(_number(tokens_cached)),
                _number(units),
            )
            self._ensure_writer()
            self._queue.put(row)
        except Exception as e:
            logger.error(f"analytics.record failed: {type(e).__name__}")

    def _ensure_writer(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._writer, name="analytics-writer", daemon=True)
                self._thread.start()

    def _writer(self) -> None:
        while True:
            row = self._queue.get()
            try:
                conn = self._connect()
                try:
                    conn.execute(_INSERT, row)
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.error(f"analytics write failed: {type(e).__name__}")
            finally:
                self._queue.task_done()

    def flush(self) -> None:
        """Дождаться записи всей очереди (тесты и корректное завершение)."""
        self._queue.join()

    def summary(self, days: int = 7) -> Dict[str, Any]:
        days = max(1, min(int(days), 90))
        since = f"-{days} days"
        conn = self._connect()
        try:
            by_kind = {
                row["kind"]: row
                for row in conn.execute(
                    "SELECT kind, SUM(cost_rub) AS cost, COUNT(*) AS n, SUM(units) AS units, "
                    "SUM(tokens_in) AS tin, SUM(tokens_out) AS tout, SUM(tokens_cached) AS tcached "
                    "FROM usage_events WHERE created_at >= datetime('now', ?) GROUP BY kind",
                    (since,),
                )
            }
            sessions = conn.execute(
                "SELECT session_id, MAX(created_at) AS last_at, SUM(cost_rub) AS cost, "
                "SUM(kind = 'llm') AS turns FROM usage_events "
                "WHERE created_at >= datetime('now', ?) AND session_id != '' "
                "GROUP BY session_id ORDER BY last_at DESC",
                (since,),
            ).fetchall()
            by_day = conn.execute(
                "SELECT strftime('%Y-%m-%d', created_at) AS day, COUNT(DISTINCT NULLIF(session_id, '')) AS sessions, "
                "SUM(cost_rub) AS cost FROM usage_events WHERE created_at >= datetime('now', ?) "
                "GROUP BY day ORDER BY day",
                (since,),
            ).fetchall()
            transferred = self._transferred(conn, since)
        finally:
            conn.close()

        def cost_of(kind: str) -> float:
            return _number(by_kind[kind]["cost"]) if kind in by_kind else 0.0

        total = sum(_number(r["cost"]) for r in by_kind.values())
        turns = int(_number(by_kind["llm"]["n"])) if "llm" in by_kind else 0
        tts_chars = _number(by_kind["tts"]["units"]) if "tts" in by_kind else 0.0
        stt_seconds = _number(by_kind["stt"]["units"]) if "stt" in by_kind else 0.0
        speech_minutes = (tts_chars / CHARS_PER_SPEECH_SECOND + stt_seconds) / 60
        session_count = len(sessions)
        llm = by_kind.get("llm")
        tokens_in = int(_number(llm["tin"])) if llm else 0
        return {
            "period_days": days,
            "sessions": session_count,
            "turns": turns,
            "transfers": len(transferred),
            "cost_rub": {
                "total": round(total, 4),
                "llm": round(cost_of("llm"), 4),
                "tts": round(cost_of("tts"), 4),
                "stt": round(cost_of("stt"), 4),
            },
            "per_session_rub": round(total / session_count, 4) if session_count else None,
            "per_turn_rub": round(total / turns, 4) if turns else None,
            "speech_minutes_est": round(speech_minutes, 2),
            "per_minute_rub": round(total / speech_minutes, 4) if speech_minutes else None,
            "cache_hit_share": round(_number(llm["tcached"]) / tokens_in, 3) if llm and tokens_in else None,
            "benchmark": {
                "name": settings.benchmark_name,
                "per_minute_rub": settings.benchmark_rub_per_minute,
            },
            "by_day": [
                {"day": r["day"], "sessions": r["sessions"], "cost_rub": round(_number(r["cost"]), 4)}
                for r in by_day
            ],
            "recent": [
                {
                    "session_id": r["session_id"],
                    "last_at": r["last_at"],
                    "turns": int(_number(r["turns"])),
                    "cost_rub": round(_number(r["cost"]), 4),
                    "transferred": r["session_id"] in transferred,
                }
                for r in sessions[:20]
            ],
        }

    @staticmethod
    def _transferred(conn: sqlite3.Connection, since: str) -> set:
        """Звонки, переведённые на человека (таблица calls есть, только если журнал звонков в SQLite)."""
        try:
            rows = conn.execute(
                "SELECT call_id FROM calls WHERE action = 'transfer' AND updated_at >= datetime('now', ?)",
                (since,),
            ).fetchall()
        except sqlite3.Error:
            return set()
        return {r["call_id"] for r in rows}


analytics = Analytics()
