from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from src.agent.hermes_agent import HermesAgent
from src.api.auth import verify_api_key
from src.config import settings
from src.services.cache import CacheService
from src.utils import logger

router = APIRouter(dependencies=[Depends(verify_api_key)])
hermes = HermesAgent()
cache = CacheService()

SESSION_TTL = settings.hermes_session_ttl_seconds


class HermesDialogRequest(BaseModel):
    session_id: str
    caller_phone: str
    user_message: str


class HermesDialogResponse(BaseModel):
    session_id: str
    agent_response: str
    transfer: Optional[Dict[str, Any]] = None


def _session_key(session_id: str) -> str:
    return f"hermes_session:{session_id}"


def _to_plain(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Привести историю к JSON-сериализуемому виду перед записью в Redis.

    hermes_agent.run() добавляет в historию response.content прямо как
    объекты Anthropic SDK (TextBlock/ToolUseBlock) — это ок для памяти
    процесса, но не для json.dumps в CacheService. Переводим в plain dict,
    Anthropic messages API их принимает обратно так же, как объекты.
    """
    plain: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            plain_content = []
            for block in content:
                if hasattr(block, "model_dump"):
                    plain_content.append(block.model_dump())
                else:
                    plain_content.append(block)
            plain.append({"role": msg["role"], "content": plain_content})
        else:
            plain.append({"role": msg["role"], "content": content})
    return plain


def _extract_transcript(messages: List[Dict[str, Any]]) -> str:
    """Читаемый транскрипт для БД — только текстовые реплики, без tool-шагов."""
    lines = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                for block in content
                if (isinstance(block, dict) and block.get("type") == "text")
                or getattr(block, "type", None) == "text"
            )
        else:
            text = ""
        if text.strip():
            lines.append(f"{msg['role'].upper()}: {text.strip()}")
    return "\n".join(lines)


async def _get_session(session_id: str) -> List[Dict[str, Any]]:
    cached = await cache.get(_session_key(session_id))
    if cached:
        return cached.get("messages", [])
    return []


async def _save_session(session_id: str, caller_phone: str, messages: List[Dict[str, Any]]) -> None:
    await cache.set(
        _session_key(session_id),
        {"caller_phone": caller_phone, "messages": _to_plain(messages)},
        ttl=SESSION_TTL
    )


async def _persist_turn(
    session_id: str,
    caller_phone: str,
    messages: List[Dict[str, Any]],
    agent_response: str,
    pending_transfer: Optional[Dict[str, Any]]
) -> None:
    """
    Записать текущее состояние разговора в Supabase (та же таблица `calls`,
    что уже использует CallOrchestrator — не вводим новую схему).

    Как и в CallOrchestrator.save_call: ошибка персиста не должна ронять
    ответ абоненту, поэтому ловим исключение здесь, а не даём ему всплыть.
    """
    try:
        from src.services.storage import StorageService
        storage = StorageService()
        await storage.upsert_call(
            call_id=session_id,
            caller_phone=caller_phone,
            transcript=_extract_transcript(messages),
            agent_response=agent_response,
            action="transfer" if pending_transfer else "in_progress"
        )
    except Exception as e:
        logger.error(f"[{session_id}] Hermes persist error: {str(e)}")


@router.post("/hermes/dialog", response_model=HermesDialogResponse)
async def hermes_dialog(request: HermesDialogRequest):
    """
    Диалог с Hermes-агентом (с доступом к tools: база знаний, счёт, перевод).

    Сервер сам хранит историю по session_id в Redis (TTL — settings.hermes_session_ttl_seconds),
    клиенту не нужно передавать историю. Каждая реплика также пишется в Supabase (calls)
    для аналитики и аудита.
    """
    try:
        history = await _get_session(request.session_id)
        history.append({"role": "user", "content": request.user_message})

        result = await hermes.run(messages=history, caller_phone=request.caller_phone)

        await _save_session(request.session_id, request.caller_phone, result["messages"])
        await _persist_turn(
            session_id=request.session_id,
            caller_phone=request.caller_phone,
            messages=result["messages"],
            agent_response=result["response"],
            pending_transfer=result["pending_transfer"]
        )

        return HermesDialogResponse(
            session_id=request.session_id,
            agent_response=result["response"],
            transfer=result["pending_transfer"]
        )

    except Exception as e:
        logger.error(f"Hermes dialog error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hermes/dialog/stream")
async def hermes_dialog_stream(request: HermesDialogRequest):
    """
    Streaming-версия — для живого эффекта "печатает в реальном времени".
    Без tool calls (см. ограничение в hermes_agent.run_streaming).
    """
    try:
        history = await _get_session(request.session_id)
        history.append({"role": "user", "content": request.user_message})

        async def token_generator():
            full_response = ""
            async for chunk in hermes.run_streaming(messages=history, caller_phone=request.caller_phone):
                full_response += chunk
                yield chunk

            history.append({"role": "assistant", "content": full_response})
            await _save_session(request.session_id, request.caller_phone, history)
            await _persist_turn(
                session_id=request.session_id,
                caller_phone=request.caller_phone,
                messages=history,
                agent_response=full_response,
                pending_transfer=None
            )

        return StreamingResponse(token_generator(), media_type="text/plain")

    except Exception as e:
        logger.error(f"Hermes stream error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hermes/dialog/{session_id}/history")
async def get_history(session_id: str):
    messages = await _get_session(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": messages}
