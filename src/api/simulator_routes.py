"""Authenticated browser simulator for the course profile."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import verify_api_key
from src.api.callback_routes import require_simulator
from src.config import settings
from src.services.call_simulator import CallSimulator, SimulationConflict
from src.services.callbacks import CallbackQueue
from src.tenant import tenant_dir

router = APIRouter(prefix="/simulator", dependencies=[Depends(verify_api_key), Depends(require_simulator)])


class Turn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    heard: str | None = Field(default=None, max_length=2000)


def run(operation: str, *args, **kwargs):
    if not (tenant_dir() / "qualification.json").is_file():
        raise HTTPException(404, "Для профиля не настроен симулятор квалификации")
    simulator = CallSimulator(settings.sqlite_path)
    try:
        return getattr(simulator, operation)(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "Звонок не найден") from exc
    except SimulationConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/calls")
async def incoming():
    return await asyncio.to_thread(run, "start")


@router.get("/calls/{call_id}")
async def call(call_id: str):
    return await asyncio.to_thread(run, "get", call_id)


@router.post("/calls/{call_id}/turn")
async def turn(call_id: str, request: Turn):
    if not settings.simulator_use_hermes:
        return await asyncio.to_thread(run, "turn", call_id, request.text, request.heard)

    from src.services.dialog import agent_for

    state = await asyncio.to_thread(run, "get", call_id)
    try:
        messages = CallSimulator.model_messages(state, request.text, request.heard)
    except SimulationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    notes = json.dumps(state["facts"], ensure_ascii=False) if state["facts"] else ""
    result = await agent_for("hermes_agent").run(messages, caller_phone="", notes=notes)
    return await asyncio.to_thread(
        run, "turn", call_id, request.text, request.heard, expected=state, model_result=result,
    )


@router.post("/calls/{call_id}/finish")
async def finish(call_id: str):
    return await asyncio.to_thread(run, "finish", call_id)


@router.post("/callbacks/{job_id}/connect")
async def connect_callback(job_id: str):
    queue = await asyncio.to_thread(CallbackQueue, settings.sqlite_path)
    job = await asyncio.to_thread(queue.get, job_id)
    if not job:
        raise HTTPException(404, "Обратный звонок не найден")
    if (job["status"], job["outcome"]) != ("finished", "answered"):
        raise HTTPException(409, "Абонент ещё не ответил")
    return await asyncio.to_thread(run, "start", job["source_call_id"], f"out-{job_id}")
