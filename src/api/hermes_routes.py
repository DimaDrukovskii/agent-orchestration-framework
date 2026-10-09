from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from src.agent.hermes_agent import HermesAgent
from src.utils import logger

router = APIRouter()
hermes = HermesAgent()

# session_id -> messages[] (в проде — заменить на Redis)
_sessions: Dict[str, List[Dict[str, Any]]] = {}


class HermesDialogRequest(BaseModel):
    session_id: str
    caller_phone: str
    user_message: str


class HermesDialogResponse(BaseModel):
    session_id: str
    agent_response: str
    transfer: Optional[Dict[str, Any]] = None


@router.post("/hermes/dialog", response_model=HermesDialogResponse)
async def hermes_dialog(request: HermesDialogRequest):
    """
    Диалог с Hermes-агентом (с доступом к tools: база знаний, счёт, перевод).
    Сервер сам хранит историю по session_id — клиенту не нужно её передавать.
    """
    try:
        history = _sessions.get(request.session_id, [])
        history.append({"role": "user", "content": request.user_message})

        result = await hermes.run(messages=history, caller_phone=request.caller_phone)

        _sessions[request.session_id] = result["messages"]

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
        history = _sessions.get(request.session_id, [])
        history.append({"role": "user", "content": request.user_message})

        async def token_generator():
            full_response = ""
            async for chunk in hermes.run_streaming(messages=history, caller_phone=request.caller_phone):
                full_response += chunk
                yield chunk

            history.append({"role": "assistant", "content": full_response})
            _sessions[request.session_id] = history

        return StreamingResponse(token_generator(), media_type="text/plain")

    except Exception as e:
        logger.error(f"Hermes stream error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hermes/dialog/{session_id}/history")
async def get_history(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": _sessions[session_id]}
