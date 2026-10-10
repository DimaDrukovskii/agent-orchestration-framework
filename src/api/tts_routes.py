"""
GET  /api/tts/status -> {enabled, provider}   (фронт решает: серверный голос или браузерный)
POST /api/tts        -> audio/mpeg            (озвучка одной реплики через TTSService)

Ключ провайдера остаётся на сервере: страница звонка шлёт только текст и
X-API-Key, как и для /api/hermes/dialog.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.api.auth import verify_api_key
from src.services.tts import TTSNotConfigured, tts_service
from src.utils import logger

MAX_TTS_CHARS = 600  # реплика агента короче; лимит защищает от расхода кредитов

router = APIRouter(dependencies=[Depends(verify_api_key)])


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)


def _error(status: int, code: str, message: str, retryable: bool) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "retryable": retryable}},
    )


@router.get("/tts/status")
async def tts_status():
    return {"enabled": tts_service.enabled, "provider": tts_service.provider}


@router.post("/tts")
async def synthesize(request: TTSRequest):
    if not tts_service.enabled:
        raise _error(503, "tts_not_configured", "Серверная озвучка не настроена", False)
    try:
        audio = await tts_service.synthesize(request.text)
    except TTSNotConfigured as e:
        raise _error(503, "tts_not_configured", str(e), False)
    except Exception as e:
        logger.error(f"TTS request failed: {e}")
        raise _error(502, "tts_upstream_error", "Провайдер озвучки недоступен", True)
    if not audio:
        raise _error(503, "tts_not_configured", "Серверная озвучка не настроена", False)
    return Response(content=audio, media_type="audio/mpeg")
