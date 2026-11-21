"""Показывает настроенную облачную модель без раскрытия ключа."""
from fastapi import APIRouter, Depends

from src.api.auth import verify_api_key
from src.config import settings

router = APIRouter(dependencies=[Depends(verify_api_key)])


def _default_model() -> str:
    if settings.llm_provider == "polza":
        return settings.polza_model
    if settings.llm_provider == "openrouter":
        return settings.openrouter_model
    if settings.llm_provider == "hermes_agent":
        return settings.hermes_agent_model
    return settings.anthropic_model


@router.get("/llm/status")
async def llm_status():
    configured = {
        "polza": bool(settings.polza_api_key),
        "openrouter": bool(settings.openrouter_api_key),
        "hermes_agent": bool(settings.hermes_agent_base_url),
        "anthropic": bool(settings.anthropic_api_key),
    }.get(settings.llm_provider, False)
    return {"default": {"provider": settings.llm_provider, "model": _default_model(), "configured": configured}}
