import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Literal, Optional, Dict, Any
from anthropic import APITimeoutError
from openai import APIStatusError, APITimeoutError as OpenAITimeoutError
from redis.exceptions import RedisError
from src.services import dialog
from src.api.auth import verify_api_key
from src.services.analytics import session_var
from src.utils import logger

router = APIRouter(dependencies=[Depends(verify_api_key)])


class HermesDialogRequest(BaseModel):
    session_id: str
    caller_phone: str
    user_message: str
    model_profile: Optional[Literal["default", "local"]] = None


class HermesDialogResponse(BaseModel):
    session_id: str
    agent_response: str
    transfer: Optional[Dict[str, Any]] = None
    chat: List[str] = []  # сообщения, которые агент отправил абоненту в чат (голос -> чат)
    links: List[Dict[str, str]] = []  # разделы сайта, которые агент показал кнопками: [{title, url}]
    persisted: bool = True


def _unavailable(code: str, message: str, retryable: bool = True) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": {"code": code, "message": message, "retryable": retryable}},
    )



@router.post("/hermes/dialog", response_model=HermesDialogResponse)
async def hermes_dialog(request: HermesDialogRequest):
    """
    Диалог с Hermes-агентом (с доступом к tools: база знаний, счёт, перевод).

    Сервер сам хранит историю по session_id (TTL — settings.hermes_session_ttl_seconds),
    клиенту не нужно передавать историю. Каждая реплика также пишется в журнал звонков (calls)
    для аналитики и аудита. Сама логика хода — src/services/dialog.py (run_turn).
    """
    session_var.set(request.session_id)  # расходы этого запроса запишутся на этот разговор
    try:
        turn = await dialog.run_turn(
            request.session_id, request.caller_phone, request.user_message, request.model_profile
        )
        return HermesDialogResponse(
            session_id=request.session_id,
            agent_response=turn["response"],
            transfer=turn["transfer"],
            chat=turn["chat"],
            links=turn["links"],
            persisted=turn["persisted"],
        )

    except (APITimeoutError, OpenAITimeoutError) as e:
        logger.error(f"[{request.session_id}] LLM timeout: {str(e)}")
        raise _unavailable("agent_timeout", "Агент не ответил вовремя")
    except (RedisError, sqlite3.Error) as e:
        logger.error(f"[{request.session_id}] Session store error: {str(e)}")
        raise _unavailable("session_store_unavailable", "Хранилище сессий недоступно")
    except APIStatusError as e:
        logger.error(f"[{request.session_id}] LLM provider error {e.status_code}: {str(e)}")
        if e.status_code == 403:
            raise _unavailable("model_not_allowed", "Модель агента не разрешена на ключе провайдера", retryable=False)
        if e.status_code == 402:
            raise _unavailable("insufficient_balance", "У ключа провайдера закончился лимит", retryable=False)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Hermes dialog error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hermes/dialog/stream")
async def hermes_dialog_stream(request: HermesDialogRequest):
    """
    Streaming-версия — для живого эффекта "печатает в реальном времени".
    Без tool calls (см. ограничение в HermesAgent.run_streaming).
    """
    try:
        history = await dialog._get_session(request.session_id)
        history.append({"role": "user", "content": request.user_message})

        async def token_generator():
            full_response = ""
            async for chunk in dialog.agent_for(request.model_profile).run_streaming(messages=history, caller_phone=request.caller_phone):
                full_response += chunk
                yield chunk

            history.append({"role": "assistant", "content": full_response})
            await dialog._save_session(request.session_id, request.caller_phone, history)
            await dialog._persist_turn(
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
    messages = await dialog._get_session(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": messages}
