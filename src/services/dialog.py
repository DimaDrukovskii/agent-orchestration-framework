"""
Диалог: один ход разговора, общий для всех каналов (виджет через HTTP, телефон через Twilio).

run_turn(): история сессии из кэша -> агент (модель, база знаний, инструменты) -> история обратно ->
запись звонка -> доставка чата. Каналы (src/api/hermes_routes.py, src/telephony/) только переводят
свой формат в вызов run_turn и обратно, логики диалога у них нет.
"""

from typing import Any, Dict, List, Optional

from src.agent.hermes_agent import HermesAgent
from src.config import settings
from src.services.cache import CacheService
from src.utils import logger, mask_phone

hermes = HermesAgent()
_agents: Dict[str, HermesAgent] = {}  # профили кроме дефолтного, создаются по требованию


def agent_for(model_profile: Optional[str]) -> HermesAgent:
    """"default"/None — LLM_PROVIDER, "local" — локальный сервер."""
    if model_profile in (None, "default"):
        return hermes
    if model_profile not in _agents:
        _agents[model_profile] = HermesAgent(model_profile)
    return _agents[model_profile]
cache = CacheService()

SESSION_TTL = settings.hermes_session_ttl_seconds


def _session_key(session_id: str) -> str:
    return f"hermes_session:{session_id}"




