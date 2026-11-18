"""Manager operations for the simulated outbound channel."""

import asyncio
import sqlite3
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import verify_api_key
from src.config import settings
from src.services.callbacks import CallbackQueue, CallbackWorker, CallingHours, SimulatedCallbackAdapter
from src.services.storage import StorageService
from src.utils import logger

router = APIRouter(prefix="/callbacks", dependencies=[Depends(verify_api_key)])


class CallbackRequest(BaseModel):
    source_call_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)


def public_job(job: dict) -> dict:
    return {key: job[key] for key in ("id", "source_call_id", "status", "outcome", "attempts", "due_at")}


def hours() -> CallingHours:
    return CallingHours(settings.callback_start_hour, settings.callback_end_hour, ZoneInfo("Europe/Minsk"))


def require_simulator() -> None:
    if not settings.callback_simulator_enabled:
        raise HTTPException(404, "Симулятор обратных звонков выключен")


@router.post("")
async def enqueue(request: CallbackRequest):
    require_simulator()
    call = await StorageService().get_call(request.source_call_id)
    if not call:
        raise HTTPException(404, "Исходный звонок не найден")
    queue = await asyncio.to_thread(CallbackQueue, settings.sqlite_path)
    try:
        job = await asyncio.to_thread(
            queue.enqueue, request.source_call_id, request.request_id, hours().next_allowed(time.time()),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return public_job(job)


@router.get("/{job_id}")
async def status(job_id: str):
    require_simulator()
    queue = await asyncio.to_thread(CallbackQueue, settings.sqlite_path)
    job = await asyncio.to_thread(queue.get, job_id)
    if not job:
        raise HTTPException(404, "Обратный звонок не найден")
    return public_job(job)


async def run_worker() -> None:
    queue = await asyncio.to_thread(CallbackQueue, settings.sqlite_path)
    worker = CallbackWorker(
        queue, SimulatedCallbackAdapter(queue, settings.callback_simulator_outcome), hours(),
        max_attempts=settings.callback_max_attempts, retry_seconds=settings.callback_retry_seconds,
    )
    while True:
        try:
            await worker.run_once()
        except sqlite3.OperationalError as exc:
            logger.warning(f"Callback queue unavailable: {type(exc).__name__}")
        await asyncio.sleep(1)
