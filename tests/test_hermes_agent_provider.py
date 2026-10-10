import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.agent.hermes_agent import HermesAgent
from src.api import hermes_routes, llm_routes
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}


def _agent(monkeypatch):
    monkeypatch.setattr(settings, "hermes_agent_base_url", "http://hermes:8642/v1")
    monkeypatch.setattr(settings, "hermes_agent_api_key", "local-hermes-key-2026")
    return HermesAgent("hermes_agent")


def test_hermes_agent_provider_points_at_hermes_container(monkeypatch):
    agent = _agent(monkeypatch)
    assert agent.provider == "hermes_agent"
    assert agent.model == settings.hermes_agent_model
    assert str(agent.client.base_url).startswith("http://hermes:8642/v1")
    assert agent.client.api_key == "local-hermes-key-2026"


def test_hermes_agent_does_not_send_client_tools(monkeypatch):
    agent = _agent(monkeypatch)
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="Здравствуйте!", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    result = asyncio.run(agent.run([{"role": "user", "content": "привет"}], "+79990000000"))

    assert "tools" not in captured
    assert captured["messages"][0]["role"] == "system"
    assert result["response"] == "Здравствуйте!"
    assert result["pending_transfer"] is None


def test_polza_agent_still_sends_tools(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    agent = HermesAgent("polza")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    asyncio.run(agent.run([{"role": "user", "content": "привет"}], "+79990000000"))
    assert captured["tools"]


def test_agent_for_hermes_agent_profile(monkeypatch):
    monkeypatch.setattr(settings, "hermes_agent_base_url", "http://hermes:8642/v1")
    monkeypatch.delitem(hermes_routes._agents, "hermes_agent", raising=False)
    agent = hermes_routes.agent_for("hermes_agent")
    assert agent.provider == "hermes_agent"
    assert hermes_routes.agent_for("hermes_agent") is agent


def test_dialog_accepts_hermes_agent_profile(monkeypatch):
    class Fake:
        async def run(self, messages, caller_phone):
            return {"response": "через Hermes", "messages": messages, "pending_transfer": None}

    async def noop(*args, **kwargs):
        return None

    async def persisted(**kwargs):
        return True

    monkeypatch.setattr(hermes_routes.cache, "get", noop)
    monkeypatch.setattr(hermes_routes.cache, "set", noop)
    monkeypatch.setattr(hermes_routes, "_persist_turn", persisted)
    monkeypatch.setitem(hermes_routes._agents, "hermes_agent", Fake())
    payload = {"session_id": "h1", "caller_phone": "+79990000000", "user_message": "привет", "model_profile": "hermes_agent"}
    response = client.post("/api/hermes/dialog", json=payload, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["agent_response"] == "через Hermes"


def test_llm_status_includes_hermes_agent(monkeypatch):
    async def up():
        return True

    async def hermes_up():
        return False

    monkeypatch.setattr(llm_routes, "probe_local", up)
    monkeypatch.setattr(llm_routes, "probe_hermes_agent", hermes_up)
    body = client.get("/api/llm/status", headers=HEADERS).json()
    assert body["hermes_agent"] == {"available": False, "model": settings.hermes_agent_model}
