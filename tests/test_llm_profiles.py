import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.agent.hermes_agent import HermesAgent
from src.api import llm_routes
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}


def test_polza_agent_sends_tools(monkeypatch):
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


def test_llm_status_lists_default_and_local(monkeypatch):
    async def up():
        return True

    monkeypatch.setattr(llm_routes, "probe_local", up)
    body = client.get("/api/llm/status", headers=HEADERS).json()
    assert set(body) == {"default", "local"}
    assert body["local"] == {"available": True, "model": settings.local_model}
