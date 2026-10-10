"""
GET /api/llm/status -> какие модели доступны виджету звонка.

default — модель из LLM_PROVIDER (облако), local — локальный сервер
(Ollama/LM Studio/vLLM). Виджет по этому ответу показывает, можно ли
выбрать локальную модель, а не падает на первой же реплике.
"""

import httpx
from fastapi import APIRouter, Depends

from src.api.auth import verify_api_key
from src.config import settings

PROBE_TIMEOUT_SECONDS = 2.0

router = APIRouter(dependencies=[Depends(verify_api_key)])


async def probe_local() -> bool:
    """Локальный сервер отвечает на GET /models (есть у Ollama, LM Studio, vLLM)."""
    url = f"{settings.local_base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            return response.status_code == 200
    except httpx.HTTPError:
        return False


def _default_model() -> str:
    if settings.llm_provider == "polza":
        return settings.polza_model
    if settings.llm_provider == "local":
        return settings.local_model
    return settings.anthropic_model


@router.get("/llm/status")
async def llm_status():
    return {
        "default": {"provider": settings.llm_provider, "model": _default_model()},
        "local": {"available": await probe_local(), "model": settings.local_model},
    }
