from fastapi.testclient import TestClient

from src.api import tts_routes
from src.config import settings
from src.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}


def _enable(monkeypatch, audio: bytes = b"ID3-fake-mp3"):
    monkeypatch.setattr(type(tts_routes.tts_service), "enabled", property(lambda self: True))

    async def fake_synthesize(text):
        return audio

    monkeypatch.setattr(tts_routes.tts_service, "synthesize", fake_synthesize)


def test_tts_requires_api_key():
    assert client.get("/api/tts/status").status_code == 401
    assert client.post("/api/tts", json={"text": "привет"}).status_code == 401


def test_tts_status_reports_disabled_by_default():
    response = client.get("/api/tts/status", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_tts_returns_503_when_not_configured():
    response = client.post("/api/tts", json={"text": "привет"}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "tts_not_configured"


def test_tts_returns_audio_when_enabled(monkeypatch):
    _enable(monkeypatch)
    response = client.post("/api/tts", json={"text": "Здравствуйте!"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3-fake-mp3"


def test_tts_rejects_empty_and_oversized_text(monkeypatch):
    _enable(monkeypatch)
    assert client.post("/api/tts", json={"text": ""}, headers=HEADERS).status_code == 422
    too_long = "а" * (tts_routes.MAX_TTS_CHARS + 1)
    assert client.post("/api/tts", json={"text": too_long}, headers=HEADERS).status_code == 422


def test_tts_upstream_failure_is_retryable_502(monkeypatch):
    monkeypatch.setattr(type(tts_routes.tts_service), "enabled", property(lambda self: True))

    async def broken(text):
        raise RuntimeError("elevenlabs down")

    monkeypatch.setattr(tts_routes.tts_service, "synthesize", broken)
    response = client.post("/api/tts", json={"text": "привет"}, headers=HEADERS)
    assert response.status_code == 502
    assert response.json()["detail"]["error"]["retryable"] is True
