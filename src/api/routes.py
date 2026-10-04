from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from src.agent.orchestrator import CallOrchestrator
from src.utils import logger

router = APIRouter()
orchestrator = CallOrchestrator()


class Message(BaseModel):
    role: str  # "user" или "assistant"
    content: str


class DialogRequest(BaseModel):
    session_id: str  # Уникальный ID сессии (можно phone)
    caller_phone: str  # Номер абонента
    user_message: str  # Что сказал/написал абонент
    history: Optional[List[Message]] = None  # История диалога


class DialogResponse(BaseModel):
    session_id: str
    agent_response: str
    action: str  # resolve, transfer, escalate
    department: Optional[str] = None
    confidence: float
    history: List[Message]


@router.post("/dialog", response_model=DialogResponse)
async def dialog(request: DialogRequest):
    """
    Многошаговый диалог с абонентом.
    
    Запрос:
    {
        "session_id": "call_123",
        "caller_phone": "+79999999999",
        "user_message": "Мне нужна помощь с интернетом",
        "history": [...]  # опционально
    }
    
    Ответ: агент говорит что-то + определяет action
    """
    try:
        logger.info(f"Dialog: {request.session_id} from {request.caller_phone}")
        logger.info(f"User: {request.user_message}")
        
        # Получить ответ от агента
        agent_response, action_result, messages = await orchestrator.dialog(
            session_id=request.session_id,
            caller_phone=request.caller_phone,
            user_message=request.user_message,
            history=request.history or []
        )
        
        logger.info(f"Agent: {agent_response}")
        logger.info(f"Action: {action_result.get('action')}")
        
        return DialogResponse(
            session_id=request.session_id,
            agent_response=agent_response,
            action=action_result.get("action", "resolve"),
            department=action_result.get("department"),
            confidence=action_result.get("confidence", 0.0),
            history=messages
        )
    
    except Exception as e:
        logger.error(f"Dialog error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dialog/history/{session_id}")
async def get_dialog_history(session_id: str):
    """Получить историю диалога"""
    try:
        history = await orchestrator.get_dialog_history(session_id)
        return {"session_id": session_id, "messages": history}
    except Exception as e:
        logger.error(f"Error getting history: {str(e)}")
        raise HTTPException(status_code=404, detail=str(e))
