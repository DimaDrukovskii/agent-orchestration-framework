"""
Озвучка ответа агента (TTS) — структура под "проводочки" (docs/mts/SCALING_PLAN.md):
провайдер выбирается через TTS_PROVIDER в .env, код вызывающей стороны
(hermes_routes / будущий voice-эндпоинт) не меняется при смене ключа.

Статус провайдеров:
- "none"       — озвучки нет, агент отвечает текстом (дефолт, работает без ключей)
- "polza"      — голоса ElevenLabs через тот же ключ polza.ai, что и LLM
                 (POLZA_API_KEY), отдельный ключ не нужен. Голос — имя
                 пресета (Sarah, Rachel, Aria, Roger) в TTS_VOICE_ID.
                 https://polza.ai/docs/api-reference/audio/speech
- "elevenlabs" — прямой API ElevenLabs, нужен TTS_API_KEY и TTS_VOICE_ID в .env
                 (https://elevenlabs.io/docs/api-reference/text-to-speech)
- "yandex"     — НЕ реализован, только ссылка на API. Токена пока нет и может
                 не появиться сегодня — не добавлять вызов вслепую, просто
                 подключить провайдера ниже по тому же паттерну, когда он будет:
                 https://yandex.cloud/ru/docs/speechkit/tts/request

TTSService.synthesize() — единая точка входа независимо от провайдера.
"""

import base64
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from src.config import settings
from src.services.speech_text import prepare_for_speech
from src.services.analytics import analytics
from src.utils import logger


class TTSNotConfigured(Exception):
    """tts_provider="none" или провайдер ещё не реализован (yandex)."""


POLZA_DEFAULT_MODEL = "elevenlabs/text-to-speech-multilingual-v2"
POLZA_DEFAULT_VOICE = "Sarah"
ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"
TTS_CACHE_MAX_ENTRIES = 200  # ~50 КБ на фразу: потолок около 10 МБ памяти


class TTSService:
    def __init__(self):
        self.provider = settings.tts_provider
        # приветствие и типовые фразы повторяются в каждом звонке — платим за озвучку один раз
        self._cache: "OrderedDict[tuple, bytes]" = OrderedDict()

    @property
    def enabled(self) -> bool:
        """Озвучка реально доступна: провайдер выбран и есть нужные ключи."""
        if self.provider == "polza":
            return bool(settings.polza_api_key)
        return (
            self.provider == "elevenlabs"
            and bool(settings.tts_api_key)
            and bool(settings.tts_voice_id)
        )

    def build_polza_request(self, text: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        url = f"{settings.polza_base_url.rstrip('/')}/audio/speech"
        headers = {
            "Authorization": f"Bearer {settings.polza_api_key}",
            "Content-Type": "application/json",
        }
        model = settings.tts_model or POLZA_DEFAULT_MODEL
        payload: Dict[str, Any] = {
            "model": model,
            "voice": settings.tts_voice_id or POLZA_DEFAULT_VOICE,
            "input": text,
            "response_format": "mp3",
        }
        if model.startswith("elevenlabs/"):  # эти поля понимает только семейство ElevenLabs
            payload["stability"] = settings.tts_stability
            payload["similarity_boost"] = settings.tts_similarity
        return url, headers, payload

    @staticmethod
    def decode_polza_response(body: Dict[str, Any]) -> bytes:
        """Polza отдаёт JSON с base64-звуком, а не бинарный mp3."""
        encoded = body.get("audio")
        if not encoded:
            raise ValueError(f"polza TTS: в ответе нет audio: {str(body)[:200]}")
        return base64.b64decode(encoded)

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Вернуть аудио (mp3) для текста, либо None если озвучка выключена."""
        if self.provider == "none":
            return None
        text = prepare_for_speech(text, settings.tts_stress_marks)
        key = (self.provider, settings.tts_model, settings.tts_voice_id, text)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        audio = await self._synthesize_uncached(text)
        if audio:
            self._cache[key] = audio
            while len(self._cache) > TTS_CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)
        return audio

    async def _synthesize_uncached(self, text: str) -> Optional[bytes]:
        if self.provider == "polza":
            return await self._synthesize_polza(text)
        if self.provider == "elevenlabs":
            return await self._synthesize_elevenlabs(text)
        if self.provider == "yandex":
            # Структура провайдера такая же, как elevenlabs ниже — добавить
            # реализацию по https://yandex.cloud/ru/docs/speechkit/tts/request
            # когда появится токен. Сейчас сознательно не реализовано.
            raise TTSNotConfigured("yandex TTS: токен ещё не подключён, см. docstring")
        raise TTSNotConfigured(f"Неизвестный TTS_PROVIDER: {self.provider}")

    async def _synthesize_polza(self, text: str) -> bytes:
        import httpx

        if not settings.polza_api_key:
            raise TTSNotConfigured("POLZA_API_KEY не задан для TTS_PROVIDER=polza")

        url, headers, payload = self.build_polza_request(text)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
                usage = body.get("usage") or {}
                analytics.record("tts", usage.get("cost_rub", body.get("cost_rub")), model=payload["model"], units=len(text))
                return self.decode_polza_response(body)
        except Exception as e:
            logger.error(f"TTS (polza) error: {str(e)}")
            raise

    async def _synthesize_elevenlabs(self, text: str) -> bytes:
        import httpx

        if not settings.tts_api_key or not settings.tts_voice_id:
            raise TTSNotConfigured("TTS_API_KEY / TTS_VOICE_ID не заданы для elevenlabs")

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}"
        headers = {"xi-api-key": settings.tts_api_key, "Content-Type": "application/json"}
        payload = {
            "text": text,
            "model_id": settings.tts_model or ELEVENLABS_DEFAULT_MODEL,
            "voice_settings": {
                "stability": settings.tts_stability,
                "similarity_boost": settings.tts_similarity,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.error(f"TTS (elevenlabs) error: {str(e)}")
            raise


tts_service = TTSService()
