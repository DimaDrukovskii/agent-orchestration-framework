import json

from fastapi.testclient import TestClient

from src.api import hermes_routes
from src.agent.hermes_agent import pop_speech_chunks
from src.main import app


def test_speech_chunks_release_a_clause_before_sentence_end():
    chunks, remainder = pop_speech_chunks(
        "Для спальни стоит проверить уровень шума и расположение кровати, чтобы подобрать модель."
    )
    assert chunks == ["Для спальни стоит проверить уровень шума и расположение кровати,"]
    assert remainder == "чтобы подобрать модель."


def test_dialog_events_preserve_sentence_order_and_done(monkeypatch):
    async def fake_turn(*args):
        yield {"type": "sentence", "text": "Сейчас проверю."}
        yield {"type": "tool", "name": "available_slots"}
        yield {"type": "sentence", "text": "Есть время на завтра."}
        yield {"type": "done", "response": "Сейчас проверю. Есть время на завтра.", "transfer": None}

    monkeypatch.setattr(hermes_routes.dialog, "run_turn_stream", fake_turn)
    response = TestClient(app).post(
        "/api/hermes/dialog/events",
        headers={"X-API-Key": "test-local-key"},
        json={"session_id": "test-stream", "caller_phone": "demo", "user_message": "Когда можно?"},
    )
    assert response.status_code == 200
    assert [json.loads(line)["type"] for line in response.text.splitlines()] == [
        "sentence", "tool", "sentence", "done"
    ]
