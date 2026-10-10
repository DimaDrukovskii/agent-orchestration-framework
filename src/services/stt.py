"""
Распознавание речи (STT) через Polza — тот же ключ, что и для LLM/TTS.

POST {POLZA_BASE_URL}/audio/transcriptions, JSON: {model, file: data-URL base64, language}.
https://polza.ai/docs/api-reference/audio/transcriptions

Модели берутся из белого списка settings.stt_models: страница теста может
выбрать только из него, иначе запрос мог бы уйти на любую платную модель.
Тарификация по секундам звука; ответ содержит usage.cost_rub.
"""

import base64
import re
from typing import Any, Dict, Optional, Tuple

from src.config import settings
from src.services.analytics import analytics
from src.utils import logger


class STTNotConfigured(Exception):
    """Нет POLZA_API_KEY."""


class STTService:
    @property
    def enabled(self) -> bool:
        return bool(settings.polza_api_key)

    @property
    def models(self) -> list:
        return [m.strip() for m in settings.stt_models.split(",") if m.strip()]

    def build_request(
        self, audio: bytes, content_type: str, model: str
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        if not settings.polza_api_key:
            raise STTNotConfigured("POLZA_API_KEY не задан")
        if model not in self.models:
            raise ValueError(f"Модель {model} не в списке STT_MODELS")
        mime = (content_type or "audio/webm").split(";")[0].strip() or "audio/webm"
        url = f"{settings.polza_base_url.rstrip('/')}/audio/transcriptions"
        headers = {
            "Authorization": f"Bearer {settings.polza_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "file": f"data:{mime};base64,{base64.b64encode(audio).decode()}",
            "language": settings.stt_language,
        }
        return url, headers, payload

    async def transcribe(
        self, audio: bytes, content_type: str, model: Optional[str] = None
    ) -> Dict[str, Any]:
        import httpx

        model = model or settings.stt_model
        url, headers, payload = self.build_request(audio, content_type, model)
        try:
            async with httpx.AsyncClient(timeout=settings.stt_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as e:
            logger.error(f"STT (polza, {model}) error: {str(e)}")
            raise
        usage = body.get("usage") or {}
        result = {
            "text": (body.get("text") or "").strip(),
            "model": model,
            "duration": body.get("duration") or usage.get("durationSeconds"),
            "cost_rub": usage.get("cost_rub"),
        }
        analytics.record("stt", result["cost_rub"], model=model, units=result["duration"] or 0)
        return result


def _words(text: str) -> list:
    return re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е")).split()


def wer(reference: str, hypothesis: str) -> float:
    """Доля ошибочных слов: (замены + вставки + удаления) / слов в эталоне."""
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


stt_service = STTService()
