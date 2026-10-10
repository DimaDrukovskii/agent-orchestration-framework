"""
Асинхронная очередь обработки диалоговых реплик — скелет.

Зачем: под нагрузку МТС (много параллельных звонков) синхронный
"запрос → полный ReAct-цикл агента → ответ в том же HTTP-вызове"
(src/api/hermes_routes.py) не масштабируется бесконечно — долгий
tool-цикл агента держит HTTP-соединение и воркер FastAPI всё это время.
Эта очередь — структура под "принял реплику → сразу ответил 202 → воркер
обработал в фоне → результат забирают по task_id".

Сознательно только структура (по договорённости: сначала скелет, остальное
потом):
- Persist задачи — в памяти процесса, не в Supabase/Redis. Переживёт процесс
  столько же, сколько синхронные сессии переживали раньше (т.е. не переживёт).
- Доставка результата — только poll по task_id, не webhook (см.
  docs/mts/SCALING_PLAN.md §4.1 — webhook_url уже заложен в схему api_clients,
  сюда пока не подключаю).
- Auth и тесты — не здесь, см. src/api/hermes_async_routes.py.

Когда дойдём до следующего шага: `QueueService` — это интерфейс, который
нужно сохранить (enqueue/dequeue/get/mark_done/mark_failed), а конкретную
in-memory реализацию заменить на Redis Streams/SQS/что угодно с at-least-once
доставкой — вызывающий код (router, worker) не меняется.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
import asyncio
import uuid


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class DialogTask:
    task_id: str
    session_id: str
    caller_phone: str
    user_message: str
    status: TaskStatus = TaskStatus.QUEUED
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class QueueService:
    def __init__(self):
        self._tasks: Dict[str, DialogTask] = {}
        self._pending: "asyncio.Queue[str]" = asyncio.Queue()

    async def enqueue(self, session_id: str, caller_phone: str, user_message: str) -> DialogTask:
        task = DialogTask(
            task_id=str(uuid.uuid4()),
            session_id=session_id,
            caller_phone=caller_phone,
            user_message=user_message,
        )
        self._tasks[task.task_id] = task
        await self._pending.put(task.task_id)
        return task

    async def dequeue(self) -> DialogTask:
        """Блокируется, пока воркер не получит следующую задачу."""
        task_id = await self._pending.get()
        task = self._tasks[task_id]
        task.status = TaskStatus.PROCESSING
        return task

    def get(self, task_id: str) -> Optional[DialogTask]:
        return self._tasks.get(task_id)

    def mark_done(self, task_id: str, result: Dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.DONE
            task.result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error = error


# Один процесс — один инстанс в памяти, как cache/hermes в остальном коде.
queue = QueueService()
