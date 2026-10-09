"""
Озвучка ответа агента (TTS) — структура под "проводочки" (docs/SCALING_PLAN.md):
провайдер выбирается через TTS_PROVIDER в .env, код вызывающей стороны
(hermes_routes / будущий voice-эндпоинт) не меняется при смене ключа.

Статус провайдеров:
- "none"       — озвучки нет, агент отвечает текстом (дефолт, работает без ключей)
- "elevenlabs" — реализовано структурно ниже, нужен ELEVENLABS_API_KEY в .env
                 (https://elevenlabs.io/docs/api-reference/text-to-speech)
- "yandex"     — НЕ реализован, только ссылка на API. Токена пока нет и может
                 не появиться сегодня — не добавлять вызов вслепую, просто
                 подключить провайдера ниже по тому же паттерну, когда он будет:
                 https://yandex.cloud/ru/docs/speechkit/tts/request

TTSService.synthesize() — единая точка входа независимо от провайдера.
"""

from typing import Optional
from src.config import settings
from src.utils import logger


class TTSNotConfigured(Exception):
    """tts_provider="none" или провайдер ещё не реализован (yandex)."""


class TTSService:
    def __init__(self):
        self.provider = settings.tts_provider

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Вернуть аудио (mp3) для текста, либо None если озвучка выключена."""
        if self.provider == "none":
            return None
        if self.provider == "elevenlabs":
            return await self._synthesize_elevenlabs(text)
        if self.provider == "yandex":
            # Структура провайдера такая же, как elevenlabs ниже — добавить
            # реализацию по https://yandex.cloud/ru/docs/speechkit/tts/request
            # когда появится токен. Сейчас сознательно не реализовано.
            raise TTSNotConfigured("yandex TTS: токен ещё не подключён, см. docstring")
        raise TTSNotConfigured(f"Неизвестный TTS_PROVIDER: {self.provider}")

    async def _synthesize_elevenlabs(self, text: str) -> bytes:
        import httpx

        if not settings.tts_api_key or not settings.tts_voice_id:
            raise TTSNotConfigured("TTS_API_KEY / TTS_VOICE_ID не заданы для elevenlabs")

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}"
        headers = {"xi-api-key": settings.tts_api_key, "Content-Type": "application/json"}
        payload = {
            "text": text,
            "model_id": "eleven_flash_v2_5",  # низкая задержка, подходит для звонка
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
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
