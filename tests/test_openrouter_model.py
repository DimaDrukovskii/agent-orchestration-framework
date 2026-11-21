from fastapi.testclient import TestClient

from src.agent.hermes_agent import HermesAgent
from src.services import dialog
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}
PAYLOAD = {"session_id": "cloud-1", "caller_phone": "+79990000000", "user_message": "привет"}


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


def test_openrouter_agent_uses_cloud_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-cloud-key")
    monkeypatch.setattr(settings, "openrouter_model", "openai/gpt-4.1-mini")
    agent = HermesAgent("openrouter")
    assert agent.provider == "openrouter"
    assert agent.model == "openai/gpt-4.1-mini"
    assert str(agent.client.base_url).startswith("https://openrouter.ai/api/v1")


def test_unknown_provider_is_rejected():
    try:
        HermesAgent("nonsense")
    except ValueError:
        return
    raise AssertionError("ожидали ValueError")


def test_agent_for_returns_default_and_cached_cloud_agent(monkeypatch):
    monkeypatch.delitem(dialog._agents, "openrouter", raising=False)
    assert dialog.agent_for(None) is dialog.hermes
    assert dialog.agent_for("default") is dialog.hermes
    cloud = dialog.agent_for("openrouter")
    assert cloud.provider == "openrouter"
    assert dialog.agent_for("openrouter") is cloud


def test_dialog_rejects_removed_local_profile():
    response = client.post("/api/hermes/dialog", json={**PAYLOAD, "model_profile": "local"}, headers=HEADERS)
    assert response.status_code == 422


def test_dialog_rejects_unknown_model_profile():
    response = client.post("/api/hermes/dialog", json={**PAYLOAD, "model_profile": "gpt-9"}, headers=HEADERS)
    assert response.status_code == 422


def test_llm_status_requires_api_key():
    assert client.get("/api/llm/status").status_code == 401


def test_llm_status_reports_cloud_configuration(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    body = client.get("/api/llm/status", headers=HEADERS).json()
    assert body == {"default": {"provider": "openrouter", "model": settings.openrouter_model, "configured": False}}
