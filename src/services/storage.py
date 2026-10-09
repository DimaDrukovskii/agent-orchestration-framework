from supabase import create_client, Client
from src.config import settings
from src.utils import logger
from typing import Optional, List


class StorageService:
    def __init__(self):
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_key
        )
    
    async def save_call(
        self,
        call_id: str,
        caller_phone: str,
        transcript: str,
        agent_response: str,
        action: str
    ) -> dict:
        """Save call record to database"""
        try:
            logger.info(f"Saving call {call_id} to database")
            
            data = {
                "call_id": call_id,
                "caller_phone": caller_phone,
                "transcript": transcript,
                "agent_response": agent_response,
                "action": action,
                "created_at": "now()"
            }
            
            response = self.client.table("calls").insert(data).execute()
            return response.data[0] if response.data else {}
        
        except Exception as e:
            logger.error(f"Error saving call: {str(e)}")
            raise
    
    async def upsert_call(
        self,
        call_id: str,
        caller_phone: str,
        transcript: str,
        agent_response: str,
        action: str
    ) -> dict:
        """
        Записать/обновить звонок по call_id (= session_id у Hermes).

        В отличие от save_call (чистый insert, одна запись на звонок),
        Hermes пишет на каждую реплику диалога одним и тем же call_id —
        поэтому upsert, а не insert, иначе второй же вызов упадёт на
        уникальном индексе calls.call_id.
        """
        try:
            data = {
                "call_id": call_id,
                "caller_phone": caller_phone,
                "transcript": transcript,
                "agent_response": agent_response,
                "action": action,
                "updated_at": "now()",
            }

            response = self.client.table("calls").upsert(data, on_conflict="call_id").execute()
            return response.data[0] if response.data else {}

        except Exception as e:
            logger.error(f"Error upserting call: {str(e)}")
            raise

    async def get_call(self, call_id: str) -> Optional[dict]:
        """Retrieve call record"""
        try:
            response = self.client.table("calls").select("*").eq("call_id", call_id).execute()
            return response.data[0] if response.data else None
        
        except Exception as e:
            logger.error(f"Error retrieving call: {str(e)}")
            return None
    
    async def get_client_by_api_key_hash(self, api_key_hash: str) -> Optional[dict]:
        """
        Структура под мультиарендность (docs/SCALING_PLAN.md §3, api_clients).
        Не вызывается из src/api/auth.py сейчас — единый AGENT_API_KEY
        продолжает работать как основная проверка. Это следующий шаг:
        когда появится больше одного бизнеса, auth.py переключится на
        поиск здесь вместо settings.agent_api_key.
        """
        try:
            response = (
                self.client.table("api_clients")
                .select("*")
                .eq("api_key_hash", api_key_hash)
                .eq("is_active", True)
                .execute()
            )
            return response.data[0] if response.data else None

        except Exception as e:
            logger.error(f"Error retrieving api_client: {str(e)}")
            return None

    async def get_caller_history(self, caller_phone: str, limit: int = 10) -> List[dict]:
        """Get call history for a caller"""
        try:
            response = (
                self.client.table("calls")
                .select("*")
                .eq("caller_phone", caller_phone)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return response.data
        
        except Exception as e:
            logger.error(f"Error retrieving caller history: {str(e)}")
            return []