def _to_plain(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Привести историю к JSON-сериализуемому виду перед записью в Redis.

    HermesAgent.run() добавляет в историю response.content прямо как
    объекты Anthropic SDK (TextBlock/ToolUseBlock) — это ок для памяти
    процесса, но не для json.dumps в CacheService. Переводим в plain dict,
    Anthropic messages API их принимает обратно так же, как объекты.
    """
    plain: List[Dict[str, Any]] = []
    for msg in messages:
        # остальные поля (tool_calls, tool_call_id) нужны провайдеру на следующей реплике
        item = {key: value for key, value in msg.items() if key != "content"}
        content = msg.get("content")
        if isinstance(content, list):
            content = [block.model_dump() if hasattr(block, "model_dump") else block for block in content]
        item["content"] = content
        plain.append(item)
    return plain


def _extract_transcript(messages: List[Dict[str, Any]]) -> str:
    """Читаемый транскрипт для БД — только текстовые реплики, без tool-шагов."""
    lines = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
                for block in content
                if (isinstance(block, dict) and block.get("type") == "text")
                or getattr(block, "type", None) == "text"
            )
        else:
            text = ""
        if text.strip():
            lines.append(f"{msg['role'].upper()}: {text.strip()}")
    return "\n".join(lines)


async def _get_session(session_id: str) -> List[Dict[str, Any]]:
    cached = await cache.get(_session_key(session_id))
    if cached:
        return cached.get("messages", [])
    return []


async def _save_session(session_id: str, caller_phone: str, messages: List[Dict[str, Any]]) -> None:
    await cache.set(
        _session_key(session_id),
        {"caller_phone": caller_phone, "messages": _to_plain(messages)},
        ttl=SESSION_TTL
    )


async def _persist_turn(
    session_id: str,
    caller_phone: str,
    messages: List[Dict[str, Any]],
    agent_response: str,
    pending_transfer: Optional[Dict[str, Any]]
) -> bool:
    """
    Записать текущее состояние разговора в Supabase (та же таблица `calls`,
    что уже использует CallOrchestrator — не вводим новую схему).

    Ошибка персиста не роняет ответ абоненту, но возвращается как persisted=False,
    чтобы вызывающая сторона видела, что история не сохранилась.
    """
    persisted = True
    try:
        from src.services.storage import StorageService
        storage = StorageService()
        await storage.upsert_call(
            call_id=session_id,
            caller_phone=caller_phone,
            transcript=_extract_transcript(messages),
            agent_response=agent_response,
            action="transfer" if pending_transfer else "in_progress"
        )
    except Exception as e:
        persisted = False
        logger.error(f"[{session_id}] Hermes persist error: {str(e)}")

    if pending_transfer:
        try:
            from src.services.telegram import send_message
            await send_message(
                settings.telegram_chat_id,
                f"Звонок от {mask_phone(caller_phone)} переведён на "
                f"{pending_transfer.get('department', '?')}: {pending_transfer.get('reason', '')}",
            )
        except Exception as e:
            logger.error(f"[{session_id}] Telegram notify error: {str(e)}")

    return persisted


async def _deliver_chat(session_id: str, messages: List[str]) -> None:
    """Доставить сообщения чата во внешний канал. Новый канал — ещё одна ветка здесь."""
    if not messages or not settings.telegram_chat_id:
        return
    try:
        from src.services.telegram import send_message
        for text in messages:
            await send_message(settings.telegram_chat_id, text)
    except Exception as e:
        logger.error(f"[{session_id}] Chat delivery error: {type(e).__name__}")


async def run_turn(
    session_id: str,
    caller_phone: str,
    user_message: str,
    model_profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Один ход диалога. Возвращает {response, transfer, chat, links, persisted}; ошибки провайдера и хранилища не глотает."""
    history = await _get_session(session_id)
    history.append({"role": "user", "content": user_message})

    result = await agent_for(model_profile).run(messages=history, caller_phone=caller_phone)

    await _save_session(session_id, caller_phone, result["messages"])
    persisted = await _persist_turn(
        session_id=session_id,
        caller_phone=caller_phone,
        messages=result["messages"],
        agent_response=result["response"],
        pending_transfer=result["pending_transfer"],
    )

    chat = result.get("chat", [])
    await _deliver_chat(session_id, chat)

    return {
        "response": result["response"],
        "transfer": result["pending_transfer"],
        "chat": chat,
        "links": result.get("links", []),
        "persisted": persisted,
    }


INTERRUPT_MARK = " [абонент перебил]"


async def run_turn_stream(
    session_id: str,
    caller_phone: str,
    user_message: str,
    model_profile: Optional[str] = None,
):
    """
    Ход диалога потоком: события агента (фразы, вызовы инструментов) уходят каналу сразу, а в конце,
    когда ответ закончен, история сохраняется, звонок записывается и доставляется чат. Последнее событие:
    {"type": "done", **turn} в том же виде, что у run_turn(). Если канал оборвал поток (абонент перебил),
    ничего не сохраняется: канал сам вызовет mark_interrupted() с тем, что абонент успел услышать.
    """
    history = await _get_session(session_id)
    history.append({"role": "user", "content": user_message})
    async for event in agent_for(model_profile).run_stream(history, caller_phone):
        if event["type"] != "done":
            yield event
            continue
        result = event["result"]
        await _save_session(session_id, caller_phone, result["messages"])
        persisted = await _persist_turn(
            session_id=session_id,
            caller_phone=caller_phone,
            messages=result["messages"],
            agent_response=result["response"],
            pending_transfer=result["pending_transfer"],
        )
        chat = result.get("chat", [])
        await _deliver_chat(session_id, chat)
        yield {
            "type": "done",
            "response": result["response"],
            "transfer": result["pending_transfer"],
            "chat": chat,
            "links": result.get("links", []),
            "persisted": persisted,
        }


async def mark_interrupted(
    session_id: str, caller_phone: str, user_message: str, spoken: str, committed: bool
) -> None:
    """
    Абонент перебил агента: в историю должно попасть только то, что он реально услышал.
      committed=True   ход уже сохранён целиком: последняя реплика агента заменяется услышанной частью;
      committed=False  ход оборван до сохранения: добавляются реплика абонента и услышанная часть ответа
                       (если агент ничего не успел сказать, не добавляется ничего, реплика останется в ожидании).
    """
    history = await _get_session(session_id)
    note = (spoken.strip() + INTERRUPT_MARK).strip()
    if committed:
        for index in range(len(history) - 1, -1, -1):
            msg = history[index]
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), str) and not msg.get("tool_calls"):
                history[index] = {**msg, "content": note}
                break
    elif spoken.strip():
        history.extend([{"role": "user", "content": user_message}, {"role": "assistant", "content": note}])
    else:
        return
    await _save_session(session_id, caller_phone, history)
