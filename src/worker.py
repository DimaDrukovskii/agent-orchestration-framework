"""
Воркер — разбирает очередь (src/services/queue.py) и прогоняет Hermes-агента
асинхронно от HTTP-запроса. Скелет: без персиста в Supabase и без истории
диалога по session_id (оба — "потом", см. src/api/hermes_async_routes.py).

Запускается как фоновая задача при старте приложения (src/main.py), не как
отдельный процесс — для одного демо-инстанса этого достаточно, чтобы
показать структуру. Отдельный процесс воркера (несколько реплик параллельно,
не один за другим) — следующий шаг, когда очередь переедет на Redis/SQS.
"""

from src.agent.hermes_agent import HermesAgent
from src.services.queue import queue
from src.utils import logger

hermes = HermesAgent()


async def run_worker() -> None:
    """Бесконечный цикл: взять задачу → прогнать агента → записать результат."""
    while True:
        task = await queue.dequeue()
        logger.info(f"[worker] processing task {task.task_id} (session={task.session_id})")
        try:
            result = await hermes.run(
                messages=[{"role": "user", "content": task.user_message}],
                caller_phone=task.caller_phone,
            )
            queue.mark_done(task.task_id, {
                "agent_response": result["response"],
                "transfer": result["pending_transfer"],
            })
        except Exception as e:
            logger.error(f"[worker] task {task.task_id} failed: {str(e)}")
            queue.mark_failed(task.task_id, str(e))
