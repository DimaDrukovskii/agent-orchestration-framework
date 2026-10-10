"""
Hermes-агент — ReAct-цикл поверх tool use. Два провайдера, переключаются
settings.llm_provider:
  - "anthropic" — прямой Claude API (tool_use/tool_result блоки)
  - "polza"     — OpenAI-совместимый шлюз polza.ai (tool_calls, role="tool")

Агент может САМ решить вызвать tool (посмотреть базу знаний, проверить счёт), получить
результат и только потом ответить — как живой оператор, который сверяется
со своей системой по ходу разговора.
"""

import json
import re
from typing import List, Dict, Any, AsyncGenerator, Optional, Tuple
from src.config import settings
from src.services.analytics import analytics
from src.agent.tools import TOOLS_SCHEMA, TOOLS_SCHEMA_OPENAI, execute_tool, kb_context, load_site_map, open_section_tool
from src.agent.escalation import escalation_intent
from src.agent.hermes_prompts import HERMES_CHAT_RULE, HERMES_INTERRUPT_RULE, HERMES_LINKS_RULE, HERMES_SYSTEM_PROMPT
from src.utils import logger, mask_phone

# провайдеры с OpenAI-совместимым API (tool_calls, role="tool")
OPENAI_COMPAT_PROVIDERS = ("polza", "local", "hermes_agent")
MAX_TOOL_ITERATIONS = 4  # защита от зацикливания агента
CLOSING_PROMPT = "Коротко скажи абоненту, что происходит дальше (своими словами, не как бот)."


