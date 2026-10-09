from fastapi.testclient import TestClient
from src.main import app
from src.api import hermes_routes

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_hermes_dialog_requires_api_key():
    response = client.post(
        "/api/hermes/dialog",
        json={"session_id": "s1", "caller_phone": "+79990000000", "user_message": "привет"},
    )
    assert response.status_code == 401


def test_hermes_dialog_success(monkeypatch):
    """
    Мокаем HermesAgent.run — тест проверяет контракт эндпоинта
    (структуру ответа), не текст от Claude (недетерминирован, и не должен
    требовать реального ANTHROPIC_API_KEY).
    """

    async def fake_run(messages, caller_phone):
        return {
            "response": "Здравствуйте! Чем могу помочь?",
            "messages": messages + [{"role": "assistant", "content": "Здравствуйте! Чем могу помочь?"}],
            "pending_transfer": None,
        }

    monkeypatch.setattr(hermes_routes.hermes, "run", fake_run)

    response = client.post(
        "/api/hermes/dialog",
        json={"session_id": "s2", "caller_phone": "+79990000000", "user_message": "привет"},
        headers={"X-API-Key": "test-local-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"session_id", "agent_response", "transfer"}
    assert data["session_id"] == "s2"
    assert isinstance(data["agent_response"], str) and data["agent_response"]
    assert data["transfer"] is None
