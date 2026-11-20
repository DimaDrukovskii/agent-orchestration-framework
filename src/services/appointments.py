"""Demo availability and work requests, shared by the simulator and MCP server."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


class AppointmentBook:
    def __init__(self, database: str, profile: str):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rules = json.loads(Path(profile).read_text(encoding="utf8"))
        self.zone = ZoneInfo(self.rules["timezone"])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    id TEXT PRIMARY KEY,
                    slot TEXT NOT NULL UNIQUE,
                    need TEXT NOT NULL,
                    request_id TEXT UNIQUE
                )
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(appointments)")}
            if "request_id" not in columns:
                conn.execute("ALTER TABLE appointments ADD COLUMN request_id TEXT")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS appointments_request_id_idx ON appointments(request_id)")

    def _offered_slots(self) -> list[str]:
        today = datetime.now(self.zone).date()
        dates = [today + timedelta(days=offset) for offset in range(1, self.rules["days_ahead"] + 1)]
        return [f"{date}T{hour}" for date in dates if date.weekday() < 5 for hour in self.rules["times"]]

    def available(self) -> list[str]:
        with closing(sqlite3.connect(self.path)) as conn:
            occupied = {row[0] for row in conn.execute("SELECT slot FROM appointments")}
        return [slot for slot in self._offered_slots() if slot not in occupied]

    def create(self, slot: str, need: str, request_id: str | None = None) -> dict:
        need = need.strip()
        if not need or len(need) > 500:
            raise ValueError("Опишите заявку в пределах 500 символов")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if request_id:
                previous = conn.execute("SELECT id, slot, need FROM appointments WHERE request_id = ?", (request_id,)).fetchone()
                if previous:
                    if previous[1:] != (slot, need):
                        raise ValueError("Эта заявка уже создана с другими данными")
                    return {"id": previous[0], "slot": slot, "need": need}
            if conn.execute("SELECT 1 FROM appointments WHERE slot = ?", (slot,)).fetchone():
                raise ValueError("Это окно уже занято. Запросите свободные окна")
            if slot not in self._offered_slots():
                raise ValueError("Это окно недоступно. Выберите время из списка свободных окон")
            appointment_id = uuid4().hex
            conn.execute("INSERT INTO appointments (id, slot, need, request_id) VALUES (?, ?, ?, ?)",
                         (appointment_id, slot, need, request_id))
            return {"id": appointment_id, "slot": slot, "need": need}
