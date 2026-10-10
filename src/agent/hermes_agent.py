"""
Hermes-агент — ReAct-цикл поверх tool use. Два провайдера, переключаются
settings.llm_provider:
  - "anthropic" — прямой Claude API (tool_use/tool_result блоки)
  - "polza"     — OpenAI-совместимый шлюз polza.ai (tool_calls, role="tool")

В отличие от orchestrator.py (один проход: запрос → ответ), этот агент может
САМ решить вызвать tool (посмотреть базу знаний, проверить счёт), получить
результат и только потом ответить — как живой оператор, который сверяется
со своей системой по ходу разговора.
"""

import json
from typing import List, Dict, Any, AsyncGenerator
from src.config import settings
from src.agent.tools import TOOLS_SCHEMA, TOOLS_SCHEMA_OPENAI, execute_tool
from src.agent.hermes_prompts import HERMES_SYSTEM_PROMPT
from src.utils import logger, mask_phone

MAX_TOOL_ITERATIONS = 4  # защита от зацикливания агента
CLOSING_PROMPT = "Коротко скажи абоненту, что происходит дальше (своими словами, не как бот)."


class HermesAgent:
    def __init__(self):
        self.provider = settings.llm_provider

        if self.provider == "polza":
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(
                api_key=settings.polza_api_key,
                base_url=settings.polza_base_url,
                timeout=settings.agent_timeout_seconds,
            )
            self.model = settings.polza_model
        elif self.provider == "anthropic":
            from anthropic import AsyncAnthropic

            self.client = AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                timeout=settings.agent_timeout_seconds,
            )
            self.model = settings.anthropic_model
        else:
            raise ValueError(f"Неизвестный LLM_PROVIDER: {self.provider}")

    async def run(
        self,
        messages: List[Dict[str, Any]],
        caller_phone: str
    ) -> Dict[str, Any]:
        """
        Полный ReAct-цикл: модель думает → при необходимости вызывает tool →
        получает результат → продолжает думать → в итоге даёт финальный текст.

        Returns:
            {
                "response": str,
                "messages": [...],
                "pending_transfer": dict|None
            }
        """
        if self.provider == "polza":
            return await self._run_openai(messages, caller_phone)
        return await self._run_anthropic(messages, caller_phone)

    # ---- Anthropic (Claude напрямую) ----

    async def _run_anthropic(
        self,
        messages: List[Dict[str, Any]],
        caller_phone: str
    ) -> Dict[str, Any]:
        working_messages = messages.copy()
        pending_transfer = None

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.info(f"Hermes iteration {iteration + 1}, phone={mask_phone(caller_phone)}")

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=600,
                temperature=settings.llm_temperature,
                system=HERMES_SYSTEM_PROMPT,
                tools=TOOLS_SCHEMA,
                messages=working_messages
            )

            text_parts = [b.text for b in response.content if b.type == "text"]
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if response.stop_reason != "tool_use":
                final_text = "".join(text_parts).strip()
                working_messages.append({"role": "assistant", "content": response.content})
                return {
                    "response": final_text,
                    "messages": working_messages,
                    "pending_transfer": pending_transfer
                }

            working_messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_block in tool_use_blocks:
                logger.info(f"Tool call: {tool_block.name}({tool_block.input})")
                result = execute_tool(tool_block.name, tool_block.input)

                if tool_block.name == "transfer_to_department":
                    pending_transfer = result

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": str(result)
                })

            working_messages.append({"role": "user", "content": tool_results})

            if pending_transfer:
                break

        closing = await self.client.messages.create(
            model=self.model,
            max_tokens=300,
            temperature=settings.llm_temperature,
            system=HERMES_SYSTEM_PROMPT,
            messages=working_messages + [{"role": "user", "content": CLOSING_PROMPT}]
        )
        final_text = "".join(b.text for b in closing.content if b.type == "text").strip()
        working_messages.append({"role": "assistant", "content": final_text})

        return {
            "response": final_text,
            "messages": working_messages,
            "pending_transfer": pending_transfer
        }

    # ---- OpenAI-совместимый шлюз (polza.ai и аналогичные) ----

    async def _run_openai(
        self,
        messages: List[Dict[str, Any]],
        caller_phone: str
    ) -> Dict[str, Any]:
        """
        Тот же ReAct-цикл, но под Chat Completions API: схема тулов —
        TOOLS_SCHEMA_OPENAI (src/agent/tools.py), вызовы — message.tool_calls,
        результаты — отдельные сообщения с role="tool".
        """
        working_messages = messages.copy()
        pending_transfer = None

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.info(f"Hermes iteration {iteration + 1}, phone={mask_phone(caller_phone)}")

            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=600,
                temperature=settings.llm_temperature,
                messages=[{"role": "system", "content": HERMES_SYSTEM_PROMPT}] + working_messages,
                tools=TOOLS_SCHEMA_OPENAI,
            )

            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason != "tool_calls" or not msg.tool_calls:
                final_text = (msg.content or "").strip()
                working_messages.append({"role": "assistant", "content": final_text})
                return {
                    "response": final_text,
                    "messages": working_messages,
                    "pending_transfer": pending_transfer
                }

            working_messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })

            for tool_call in msg.tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                logger.info(f"Tool call: {tool_call.function.name}({tool_input})")
                result = execute_tool(tool_call.function.name, tool_input)

                if tool_call.function.name == "transfer_to_department":
                    pending_transfer = result

                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })

            if pending_transfer:
                break

        closing = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=300,
            temperature=settings.llm_temperature,
            messages=(
                [{"role": "system", "content": HERMES_SYSTEM_PROMPT}]
                + working_messages
                + [{"role": "user", "content": CLOSING_PROMPT}]
            ),
        )
        final_text = (closing.choices[0].message.content or "").strip()
        working_messages.append({"role": "assistant", "content": final_text})

        return {
            "response": final_text,
            "messages": working_messages,
            "pending_transfer": pending_transfer
        }

    async def run_streaming(
        self,
        messages: List[Dict[str, Any]],
        caller_phone: str
    ) -> AsyncGenerator[str, None]:
        """
        Streaming-версия для "живого" ощущения — текст приходит кусками.
        Упрощённая версия БЕЗ tool calls в стриме — для ответов, требующих
        tool (цифры, инструкции), используй run().
        """
        if self.provider == "polza":
            stream = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=600,
                temperature=settings.llm_temperature,
                messages=[{"role": "system", "content": HERMES_SYSTEM_PROMPT}] + messages,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
            return

        async with self.client.messages.stream(
            model=self.model,
            max_tokens=600,
            temperature=settings.llm_temperature,
            system=HERMES_SYSTEM_PROMPT,
            messages=messages
        ) as stream:
            async for text in stream.text_stream:
                yield text
