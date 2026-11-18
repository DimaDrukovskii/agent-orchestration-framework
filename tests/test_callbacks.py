import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.services.callbacks import CallbackQueue, CallbackWorker, CallingHours, SimulatedCallbackAdapter
from src.services.sqlite_store import _db

MINSK = ZoneInfo("Europe/Minsk")


def at(hour: int) -> float:
    return datetime(2026, 11, 15, hour, tzinfo=MINSK).timestamp()


def test_duplicate_requests_and_recovery_after_dial(tmp_path):
    path = str(tmp_path / "calls.db")
    queue = CallbackQueue(path)
    job = queue.enqueue("call-1", "request-1", at(10))
    assert queue.enqueue("call-1", "request-1", at(10))["id"] == job["id"]
    with pytest.raises(ValueError):
        queue.enqueue("call-2", "request-1", at(10))

    attempt = queue.claim(at(10), lease_seconds=30)
    adapter = SimulatedCallbackAdapter(queue, "answered")
    assert asyncio.run(adapter.dial(attempt.source_call_id, attempt.id)) == "answered"
    # Emulate a process stopping after the provider answered, before the queue was updated.
    reopened = CallbackQueue(path)
    assert reopened.claim(at(10) + 29, 30) is None
    recovered = reopened.claim(at(10) + 30, 30)
    assert recovered.id == attempt.id
    assert not queue.finish(attempt, "answered", None)

    restarted_adapter = SimulatedCallbackAdapter(reopened, "busy")
    assert asyncio.run(restarted_adapter.dial(recovered.source_call_id, recovered.id)) == "answered"
    assert reopened.finish(recovered, "answered", None)
    assert reopened.get(job["id"])["status"] == "finished"
    with _db(path) as conn:
        assert conn.execute("SELECT count(*) FROM simulated_dials").fetchone()[0] == 1


@pytest.mark.parametrize("outcome", ["busy", "no_answer"])
def test_callback_hours_retry_and_limit(tmp_path, outcome):
    queue = CallbackQueue(str(tmp_path / "calls.db"))
    job = queue.enqueue("call-1", "request-1", at(8))
    now = at(8)
    worker = CallbackWorker(
        queue, SimulatedCallbackAdapter(queue, outcome), CallingHours(9, 20, MINSK),
        max_attempts=2, retry_seconds=60, clock=lambda: now,
    )
    assert not asyncio.run(worker.run_once())
    now = at(9)
    assert asyncio.run(worker.run_once())
    assert queue.get(job["id"])["due_at"] == now + 60
    assert not asyncio.run(worker.run_once())
    now += 60
    assert asyncio.run(worker.run_once())
    result = queue.get(job["id"])
    assert (result["attempts"], result["status"], result["outcome"]) == (2, "finished", outcome)


def test_calling_hours_move_after_close_to_next_day():
    hours = CallingHours(9, 20, MINSK)
    assert hours.next_allowed(at(8)) == at(9)
    assert hours.next_allowed(at(20)) == at(9) + 86400


def test_manager_can_schedule_callback_only_for_existing_call(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.config import settings
    from src.main import app
    from src.services.sqlite_store import SqliteStorage

    path = str(tmp_path / "calls.db")
    monkeypatch.setattr(settings, "sqlite_path", path)
    monkeypatch.setattr(settings, "callback_simulator_enabled", True)
    monkeypatch.setattr(settings, "callback_start_hour", 0)
    monkeypatch.setattr(settings, "callback_end_hour", 24)

    request = {"source_call_id": "inbound", "request_id": "manager-action"}
    headers = {"X-API-Key": settings.agent_api_key}
    client = TestClient(app)
    assert client.post("/api/callbacks", json=request).status_code == 401
    assert client.post("/api/callbacks", json=request, headers=headers).status_code == 404
    asyncio.run(SqliteStorage(path).save_call("inbound", "", "USER: Монтаж", "", "finished"))

    response = client.post("/api/callbacks", json=request, headers=headers)
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "queued"
    assert client.post("/api/callbacks", json=request, headers=headers).json() == job
    assert client.get(f"/api/callbacks/{job['id']}", headers=headers).json() == job


def test_worker_recovers_after_process_exits_between_dial_and_commit(tmp_path):
    import subprocess
    import sys

    path = str(tmp_path / "calls.db")
    queue = CallbackQueue(path)
    now = at(10)
    job = queue.enqueue("call-1", "request-1", now)
    crashed = subprocess.run(
        [
            sys.executable, "-c",
            """
import asyncio
import os
import sys
from src.services.callbacks import CallbackQueue, SimulatedCallbackAdapter

queue = CallbackQueue(sys.argv[1])
attempt = queue.claim(float(sys.argv[2]), lease_seconds=30)
assert asyncio.run(SimulatedCallbackAdapter(queue).dial(attempt.source_call_id, attempt.id)) == "answered"
os._exit(23)
""",
            path, str(now),
        ],
        timeout=10,
        capture_output=True,
        text=True,
    )
    assert crashed.returncode == 23, crashed.stderr
    assert queue.get(job["id"])["status"] == "dialing"
    restarted = CallbackWorker(
        CallbackQueue(path), SimulatedCallbackAdapter(queue, "busy"), CallingHours(9, 20, MINSK),
        max_attempts=2, retry_seconds=60, clock=lambda: now + 30,
    )
    assert asyncio.run(restarted.run_once())
    recovered = queue.get(job["id"])
    assert (recovered["status"], recovered["outcome"], recovered["attempts"]) == ("finished", "answered", 1)
    with _db(path) as conn:
        assert conn.execute("SELECT count(*) FROM simulated_dials").fetchone()[0] == 1
