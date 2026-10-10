"""
GET  /api/stt/status -> {enabled, models, default}
POST /api/stt        -> {text, model, duration, cost_rub}

Тело запроса — сырое аудио (Content-Type: audio/webm и т.п.), без multipart.
Ключ провайдера остаётся на сервере; модель выбирается только из STT_MODELS.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.api.auth import verify_api_key
from src.config import settings
from src.services.analytics import session_var
from src.services.stt import STTNotConfigured, stt_service
from src.utils import logger

router = APIRouter(dependencies=[Depends(verify_api_key)])


def _error(status: int, code: str, message: str, retryable: bool) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "retryable": retryable}},
    )


@router.get("/stt/status")
async def stt_status():
    return {
        "enabled": stt_service.enabled,
        "models": stt_service.models,
        "default": settings.stt_model,
    }


@router.post("/stt")
async def transcribe(request: Request, model: str = ""):
    if not stt_service.enabled:
        raise _error(503, "stt_not_configured", "Распознавание речи не настроено", False)
    audio = await request.body()
    if not audio:
        raise _error(400, "empty_audio", "Пустая запись", False)
    if len(audio) > settings.stt_max_bytes:
        raise _error(413, "audio_too_large", "Запись слишком длинная", False)
    chosen = model or settings.stt_model
    if chosen not in stt_service.models:
        raise _error(400, "model_not_allowed", "Модель не из списка STT_MODELS", False)
    session_var.set(request.headers.get("x-session-id", "")[:128])
    try:
        return await stt_service.transcribe(audio, request.headers.get("content-type", ""), chosen)
    except STTNotConfigured as e:
        raise _error(503, "stt_not_configured", str(e), False)
    except Exception as e:
        logger.error(f"STT request failed: {e}")
        raise _error(502, "stt_upstream_error", "Провайдер распознавания недоступен", True)