def trim_history(messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """
    Оставить последние `limit` сообщений, не разрывая цепочку вызова инструмента:
    срез всегда начинается с реплики абонента (role="user"), поэтому assistant
    с tool_calls и его role="tool" ответы остаются вместе. Исходный список не меняется.
    """
    if limit <= 0 or len(messages) <= limit:
        return list(messages)
    start = len(messages) - limit
    while start < len(messages) and messages[start].get("role") != "user":
        start += 1
    if start >= len(messages):  # в хвосте нет реплики абонента — не обрезаем вслепую
        return list(messages)
    return list(messages[start:])


TURN_CAP_REPLY = (
    "Мы уже долго разговариваем, давайте я соединю вас с оператором, "
    "он разберёт всё подробно. Соединяю?"
)
KB_BLOCK = "\n\n[СПРАВКА ИЗ БАЗЫ ЗНАНИЙ — отвечай по ней, ничего не выдумывай]\n{snippet}"


def with_kb_context(messages: List[Dict[str, Any]], snippet: str) -> List[Dict[str, Any]]:
    """Копия сообщений, где к последней реплике абонента добавлена справка. Исходный список не меняется."""
    if not snippet:
        return list(messages)
    result = list(messages)
    for idx in range(len(result) - 1, -1, -1):
        msg = result[idx]
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            result[idx] = {**msg, "content": msg["content"] + KB_BLOCK.format(snippet=snippet)}
            break
    return result


def recent_user_text(messages: List[Dict[str, Any]], count: int = 2) -> str:
    """Последние реплики абонента одной строкой: в «а сколько там интернета?» тема названа в предыдущей."""
    texts = [m["content"] for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return " ".join(t.split(" [СПРАВКА")[0] for t in texts[-count:])


def count_user_turns(messages: List[Dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def log_usage(response: Any, model: str) -> None:
    """Токены запроса в лог: по ним видно, куда уходят деньги (cached — дешёвые)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    logger.info(
        f"LLM usage model={model} in={getattr(usage, 'prompt_tokens', 0)} "
        f"cached={cached} out={getattr(usage, 'completion_tokens', 0)}"
    )
    analytics.record(
        "llm",
        getattr(usage, "cost_rub", 0),  # реальная цена запроса от провайдера
        model=model,
        tokens_in=getattr(usage, "prompt_tokens", 0),
        tokens_out=getattr(usage, "completion_tokens", 0),
        tokens_cached=cached,
    )


SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)]*\s+")


def pop_sentences(buffer: str) -> Tuple[List[str], str]:
    """Отделить от потока текста законченные предложения: (готовые, недописанный хвост)."""
    parts = SENTENCE_END.split(buffer)
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


def new_turn_state() -> Dict[str, Any]:
    return {"pending_transfer": None, "chat": [], "links": []}


def apply_tool(name: str, tool_input: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """Выполнить инструмент и учесть его побочный результат (перевод, чат, ссылка) в состоянии реплики."""
    logger.info(f"Tool call: {name}({tool_input})")
    result = execute_tool(name, tool_input)
    if name == "transfer_to_department":
        state["pending_transfer"] = result
    elif name == "send_to_chat" and result.get("text"):
        state["chat"].append(result["text"])
    elif name == "show_section" and result.get("url"):
        state["links"].append({"title": result["title"], "url": result["url"]})
    return result


def escalation_result(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Очевидная просьба о человеке в последней реплике: итог хода без обращения к модели."""
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
    found = escalation_intent(last)
    if not found:
        return None
    department, reason, phrase = found
    state = new_turn_state()
    apply_tool("transfer_to_department", {"department": department, "reason": reason}, state)
    return {"response": phrase, "messages": messages + [{"role": "assistant", "content": phrase}],
            "pending_transfer": state["pending_transfer"], "chat": [], "links": []}


class HermesAgent:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or settings.llm_provider

        if self.provider in OPENAI_COMPAT_PROVIDERS:
            from openai import AsyncOpenAI

            if self.provider == "hermes_agent":
                # Настоящий Hermes Agent: свой цикл и свои инструменты (в том числе MCP), наши tools ему не шлём
                self.client = AsyncOpenAI(
                    api_key=settings.hermes_agent_api_key or "none",
                    base_url=settings.hermes_agent_base_url,
                    timeout=settings.hermes_agent_timeout_seconds,
                )
                self.model = settings.hermes_agent_model
            elif self.provider == "local":
                self.client = AsyncOpenAI(
                    api_key=settings.local_api_key,
                    base_url=settings.local_base_url,
                    timeout=settings.local_timeout_seconds,
                )
                self.model = settings.local_model
            else:
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

    def _extra_body(self) -> Dict[str, Any]:
        """Параметры Polza вне стандарта OpenAI: отключаем скрытые рассуждения (только polza)."""
        if self.provider == "polza" and settings.llm_reasoning_effort:
            return {"extra_body": {"reasoning": {"effort": settings.llm_reasoning_effort}}}
        return {}

    def system_message(self) -> Dict[str, Any]:
        """Системный промпт; для Claude на Polza помечается к кэшированию (read = 0.1x цены)."""
        prompt = (HERMES_SYSTEM_PROMPT + HERMES_INTERRUPT_RULE + (HERMES_CHAT_RULE if settings.chat_enabled else "")
                  + (HERMES_LINKS_RULE if load_site_map() else ""))
        cacheable = (
            settings.llm_prompt_cache
            and self.provider == "polza"
            and self.model.startswith("anthropic/")
        )
        if not cacheable:
            return {"role": "system", "content": prompt}
        return {
            "role": "system",
            "content": [{
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }],
        }

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
        if self.provider in OPENAI_COMPAT_PROVIDERS:
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
                tools=[t for t in TOOLS_SCHEMA if settings.chat_enabled or t["name"] != "send_to_chat"],
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

    # ---- Потоковый ответ: фраза уходит абоненту, как только модель её сказала ----

    async def run_stream(
        self,
        messages: List[Dict[str, Any]],
        caller_phone: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Тот же цикл «модель, инструмент, модель», но ответ идёт потоком событий:
          {"type": "sentence", "text": ...}  законченная фраза, её можно озвучивать сразу
          {"type": "tool", "name": ...}      агент пошёл за данными (повод сказать «секунду»)
          {"type": "done", "result": {...}}  итог в том же виде, что у run()
        Так агент начинает говорить, не дожидаясь конца своего ответа, и может оговорить действие
        («сейчас посмотрю») до вызова инструмента. Провайдеры вне OpenAI-совместимых отдают ответ целиком.
        """
        if self.provider not in OPENAI_COMPAT_PROVIDERS:
            result = await self.run(messages, caller_phone)
            for sentence in pop_sentences(result["response"] + "\n")[0]:
                yield {"type": "sentence", "text": sentence}
            yield {"type": "done", "result": result}
            return

        working = messages.copy()
        state = new_turn_state()
        spoken: List[str] = []  # всё, что агент сказал за реплику (включая слова перед вызовом инструмента)

        if count_user_turns(working) > settings.llm_max_turns:
            working.append({"role": "assistant", "content": TURN_CAP_REPLY})
            yield {"type": "sentence", "text": TURN_CAP_REPLY}
            yield {"type": "done", "result": {"response": TURN_CAP_REPLY, "messages": working, "pending_transfer": None, "chat": [], "links": []}}
            return

        if self.provider == "hermes_agent":  # у Hermes нет нашего инструмента перевода: очевидные случаи решаем до него
            shortcut = escalation_result(working)
            if shortcut:
                yield {"type": "sentence", "text": shortcut["response"]}
                yield {"type": "done", "result": shortcut}
                return

        preretrieval = settings.llm_kb_preretrieval  # и для hermes_agent: справка подставляется в реплику, его инструментов не нужно
        snippet = ""
        if preretrieval:
            snippet = kb_context(recent_user_text(working))
        tools = [t for t in TOOLS_SCHEMA_OPENAI
                 if not (preretrieval and t["function"]["name"] == "search_knowledge_base")
                 and (settings.chat_enabled or t["function"]["name"] != "send_to_chat")]
        tools += open_section_tool()

        for iteration in range(MAX_TOOL_ITERATIONS):
            request: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": settings.llm_max_tokens,
                "temperature": settings.llm_temperature,
                "stream": True,
                "messages": [self.system_message()]
                + with_kb_context(trim_history(working, settings.llm_max_history_messages), snippet),
            }
            if self.provider != "hermes_agent" and not state["pending_transfer"]:
                request["tools"] = tools  # после перевода инструменты не нужны: модель только прощается
            extra = self._extra_body().get("extra_body", {})
            request["extra_body"] = {**extra, "stream_options": {"include_usage": True}}

            buffer, text, calls, finish = "", "", {}, None
            async for chunk in await self.client.chat.completions.create(**request):
                if getattr(chunk, "usage", None) is not None:
                    log_usage(chunk, self.model)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                finish = choice.finish_reason or finish
                for piece in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = piece.id or slot["id"]
                    if piece.function:
                        slot["name"] += piece.function.name or ""
                        slot["arguments"] += piece.function.arguments or ""
                if delta.content:
                    buffer += delta.content
                    text += delta.content
                    ready, buffer = pop_sentences(buffer)
                    for sentence in ready:
                        spoken.append(sentence)
                        yield {"type": "sentence", "text": sentence}
            if buffer.strip() and not calls:
                spoken.append(buffer.strip())
                yield {"type": "sentence", "text": buffer.strip()}
            elif buffer.strip():  # слова перед вызовом инструмента тоже произносим
                spoken.append(buffer.strip())
                yield {"type": "sentence", "text": buffer.strip()}

            if not calls:
                final = " ".join(spoken).strip()
                working.append({"role": "assistant", "content": final})
                yield {"type": "done", "result": {
                    "response": final, "messages": working, "pending_transfer": state["pending_transfer"],
                    "chat": state["chat"], "links": state["links"]}}
                return

            ordered = [calls[i] for i in sorted(calls)]
            working.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                    for c in ordered
                ],
            })
            for call in ordered:
                yield {"type": "tool", "name": call["name"]}
                try:
                    args = json.loads(call["arguments"] or "{}")
                except ValueError:
                    args = {}
                result = apply_tool(call["name"], args, state)
                working.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})

        final = " ".join(spoken).strip() or TURN_CAP_REPLY
        working.append({"role": "assistant", "content": final})
        yield {"type": "done", "result": {
            "response": final, "messages": working, "pending_transfer": state["pending_transfer"],
            "chat": state["chat"], "links": state["links"]}}

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
        working_messages = messages.copy()  # полная история: идёт в хранилище и транскрипт
        state = new_turn_state()  # перевод, чат и ссылки, которые накопили инструменты за реплику
        pending_transfer = None
        chat_messages: List[str] = state["chat"]  # что агент отправил абоненту в чат за эту реплику
        links: List[Dict[str, str]] = state["links"]  # разделы сайта, которые агент показал кнопками

        if count_user_turns(working_messages) > settings.llm_max_turns:
            logger.warning(f"Turn cap reached ({settings.llm_max_turns}), phone={mask_phone(caller_phone)}")
            working_messages.append({"role": "assistant", "content": TURN_CAP_REPLY})
            return {"response": TURN_CAP_REPLY, "messages": working_messages, "pending_transfer": None}

        if self.provider == "hermes_agent":
            shortcut = escalation_result(working_messages)
            if shortcut:
                return shortcut

        preretrieval = settings.llm_kb_preretrieval  # и для hermes_agent: справка подставляется в реплику, его инструментов не нужно
        snippet = ""
        if preretrieval:
            snippet = kb_context(recent_user_text(working_messages))
        tools = [t for t in TOOLS_SCHEMA_OPENAI
                 if not (preretrieval and t["function"]["name"] == "search_knowledge_base")
                 and (settings.chat_enabled or t["function"]["name"] != "send_to_chat")]
        tools += open_section_tool()

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.info(f"Hermes iteration {iteration + 1}, phone={mask_phone(caller_phone)}")

            request: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": settings.llm_max_tokens,
                "temperature": settings.llm_temperature,
                "messages": [self.system_message()]
                + with_kb_context(trim_history(working_messages, settings.llm_max_history_messages), snippet),
            }
            if self.provider != "hermes_agent":
                request["tools"] = tools
            request.update(self._extra_body())
            response = await self.client.chat.completions.create(**request)
            log_usage(response, self.model)

            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason != "tool_calls" or not msg.tool_calls:
                final_text = (msg.content or "").strip()
                working_messages.append({"role": "assistant", "content": final_text})
                return {
                    "response": final_text,
                    "messages": working_messages,
                    "pending_transfer": pending_transfer,
                    "chat": chat_messages,
                    "links": links,
                }

            working_messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })

            for tool_call in msg.tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                result = apply_tool(tool_call.function.name, tool_input, state)
                pending_transfer = state["pending_transfer"]

                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })

            if pending_transfer:
                break

        closing = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=settings.llm_closing_max_tokens,
            temperature=settings.llm_temperature,
            messages=(
                [self.system_message()]
                + trim_history(working_messages, settings.llm_max_history_messages)
                + [{"role": "user", "content": CLOSING_PROMPT}]
            ),
            **self._extra_body(),
        )
        final_text = (closing.choices[0].message.content or "").strip()
        working_messages.append({"role": "assistant", "content": final_text})

        return {
            "response": final_text,
            "messages": working_messages,
            "pending_transfer": pending_transfer,
            "chat": chat_messages,
            "links": links,
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
        if self.provider in OPENAI_COMPAT_PROVIDERS:
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
