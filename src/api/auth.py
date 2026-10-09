"""
Проверка API-ключа для публичных эндпоинтов агента.

MVP: один общий ключ из settings.agent_api_key (переменная окружения
AGENT_API_KEY). Это осознанно просто — см. docs/SCALING_PLAN.md §2 и §7
(Этап 1): таблица api_clients с ключом на бизнес — следующий шаг, не этот.
"""

from typing import Optional
from fastapi import Header, HTTPException, status
from src.config import settings
from src.utils import logger


def _reject(reason: str) -> None:
    logger.warning(f"Rejected request: {reason}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "invalid_api_key", "message": "Invalid API key", "retryable": False}}
    )


async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")) -> str:
    # Header опционален на уровне FastAPI (иначе отсутствие ключа даёт 422,
    # а не единый формат ошибки из docs/SCALING_PLAN.md §2) — отсутствие и
    # неверное значение должны выглядеть для интегратора одинаково: 401.
    if not x_api_key:
        _reject("missing X-API-Key")
    if x_api_key != settings.agent_api_key:
        _reject("invalid X-API-Key")
    return x_api_key
