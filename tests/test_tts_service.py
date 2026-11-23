import asyncio
import base64

import pytest

from src.config import settings
from src.services import tts as tts_module
from src.services.tts import TTSNotConfigured, TTSService


@pytest.fixture
def polza(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "polza")
    monkeypatch.setattr(settings, "polza_api_key", "pk-test")
    monkeypatch.setattr(settings, "polza_base_url", "https://polza.example/api/v1")
    monkeypatch.setattr(settings, "tts_model", "")
    monkeypatch.setattr(settings, "tts_voice_id", "")
    return TTSService()


def test_polza_enabled_needs_key_but_not_voice(polza, monkeypatch):
    assert polza.enabled is True
    monkeypatch.setattr(settings, "polza_api_key", "")
    assert polza.enabled is False


def test_polza_request_uses_defaults(polza):
    url, headers, payload = polza.build_polza_request("Здравствуйте")
    assert url == "https://polza.example/api/v1/audio/speech"
    assert headers["Authorization"] == "Bearer pk-test"
    assert payload["model"] == "elevenlabs/text-to-speech-multilingual-v2"
    assert payload["voice"] == "Sarah"
    assert payload["input"] == "Здравствуйте"
    assert payload["response_format"] == "mp3"
    assert payload["stability"] == settings.tts_stability
    assert payload["similarity_boost"] == settings.tts_similarity


def test_polza_request_honours_model_and_voice_overrides(polza, monkeypatch):
    monkeypatch.setattr(settings, "tts_model", "elevenlabs/text-to-speech-turbo-2-5")
    monkeypatch.setattr(settings, "tts_voice_id", "Aria")
    _, _, payload = polza.build_polza_request("привет")
    assert payload["model"] == "elevenlabs/text-to-speech-turbo-2-5"
    assert payload["voice"] == "Aria"


def test_polza_response_is_decoded_from_base64():
    raw = b"ID3-fake-mp3"
    body = {"audio": base64.b64encode(raw).decode(), "contentType": "audio/mpeg"}
    assert TTSService.decode_polza_response(body) == raw


def test_polza_response_without_audio_is_an_error():
    with pytest.raises(ValueError):
        TTSService.decode_polza_response({"error": {"code": "x"}})


def test_synthesize_polza_not_configured_without_key(polza, monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "")
    with pytest.raises(TTSNotConfigured):
        asyncio.run(polza.synthesize("привет"))


def test_module_exposes_singleton():
    assert isinstance(tts_module.tts_service, TTSService)


def test_polza_elevenlabs_params_only_for_elevenlabs_models(polza, monkeypatch):
    monkeypatch.setattr(settings, "tts_model", "google/gemini-3.1-flash-tts-preview")
    monkeypatch.setattr(settings, "tts_voice_id", "Aoede")
    _, _, payload = polza.build_polza_request("привет")
    assert payload["model"] == "google/gemini-3.1-flash-tts-preview"
    assert payload["voice"] == "Aoede"
    assert "stability" not in payload and "similarity_boost" not in payload

    monkeypatch.setattr(settings, "tts_model", "elevenlabs/text-to-speech-turbo-2-5")
    _, _, payload = polza.build_polza_request("привет")
    assert payload["stability"] == settings.tts_stability


@pytest.mark.asyncio
async def test_elevenlabs_stream_uses_configured_voice_model_and_phone_codec(monkeypatch):
    import json
    from contextlib import aclosing

    import httpx

    for field, value in (("tts_api_key", "fixture-key"), ("tts_voice_id", "fixture-voice"), ("tts_model", "eleven_flash_v2_5")):
        monkeypatch.setattr(settings, field, value)

    closed = False

    class Audio(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\xff" * 160
            pytest.fail("The provider stream was read past the first frame")

        async def aclose(self):
            nonlocal closed
            closed = True

    def provider(request):
        assert request.url.path == "/v1/text-to-speech/fixture-voice/stream"
        assert request.url.params["output_format"] == "ulaw_8000"
        assert request.headers["xi-api-key"] == "fixture-key"
        payload = json.loads(request.content)
        assert payload["model_id"] == "eleven_flash_v2_5"
        assert payload["voice_settings"]["speed"] == settings.tts_speed
        return httpx.Response(200, stream=Audio())

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(provider), **kw))
    async with aclosing(TTSService().stream_elevenlabs_mulaw("Добрый день")) as stream:
        assert await anext(stream) == b"\xff" * 160
    assert closed
