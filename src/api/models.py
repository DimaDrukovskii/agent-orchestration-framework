from pydantic import BaseModel
from typing import Optional


class CallRequest(BaseModel):
    call_id: str
    audio_url: str
    caller_phone: str
    context: Optional[str] = None


class CallResponse(BaseModel):
    call_id: str
    transcript: str
    agent_response: str
    action: str  # transfer, escalate, resolve
    confidence: float
    metadata: Optional[dict] = None
