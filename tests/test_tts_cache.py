import asyncio

from src.config import settings
from src.services.tts import TTS_CACHE_MAX_ENTRIES, TTSService


def run(coro):
    return asyncio.run(coro)


def make_service(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "polza")
    service = TTSService()
    calls = []

    async def fake_upstream(text):
        calls.append(text)
        return b"mp3:" + text.encode()

    monkeypatch.setattr(service, "_synthesize_uncached", fake_upstream)
    return service, calls


def test_repeated_phrase_is_paid_once(monkeypatch):
    service, calls = make_service(monkeypatch)

    first = run(service.synthesize("Здравствуйте"))
    second = run(service.synthesize("Здравствуйте"))

    assert first == second
    assert calls == ["Здравствуйте"]


def test_different_text_or_voice_is_not_shared(monkeypatch):
    service, calls = make_service(monkeypatch)

    run(service.synthesize("один"))
    run(service.synthesize("два"))
    monkeypatch.setattr(settings, "tts_voice_id", "other-voice")
    run(service.synthesize("один"))

    assert calls == ["один", "два", "один"]


def test_cache_is_bounded(monkeypatch):
    service, calls = make_service(monkeypatch)

    for i in range(TTS_CACHE_MAX_ENTRIES + 5):
        run(service.synthesize(f"фраза {i}"))

    assert len(service._cache) == TTS_CACHE_MAX_ENTRIES


def test_disabled_provider_returns_none_without_upstream(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "none")
    service = TTSService()

    assert run(service.synthesize("текст")) is None
