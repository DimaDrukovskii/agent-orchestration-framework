from anthropic import Anthropic
from src.config import settings
from src.utils import logger, mask_phone
from src.agent.prompts import MTS_SYSTEM_PROMPT, DETERMINE_ACTION_PROMPT
from typing import Optional
import json


class LLMService:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = "claude-3-5-sonnet-20241022"
    
    async def run_agent(
        self,
        transcript: str,
        caller_phone: str,
        context: Optional[str] = None,
        system_prompt: str = None
    ) -> str:
        """
        Run Claude agent to process call transcript (single message).
        Returns agent's response to caller.
        """
        try:
            logger.info(f"LLM: Processing call from {mask_phone(caller_phone)}")
            
            user_prompt = f"""Абонент: {caller_phone}
Контекст: {context or 'общий запрос'}

Транскрипт звонка:
{transcript}

Дай ответ на русском языке, чётко и профессионально."""
            
            message = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=system_prompt or MTS_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            
            response = message.content[0].text
            logger.info(f"LLM response: {len(response)} chars")
            return response
        
        except Exception as e:
            logger.error(f"LLMService error: {str(e)}")
            raise
    
    async def run_agent_dialog(
        self,
        messages: list,
        caller_phone: str,
        system_prompt: str = None
    ) -> str:
        """
        Run agent in dialog mode (multi-turn conversation).
        Messages format: [{"role": "user"|"assistant", "content": "..."}, ...]
        """
        try:
            logger.info(f"LLM Dialog: {len(messages)} messages from {mask_phone(caller_phone)}")
            
            message = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=system_prompt or MTS_SYSTEM_PROMPT,
                messages=messages
            )
            
            response = message.content[0].text
            logger.info(f"LLM Dialog response: {len(response)} chars")
            return response
        
        except Exception as e:
            logger.error(f"LLM Dialog error: {str(e)}")
            raise
    
    async def determine_action_from_dialog(self, messages: list, agent_response: str) -> dict:
        """
        Determine action based on dialog history.
        """
        try:
            logger.info("LLM: Determining action from dialog...")
            
            # Rebuild context for action determination
            dialog_text = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}"
                for msg in messages
            ])
            
            prompt = f"""Проанализируй диалог и определи статус звонка.

{dialog_text}

Ответь ТОЛЬКО JSON:
{{
    "action": "resolve|transfer|escalate",
    "department": "техподдержка|биллинг|продажи|менеджер",
    "reason": "одно предложение",
    "confidence": 0.75
}}"""
            
            message = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = message.content[0].text
            
            try:
                result = json.loads(response_text)
                logger.info(f"Action: {result.get('action')} ({result.get('confidence')})")
                return result
            except json.JSONDecodeError:
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                if start != -1 and end > start:
                    result = json.loads(response_text[start:end])
                    return result
                raise ValueError("Could not parse JSON")
        
        except Exception as e:
            logger.error(f"Error determining action: {str(e)}")
            return {
                "action": "resolve",
                "reason": "fallback",
                "confidence": 0.3
            }
    
    async def determine_action(self, response: str, transcript: str) -> dict:
        """
        Use Claude to classify: transfer/escalate/resolve
        """
        try:
            logger.info("LLM: Determining action...")
            
            prompt = DETERMINE_ACTION_PROMPT.format(
                transcript=transcript,
                agent_response=response
            )
            
            message = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            response_text = message.content[0].text
            
            # Extract JSON from response
            try:
                result = json.loads(response_text)
                logger.info(f"Action determined: {result['action']} ({result['confidence']})")
                return result
            except json.JSONDecodeError:
                # Try to find JSON in text
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                if start != -1 and end > start:
                    result = json.loads(response_text[start:end])
                    return result
                raise ValueError("Could not parse JSON response")
        
        except Exception as e:
            logger.error(f"Error determining action: {str(e)}")
            return {
                "action": "resolve",
                "reason": "fallback - could not determine",
                "confidence": 0.3
            }
