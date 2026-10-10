"""
GET /api/analytics/summary?days=7 -> расходы и метрики диалогов за период (панель demo/tools/analytics.html).

Данные берутся из usage_events (src/services/analytics.py). Тот же X-API-Key, что у остальных маршрутов.
"""

import asyncio

from fastapi import APIRouter, Depends, Query

from src.api.auth import verify_api_key
from src.services.analytics import analytics

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/analytics/summary")
async def analytics_summary(days: int = Query(7, ge=1, le=90)):
    return await asyncio.to_thread(analytics.summary, days)
