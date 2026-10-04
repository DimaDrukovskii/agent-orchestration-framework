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
    
    async def get_call(self, call_id: str) -> Optional[dict]:
        """Retrieve call record"""
        try:
            response = self.client.table("calls").select("*").eq("call_id", call_id).execute()
            return response.data[0] if response.data else None
        
        except Exception as e:
            logger.error(f"Error retrieving call: {str(e)}")
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
