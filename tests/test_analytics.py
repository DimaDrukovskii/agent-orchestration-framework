import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.agent.hermes_agent import log_usage
from src.config import settings
from src.main import app
from src.services import analytics as analytics_module
from src.services.analytics import Analytics, session_var
from src.services.sqlite_store import resolve_path

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "analytics.db"))
    return Analytics()


def test_summary_counts_cost_by_kind_and_session(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    a.record("llm", 0.05, tokens_in=1000, tokens_out=50, tokens_cached=800, session_id="s1")
    a.record("llm", 0.03, tokens_in=1000, tokens_out=40, tokens_cached=900, session_id="s1")
    a.record("tts", 0.40, units=140, session_id="s1")  # 140 символов = 10 секунд речи
    a.record("stt", 0.10, units=20, session_id="s1")  # 20 секунд записи
    a.record("llm", 0.02, session_id="s2")
    a.flush()

    s = a.summary(7)

    assert s["sessions"] == 2
    assert s["turns"] == 3
    assert s["cost_rub"] == {"total": 0.6, "llm": 0.1, "tts": 0.4, "stt": 0.1}
    assert s["per_session_rub"] == 0.3
    assert s["speech_minutes_est"] == 0.5
    assert s["per_minute_rub"] == 1.2
    assert s["cache_hit_share"] == 0.85
    assert {r["session_id"] for r in s["recent"]} == {"s1", "s2"}
    assert s["benchmark"]["per_minute_rub"] == settings.benchmark_rub_per_minute


def test_summary_of_empty_journal_has_no_division_by_zero(monkeypatch, tmp_path):
    s = _fresh(monkeypatch, tmp_path).summary(7)
    assert s["sessions"] == 0 and s["per_minute_rub"] is None and s["per_session_rub"] is None


def test_transfer_is_marked_from_calls_table(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    a.record("llm", 0.01, session_id="s1")
    a.flush()
    conn = sqlite3.connect(resolve_path(settings.sqlite_path))
    conn.executescript(
        "CREATE TABLE calls (call_id TEXT, action TEXT, updated_at TEXT);"
        "INSERT INTO calls VALUES ('s1', 'transfer', datetime('now'));"
    )
    conn.commit()
    conn.close()

    s = a.summary(7)

    assert s["transfers"] == 1
    assert s["recent"][0]["transferred"] is True


def test_record_uses_session_context_and_never_raises(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    session_var.set("ctx-session")
    a.record("llm", "не число", tokens_in=None)  # мусор на входе не роняет звонок
    a.flush()
    assert a.summary(7)["recent"][0]["session_id"] == "ctx-session"


def test_log_usage_records_provider_cost(monkeypatch, tmp_path):
    a = _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(analytics_module, "analytics", a)
    monkeypatch.setattr("src.agent.hermes_agent.analytics", a)
    session_var.set("llm-session")
    usage = SimpleNamespace(
        prompt_tokens=1200, completion_tokens=30, cost_rub=0.0123,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
    )

    log_usage(SimpleNamespace(usage=usage), "anthropic/claude-haiku-5.5")
    a.flush()

    s = a.summary(7)
    assert s["cost_rub"]["llm"] == 0.0123
    assert s["cache_hit_share"] == round(1000 / 1200, 3)


def test_summary_endpoint_requires_key_and_returns_shape():
    assert client.get("/api/analytics/summary").status_code == 401
    response = client.get("/api/analytics/summary?days=3", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["period_days"] == 3
    assert {"sessions", "cost_rub", "per_minute_rub", "benchmark", "by_day", "recent"} <= set(body)
