from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from src.main import app
from src.services import dialog

client = TestClient(app)

PAYLOAD = {"session_id": "s2", "caller_phone": "+79990000000", "user_message": "привет"}
HEADERS = {"X-API-Key": "test-local-key"}


def _stub_agent_and_cache(monkeypatch):
    """Агент и Redis замоканы: тест проверяет контракт эндпоинта, не Claude и не живой Redis."""

    async def fake_run(messages, caller_phone):
        return {
            "response": "Здравствуйте! Чем могу помочь?",
            "messages": messages + [{"role": "assistant", "content": "Здравствуйте! Чем могу помочь?"}],
            "pending_transfer": None,
        }

    async def fake_get(key):
        return None

    async def fake_set(key, value, ttl=3600):
        return None

    monkeypatch.setattr(dialog.hermes, "run", fake_run)
    monkeypatch.setattr(dialog.cache, "get", fake_get)
    monkeypatch.setattr(dialog.cache, "set", fake_set)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_hermes_dialog_requires_api_key():
    response = client.post("/api/hermes/dialog", json=PAYLOAD)
    assert response.status_code == 401


def test_hermes_dialog_success(monkeypatch):
    _stub_agent_and_cache(monkeypatch)

    async def fake_persist(**kwargs):
        return True

    monkeypatch.setattr(dialog, "_persist_turn", fake_persist)

    response = client.post("/api/hermes/dialog", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"session_id", "agent_response", "transfer", "chat", "links", "persisted"}
    assert data["session_id"] == "s2"
    assert isinstance(data["agent_response"], str) and data["agent_response"]
    assert data["transfer"] is None
    assert data["persisted"] is True


def test_hermes_dialog_supabase_down_returns_persisted_false(monkeypatch):
    _stub_agent_and_cache(monkeypatch)

    async def failing_persist(**kwargs):
        return False

    monkeypatch.setattr(dialog, "_persist_turn", failing_persist)

    response = client.post("/api/hermes/dialog", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["persisted"] is False


def test_hermes_dialog_redis_down_returns_503(monkeypatch):
    _stub_agent_and_cache(monkeypatch)

    async def broken_get(key):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(dialog.cache, "get", broken_get)

    response = client.post("/api/hermes/dialog", json=PAYLOAD, headers=HEADERS)

    assert response.status_code == 503
    error = response.json()["detail"]["error"]
    assert error["code"] == "session_store_unavailable"
    assert error["retryable"] is True
