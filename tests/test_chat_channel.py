import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.agent import tools
from src.agent.hermes_agent import HermesAgent
from src.api import hermes_routes
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}


def test_send_to_chat_tool_is_exposed_in_both_schemas():
    assert "send_to_chat" in [t["name"] for t in tools.TOOLS_SCHEMA]
    assert "send_to_chat" in [t["function"]["name"] for t in tools.TOOLS_SCHEMA_OPENAI]


def test_send_to_chat_returns_trimmed_capped_text():
    result = tools.execute_tool("send_to_chat", {"text": "  " + "а" * 5000 + "  "})
    assert result["action"] == "chat"
    assert len(result["text"]) == tools.CHAT_MESSAGE_MAX_CHARS


def _tool_call(name, arguments):
    call = SimpleNamespace(id="call-1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
    call.model_dump = lambda: {"id": "call-1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    return call


def _agent(monkeypatch, replies):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    monkeypatch.setattr(settings, "polza_model", "google/gemini-3.5-flash-lite")
    agent = HermesAgent("polza")
    queue = list(replies)

    async def fake_create(**kwargs):
        message, finish = queue.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=None)

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    return agent


def test_agent_collects_chat_messages_from_tool_call(monkeypatch):
    agent = _agent(monkeypatch, [
        (SimpleNamespace(content=None, tool_calls=[_tool_call("send_to_chat", {"text": "RED 750 ₽, Проще 450 ₽"})]), "tool_calls"),
        (SimpleNamespace(content="Отправила вам в чат.", tool_calls=None), "stop"),
    ])
    result = asyncio.run(agent.run([{"role": "user", "content": "пришлите цены"}], "+79990000000"))
    assert result["chat"] == ["RED 750 ₽, Проще 450 ₽"]
    assert result["response"] == "Отправила вам в чат."


def test_agent_without_chat_returns_empty_list(monkeypatch):
    agent = _agent(monkeypatch, [(SimpleNamespace(content="Привет", tool_calls=None), "stop")])
    result = asyncio.run(agent.run([{"role": "user", "content": "привет"}], "+79990000000"))
    assert result["chat"] == []


def test_dialog_response_includes_chat_and_delivers_it(monkeypatch):
    delivered = []

    class FakeAgent:
        async def run(self, messages, caller_phone):
            return {"response": "Отправила.", "messages": messages + [{"role": "assistant", "content": "Отправила."}],
                    "pending_transfer": None, "chat": ["подробности"]}

    async def fake_persist(*args, **kwargs):
        return True

    async def fake_deliver(session_id, messages):
        delivered.extend(messages)

    async def fake_get(session_id):
        return []

    async def fake_save(*args, **kwargs):
        return None

    monkeypatch.setattr(hermes_routes, "agent_for", lambda profile: FakeAgent())
    monkeypatch.setattr(hermes_routes, "_persist_turn", fake_persist)
    monkeypatch.setattr(hermes_routes, "_deliver_chat", fake_deliver)
    monkeypatch.setattr(hermes_routes, "_get_session", fake_get)
    monkeypatch.setattr(hermes_routes, "_save_session", fake_save)

    response = client.post(
        "/api/hermes/dialog",
        json={"session_id": "s1", "caller_phone": "+79990000000", "user_message": "пришлите цены"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["chat"] == ["подробности"]
    assert delivered == ["подробности"]


def _status_error(status):
    import httpx
    from openai import APIStatusError

    response = httpx.Response(status, request=httpx.Request("POST", "http://polza.test"))
    return APIStatusError("boom", response=response, body=None)


def test_provider_403_and_402_map_to_non_retryable_503(monkeypatch):
    async def fake_get(session_id):
        return []

    monkeypatch.setattr(hermes_routes, "_get_session", fake_get)
    for status, code in ((403, "model_not_allowed"), (402, "insufficient_balance")):
        class FailingAgent:
            async def run(self, messages, caller_phone, _status=status):
                raise _status_error(_status)

        monkeypatch.setattr(hermes_routes, "agent_for", lambda profile, _a=FailingAgent(): _a)
        response = client.post(
            "/api/hermes/dialog",
            json={"session_id": "s-err", "caller_phone": "+79990000000", "user_message": "привет"},
            headers=HEADERS,
        )
        error = response.json()["detail"]["error"]
        assert response.status_code == 503 and error["code"] == code and error["retryable"] is False


def test_session_history_keeps_tool_call_fields():
    history = [
        {"role": "user", "content": "пришлите цены"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "send_to_chat", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{'action': 'chat'}"},
        {"role": "assistant", "content": "Отправила."},
    ]
    plain = hermes_routes._to_plain(history)
    assert plain[1]["tool_calls"][0]["id"] == "c1"
    assert plain[2]["tool_call_id"] == "c1"
    assert plain[1]["content"] is None
    assert json.dumps(plain)  # сериализуется для Redis
