import base64

import pytest
from fastapi.testclient import TestClient

from src.api import stt_routes
from src.config import settings
from src.main import app
from src.services.stt import STTService, STTNotConfigured, wer

client = TestClient(app)
HEADERS = {"X-API-Key": settings.agent_api_key}
AUDIO = b"RIFF-fake-wav-bytes"


def test_build_request_uses_json_data_url_and_language(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    service = STTService()
    url, headers, payload = service.build_request(AUDIO, "audio/webm", "openai/whisper-large-v3-turbo")
    assert url.endswith("/audio/transcriptions")
    assert headers["Authorization"] == "Bearer pza-test"
    assert payload["model"] == "openai/whisper-large-v3-turbo"
    assert payload["language"] == settings.stt_language
    assert payload["file"] == "data:audio/webm;base64," + base64.b64encode(AUDIO).decode()


def test_build_request_strips_codec_suffix_from_content_type(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    _, _, payload = STTService().build_request(AUDIO, "audio/webm;codecs=opus", settings.stt_model)
    assert payload["file"].startswith("data:audio/webm;base64,")


def test_unknown_model_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    with pytest.raises(ValueError):
        STTService().build_request(AUDIO, "audio/webm", "openai/gpt-4o-transcribe-diarize")


def test_not_configured_without_key(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "")
    assert STTService().enabled is False
    with pytest.raises(STTNotConfigured):
        STTService().build_request(AUDIO, "audio/webm", settings.stt_model)


def test_wer_counts_word_errors_ignoring_case_and_punctuation():
    assert wer("Здравствуйте, мне нужен тариф", "здравствуйте мне нужен тариф") == 0
    assert wer("мне нужен тариф", "мне нужен") == pytest.approx(1 / 3)
    assert wer("", "") == 0
    assert wer("", "что-то") == 1


def _enable(monkeypatch, result=None):
    monkeypatch.setattr(type(stt_routes.stt_service), "enabled", property(lambda self: True))

    async def fake_transcribe(audio, content_type, model=None):
        return result or {"text": "привет", "model": model or settings.stt_model, "duration": 1.5, "cost_rub": 0.01}

    monkeypatch.setattr(stt_routes.stt_service, "transcribe", fake_transcribe)


def test_stt_requires_api_key():
    assert client.get("/api/stt/status").status_code == 401
    assert client.post("/api/stt", content=AUDIO).status_code == 401


def test_stt_status_lists_models(monkeypatch):
    _enable(monkeypatch)
    body = client.get("/api/stt/status", headers=HEADERS).json()
    assert body["enabled"] is True
    assert settings.stt_model in body["models"]


def test_stt_returns_text(monkeypatch):
    _enable(monkeypatch)
    response = client.post("/api/stt", content=AUDIO, headers={**HEADERS, "Content-Type": "audio/webm"})
    assert response.status_code == 200
    assert response.json()["text"] == "привет"


def test_stt_503_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "")
    response = client.post("/api/stt", content=AUDIO, headers={**HEADERS, "Content-Type": "audio/webm"})
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "stt_not_configured"


def test_stt_rejects_empty_and_oversized_audio(monkeypatch):
    _enable(monkeypatch)
    headers = {**HEADERS, "Content-Type": "audio/webm"}
    assert client.post("/api/stt", content=b"", headers=headers).status_code == 400
    monkeypatch.setattr(settings, "stt_max_bytes", 10)
    assert client.post("/api/stt", content=b"x" * 11, headers=headers).status_code == 413


def test_stt_rejects_model_outside_allowlist(monkeypatch):
    _enable(monkeypatch)
    response = client.post(
        "/api/stt?model=openai/gpt-4o-transcribe-diarize",
        content=AUDIO,
        headers={**HEADERS, "Content-Type": "audio/webm"},
    )
    assert response.status_code == 400
