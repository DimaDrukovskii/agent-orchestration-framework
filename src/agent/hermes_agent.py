"""
Hermes-агент — ReAct-цикл поверх Claude tool use.

В отличие от orchestrator.py (один проход: запрос → ответ),
этот агент может САМ решить вызвать tool (посмотреть базу знаний,
проверить счёт), получить результат и только потом ответить —
как живой оператор, который сверяется со своей системой по ходу разговора.
"""

from typing import List, Dict, Any, AsyncGenerator
from anthropic import AsyncAnthropic
from src.config import settings
from src.agent.tools import TOOLS_SCHEMA, execute_tool
from src.agent.hermes_prompts import HERMES_SYSTEM_PROMPT
from src.utils import logger, mask_phone

MAX_TOOL_ITERATIONS = 4  # защита от зацикливания агента


class HermesAgent:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = "claude-3-5-sonnet-20241022"

    async def run(
        self,
        messages: List[Dict[str, Any]],
        caller_phone: str
    ) -> Dict[str, Any]:
        """
        Полный ReAct-цикл: Claude думает → при необходимости вызывает tool →
        получает результат → продолжает думать → в итоге даёт финальный текстовый ответ.

        Returns:
            {
                "response": str,              # финальный текст для абонента
                "messages": [...],             # обновлённая история (с tool-шагами)
                "pending_transfer": dict|None  # если был вызван transfer_to_department
            }
        """
        working_messages = messages.copy()
        pending_transfer = None

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.info(f"Hermes iteration {iteration + 1}, phone={mask_phone(caller_phone)}")

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=600,
                system=HERMES_SYSTEM_PROMPT,
                tools=TOOLS_SCHEMA,
                messages=working_messages
            )

            # Собрать текст и tool_use блоки из ответа
            text_parts = [b.text for b in response.content if b.type == "text"]
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if response.stop_reason != "tool_use":
                # Claude закончил рассуждать и дал финальный ответ
                final_text = "".join(text_parts).strip()
                working_messages.append({"role": "assistant", "content": response.content})
                return {
                    "response": final_text,
                    "messages": working_messages,
                    "pending_transfer": pending_transfer
                }

            # Claude хочет вызвать один или несколько инструментов
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

            # Если это был transfer — не продолжаем крутить цикл, сразу формируем ответ
            if pending_transfer:
                break

        # Если дошли сюда — либо лимит итераций, либо был transfer:
        # просим Claude сформулировать финальную реплику абоненту
        closing = await self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=HERMES_SYSTEM_PROMPT,
            messages=working_messages + [{
                "role": "user",
                "content": "Коротко скажи абоненту, что происходит дальше (своими словами, не как бот)."
            }]
        )
        final_text = "".join(b.text for b in closing.content if b.type == "text").strip()
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
        Streaming-версия для "живого" ощущения — текст приходит кусками,
        как будто оператор печатает/говорит в реальном времени.

        ВНИМАНИЕ: со streaming и tool_use одновременно нужно аккуратно
        собирать блоки — здесь упрощённая версия БЕЗ tool calls в стриме.
        Для ответов, требующих tool (цифры, инструкции) — используй run().
        """
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=600,
            system=HERMES_SYSTEM_PROMPT,
            messages=messages
        ) as stream:
            async for text in stream.text_stream:
                yield text
