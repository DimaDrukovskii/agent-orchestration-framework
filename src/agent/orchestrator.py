from typing import Optional, Dict, Any, List, Tuple
from src.services.llm import LLMService
from src.constants import AGENT_SYSTEM_PROMPT
from src.utils import logger


class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content
    
    def to_dict(self) -> Dict:
        return {"role": self.role, "content": self.content}


class CallOrchestrator:
    def __init__(self):
        self.llm = LLMService()
        self.sessions = {}  # session_id -> {caller_phone, messages: []}
        self.call_states = {}
    
    async def dialog(
        self,
        session_id: str,
        caller_phone: str,
        user_message: str,
        history: Optional[List[Dict]] = None
    ) -> Tuple[str, Dict[str, Any], List[Dict]]:
        """
        Многошаговый диалог.
        
        Returns: (agent_response, action_result, full_history)
        """
        try:
            logger.info(f"[{session_id}] Dialog: user says '{user_message}'")
            
            # 1. Initialize or get session
            if session_id not in self.sessions:
                self.sessions[session_id] = {
                    "caller_phone": caller_phone,
                    "messages": []
                }
            
            session = self.sessions[session_id]
            
            # 2. Build message history for Claude
            messages = session["messages"].copy() if not history else history.copy()
            messages.append({"role": "user", "content": user_message})
            
            # 3. Get Claude response
            agent_response = await self.llm.run_agent_dialog(
                messages=messages,
                caller_phone=caller_phone,
                system_prompt=AGENT_SYSTEM_PROMPT
            )
            
            # 4. Determine action (transfer/escalate/resolve)
            action_result = await self.llm.determine_action_from_dialog(
                messages=messages,
                agent_response=agent_response
            )
            
            # 5. Update session history
            session["messages"] = messages
            session["messages"].append({"role": "assistant", "content": agent_response})
            
            # 6. Save to DB if action is terminal (transfer/escalate/resolve)
            if action_result.get("action") in ["transfer", "escalate", "resolve"]:
                await self.save_dialog(
                    session_id=session_id,
                    caller_phone=caller_phone,
                    messages=session["messages"],
                    action=action_result.get("action"),
                    department=action_result.get("department")
                )
            
            return agent_response, action_result, session["messages"]
        
        except Exception as e:
            logger.error(f"[{session_id}] Dialog error: {str(e)}")
            raise
    
    async def get_dialog_history(self, session_id: str) -> List[Dict]:
        """Получить историю диалога"""
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")
        return self.sessions[session_id]["messages"]
    
    async def save_dialog(
        self,
        session_id: str,
        caller_phone: str,
        messages: List[Dict],
        action: str,
        department: Optional[str] = None
    ):
        """Сохранить завершённый диалог в БД"""
        try:
            from src.services.storage import StorageService
            storage = StorageService()
            
            # Форматировать сообщения
            transcript = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}"
                for msg in messages
            ])
            
            await storage.save_call(
                call_id=session_id,
                caller_phone=caller_phone,
                transcript=transcript,
                agent_response=messages[-1]["content"] if messages else "",
                action=action
            )
            logger.info(f"[{session_id}] Dialog saved to DB")
        except Exception as e:
            logger.error(f"[{session_id}] Save error: {str(e)}")
    
    async def process_agent_only(
        self,
        transcript: str,
        caller_phone: str,
        call_id: str
    ) -> str:
        """
        Only run Claude agent without STT/storage.
        Used for Twilio flow where STT already done.
        """
        try:
            logger.info(f"[{call_id}] Running agent on transcript")
            agent_response = await self.llm.run_agent(
                transcript=transcript,
                caller_phone=caller_phone,
                context=None,
                system_prompt=AGENT_SYSTEM_PROMPT
            )
            return agent_response
        except Exception as e:
            logger.error(f"[{call_id}] Agent error: {str(e)}")
            raise
    
    async def determine_action(self, agent_response: str, transcript: str) -> Dict[str, Any]:
        """
        Use Claude to determine action.
        Returns: {action, department, reason, confidence}
        """
        return await self.llm.determine_action(agent_response, transcript)
    
    async def save_call(
        self,
        call_id: str,
        caller_phone: str,
        transcript: str,
        agent_response: str,
        action: str,
        department: str = "",
        confidence: float = 0.0
    ):
        """Save call to database"""
        from src.services.storage import StorageService
        storage = StorageService()
        
        try:
            await storage.save_call(
                call_id=call_id,
                caller_phone=caller_phone,
                transcript=transcript,
                agent_response=agent_response,
                action=action
            )
            logger.info(f"[{call_id}] Call saved to DB")
        except Exception as e:
            logger.error(f"[{call_id}] Save error: {str(e)}")
            # Don't raise - don't break call flow if DB fails
    
    async def get_status(self, call_id: str) -> Dict[str, Any]:
        """Get call processing status"""
        if call_id not in self.call_states:
            raise ValueError(f"Call {call_id} not found")
        return self.call_states[call_id]
