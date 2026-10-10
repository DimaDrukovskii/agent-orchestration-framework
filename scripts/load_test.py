"""
Нагрузочная проверка нашего сервера (без платных запросов): N одновременных звонков,
по нескольку реплик в каждом. Вместо модели — имитация с задержкой, всё остальное
настоящее: маршрут /api/hermes/dialog, сессии, запись звонка в базу.

    python3 scripts/load_test.py [звонков] [реплик] [задержка_модели_сек]

Если сервер не блокируется, общее время ≈ реплик * задержка (звонки идут параллельно),
а не звонков * реплик * задержка.
"""

import asyncio
import os
import statistics
import sys
import tempfile
import time

os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(prefix="hermes-load-"), "load.db"))

import httpx  # noqa: E402

from src.agent import tools  # noqa: E402
from src.services import dialog  # noqa: E402
from src.config import settings  # noqa: E402
from src.main import app  # noqa: E402

CALLS = int(sys.argv[1]) if len(sys.argv) > 1 else 50
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
MODEL_DELAY = float(sys.argv[3]) if len(sys.argv) > 3 else 2.5


class SimulatedAgent:
    """Как настоящий агент: поиск по базе знаний (синхронный), затем «ответ модели» с задержкой."""

    async def run(self, messages, caller_phone):
        tools.kb_context(messages[-1]["content"])
        await asyncio.sleep(MODEL_DELAY)
        messages = messages + [{"role": "assistant", "content": "Ответ."}]
        return {"response": "Ответ.", "messages": messages, "pending_transfer": None, "chat": []}


async def one_call(client, index, latencies, failures):
    session = f"load-{index}"
    for turn in range(TURNS):
        started = time.perf_counter()
        response = await client.post(
            "/api/hermes/dialog",
            json={"session_id": session, "caller_phone": f"+7999{index:07d}", "user_message": f"Сколько стоит тариф RED, вопрос {turn}?"},
            headers={"X-API-Key": settings.agent_api_key},
        )
        latencies.append(time.perf_counter() - started)
        if response.status_code != 200:
            failures.append(response.status_code)


async def main():
    dialog.agent_for = lambda profile: SimulatedAgent()
    latencies, failures = [], []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
        started = time.perf_counter()
        await asyncio.gather(*(one_call(client, i, latencies, failures) for i in range(CALLS)))
        total = time.perf_counter() - started

    ideal = TURNS * MODEL_DELAY
    latencies.sort()
    print(f"звонков: {CALLS} одновременно, реплик в каждом: {TURNS}, задержка модели: {MODEL_DELAY} с")
    print(f"общее время: {total:.1f} с (идеал при полной параллельности ≈ {ideal:.1f} с, последовательно было бы {CALLS * ideal:.0f} с)")
    print(f"реплика: медиана {statistics.median(latencies):.2f} с, p95 {latencies[int(len(latencies) * 0.95) - 1]:.2f} с, максимум {latencies[-1]:.2f} с")
    print(f"ошибок: {len(failures)} {sorted(set(failures)) or ''}")


asyncio.run(main())
