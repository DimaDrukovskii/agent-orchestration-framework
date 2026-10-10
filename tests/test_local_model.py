from fastapi.testclient import TestClient

from src.agent.hermes_agent import HermesAgent
from src.api import llm_routes
from src.services import dialog
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}
PAYLOAD = {"session_id": "local-1", "caller_phone": "+79990000000", "user_message": "привет"}


class FakeAgent:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def run(self, messages, caller_phone):
        self.calls += 1
        return {
            "response": self.reply,
            "messages": messages + [{"role": "assistant", "content": self.reply}],
            "pending_transfer": None,
        }


def _stub_storage(monkeypatch):
    async def fake_get(key):
        return None

    async def fake_set(key, value, ttl=3600):
        return None

    async def fake_persist(**kwargs):
        return True

    monkeypatch.setattr(dialog.cache, "get", fake_get)
    monkeypatch.setattr(dialog.cache, "set", fake_set)
    monkeypatch.setattr(dialog, "_persist_turn", fake_persist)


def test_local_agent_points_at_local_server(monkeypatch):
    monkeypatch.setattr(settings, "local_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "local_model", "hermes3")
    agent = HermesAgent("local")
    assert agent.provider == "local"
    assert agent.model == "hermes3"
    assert str(agent.client.base_url).startswith("http://localhost:11434/v1")


def test_unknown_provider_is_rejected():
    try:
        HermesAgent("nonsense")
    except ValueError:
        return
    raise AssertionError("ожидали ValueError")


def test_agent_for_returns_default_and_cached_local_agent(monkeypatch):
    monkeypatch.setattr(settings, "local_base_url", "http://localhost:11434/v1")
    monkeypatch.delitem(dialog._agents, "local", raising=False)
    assert dialog.agent_for(None) is dialog.hermes
    assert dialog.agent_for("default") is dialog.hermes
    local = dialog.agent_for("local")
    assert local.provider == "local"
    assert dialog.agent_for("local") is local


def test_dialog_routes_to_local_agent_when_requested(monkeypatch):
    _stub_storage(monkeypatch)
    default_agent, local_agent = FakeAgent("из облака"), FakeAgent("из локальной модели")
    monkeypatch.setattr(dialog, "hermes", default_agent)
    monkeypatch.setitem(dialog._agents, "local", local_agent)

    local = client.post("/api/hermes/dialog", json={**PAYLOAD, "model_profile": "local"}, headers=HEADERS)
    default = client.post("/api/hermes/dialog", json=PAYLOAD, headers=HEADERS)

    assert local.json()["agent_response"] == "из локальной модели"
    assert default.json()["agent_response"] == "из облака"
    assert (local_agent.calls, default_agent.calls) == (1, 1)


def test_dialog_rejects_unknown_model_profile():
    response = client.post("/api/hermes/dialog", json={**PAYLOAD, "model_profile": "gpt-9"}, headers=HEADERS)
    assert response.status_code == 422


def test_llm_status_requires_api_key():
    assert client.get("/api/llm/status").status_code == 401


def test_llm_status_reports_local_availability(monkeypatch):
    async def up():
        return True

    async def down():
        return False

    monkeypatch.setattr(settings, "local_model", "hermes3")
    monkeypatch.setattr(llm_routes, "probe_local", up)
    body = client.get("/api/llm/status", headers=HEADERS).json()
    assert body["local"] == {"available": True, "model": "hermes3"}
    assert body["default"]["provider"] == settings.llm_provider

    monkeypatch.setattr(llm_routes, "probe_local", down)
    assert client.get("/api/llm/status", headers=HEADERS).json()["local"]["available"] is False


def test_probe_local_is_false_for_placeholder_url(monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "local_base_url", "http://<IP-устройства>:<порт>/v1")
    assert asyncio.run(llm_routes.probe_local()) is False
