"""
Асинхронный контракт поверх Hermes-агента — скелет для нагрузки МТС.

POST /api/hermes/dialog/async       -> 202 {task_id, status: "queued"}
GET  /api/hermes/dialog/async/{id}  -> {status, agent_response?, transfer?, error?}

Сознательно НЕ смешан с синхронным путём (src/api/hermes_routes.py):
- без Redis-сессий и Supabase-персиста — одна реплика = один изолированный
  вызов агента, без истории разговора (worker.py передаёт только текущее
  сообщение)
- без X-API-Key — auth на этот путь не навешан

Это осознанный порядок (сначала структура, остальное — отдельным шагом),
не недосмотр. Прежде чем этот путь станет публичным контрактом для МТС
(docs/SCALING_PLAN.md §2), сюда нужно дотянуть то же самое, что уже есть
в синхронном пути: историю диалога, персист, авторизацию, лимиты.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from src.services.queue import queue

router = APIRouter()


class AsyncDialogRequest(BaseModel):
    session_id: str
    caller_phone: str
    user_message: str


class AsyncDialogAccepted(BaseModel):
    task_id: str
    status: str


class AsyncDialogStatus(BaseModel):
    task_id: str
    status: str
    agent_response: Optional[str] = None
    transfer: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/hermes/dialog/async", response_model=AsyncDialogAccepted, status_code=202)
async def hermes_dialog_async(request: AsyncDialogRequest):
    task = await queue.enqueue(request.session_id, request.caller_phone, request.user_message)
    return AsyncDialogAccepted(task_id=task.task_id, status=task.status.value)


@router.get("/hermes/dialog/async/{task_id}", response_model=AsyncDialogStatus)
async def hermes_dialog_async_status(task_id: str):
    task = queue.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return AsyncDialogStatus(
        task_id=task.task_id,
        status=task.status.value,
        agent_response=(task.result or {}).get("agent_response"),
        transfer=(task.result or {}).get("transfer"),
        error=task.error,
    )
