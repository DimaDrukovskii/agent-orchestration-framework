"""
Диалог: один ход разговора, общий для всех каналов (виджет через HTTP, телефон через Twilio).

run_turn(): история сессии из кэша -> агент (модель, база знаний, инструменты) -> история обратно ->
запись звонка -> доставка чата. Каналы (src/api/hermes_routes.py, src/telephony/) только переводят
свой формат в вызов run_turn и обратно, логики диалога у них нет.
"""

import asyncio
import re
from contextlib import aclosing
from typing import Any, Dict, List, Optional

from src.agent.hermes_agent import HermesAgent
from src.agent.tools import CALLER_PHONE_MARK
from src.config import settings
from src.services.session_guard import session_guard
from src.services.cache import CacheService
from src.services.qualification import build_card, extract_facts
from src.tenant import tenant_dir
from src.utils import logger, mask_phone

hermes = HermesAgent()
_agents: Dict[str, HermesAgent] = {}  # профили кроме дефолтного, создаются по требованию


def agent_for(model_profile: Optional[str]) -> HermesAgent:
    """"default"/None — LLM_PROVIDER, "hermes_agent" — отдельный Hermes сервер."""
    if model_profile in (None, "default"):
        return hermes
    if model_profile not in _agents:
        _agents[model_profile] = HermesAgent(model_profile)
    return _agents[model_profile]
cache = CacheService()

SESSION_TTL = settings.hermes_session_ttl_seconds
SAVE_FAILED_REPLY = (
    "Пока не удалось подтвердить сохранение вашего запроса. "
    "Пожалуйста, не считайте заявку принятой: попробуйте отправить её ещё раз."
)
_GREETING_PREFIX = re.compile(
    r'^\s*(?:(?:здравствуйте|здравствуй|привет(?:ствую)?|доброе\s+утро|добрый\s+(?:день|вечер))\b[!.,:;\s—-]*)+',
    re.IGNORECASE,
)


def _without_greeting(text: str) -> str:
    return _GREETING_PREFIX.sub('', text).strip()


def _normalize_reply(result: dict) -> None:
    result['response'] = _without_greeting(result['response']) or 'Слушаю вас.'
    for message in reversed(result['messages']):
        if message.get('role') == 'assistant' and isinstance(message.get('content'), str) and not message.get('tool_calls'):
            message['content'] = _without_greeting(message['content']) or 'Слушаю вас.'
            break


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


# ---- Заметки о звонке: память дольше окна истории ----------------------------------------------------
# Модель видит последние llm_max_history_messages сообщений. То, что выпало из окна (имя, семья, даты, договорённости),
# в фоне дописывается дешёвой моделью в короткие заметки, и они подставляются в каждую реплику.

_notes_tasks: Dict[str, "asyncio.Task"] = {}
NOTES_WAIT_SECONDS = 2.0


def _notes_key(session_id: str) -> str:
    return f"hermes_notes:{session_id}"


async def _get_notes(session_id: str) -> Dict[str, Any]:
    return (await cache.get(_notes_key(session_id))) or {"text": "", "covered": 0}


async def update_notes(session_id: str, messages: List[Dict[str, Any]]) -> None:
    """Заметки охватывают всё, что при следующей реплике выпадет из окна истории. Ошибки не мешают разговору."""
    try:
        notes = await _get_notes(session_id)
        upto = len(messages) - max(settings.llm_max_history_messages - 2, 2)
        if upto - notes["covered"] < 2:
            return
        text = await hermes.summarize_notes(notes["text"], messages[notes["covered"]:upto])
        await cache.set(_notes_key(session_id), {"text": text, "covered": upto}, ttl=SESSION_TTL)
    except Exception as e:
        logger.warning(f"call notes skipped: {type(e).__name__}")


def _schedule_notes(session_id: str, messages: List[Dict[str, Any]]) -> None:
    _notes_tasks[session_id] = asyncio.create_task(update_notes(session_id, messages))


async def _notes_for_turn(session_id: str) -> Dict[str, Any]:
    """Заметки для агента; если они ещё дописываются после прошлой реплики, ждём недолго."""
    task = _notes_tasks.get(session_id)
    if task and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), NOTES_WAIT_SECONDS)
        except (asyncio.TimeoutError, Exception):
            pass
    if task and task.done():
        _notes_tasks.pop(session_id, None)
    return {"notes": (await _get_notes(session_id))["text"]}


def with_caller_phone(card: Dict[str, str], caller_phone: str) -> Dict[str, str]:
    """«номер звонящего» в карточке -> номер, с которого идёт звонок. Исходная карточка не меняется."""
    if card.get("contact_phone", "").lower() != CALLER_PHONE_MARK or not caller_phone:
        return card
    return {**card, "contact_phone": caller_phone}


def _channel_context(extra: dict, caller_phone: str) -> dict:
    if not caller_phone:
        return {**extra, 'notes': extra.get('notes', '') +
                '\nКанал сайта: прямого телефонного перевода нет; транспорт не передаёт номер посетителя. '
                'Для обратного звонка используй продиктованный посетителем номер или спроси его, если ещё нет. '
                'Не используй «номер звонящего». '
                'Оформляй заявку менеджеру и не говори «соединяю».'}
    return extra


def _browser_handoff(result: dict, caller_phone: str) -> bool:
    if caller_phone or not result['pending_transfer']:
        return False
    contact = (result.get('handoff_card') or {}).get('contact_phone', '')
    result['response'] = ('Ваш запрос сохранён для менеджера. Контакт для связи указан в заявке.'
                          if sum(c.isdigit() for c in contact) >= 7
                          else 'Чтобы менеджер мог вам перезвонить, подскажите контактный номер.')
    _replace_final_response(result, result['response'])
    return True


def _replace_final_response(result: dict, response: str) -> None:
    result['response'] = response
    for message in reversed(result['messages']):
        if message.get('role') == 'assistant' and isinstance(message.get('content'), str) and not message.get('tool_calls'):
            message['content'] = result['response']
            break


async def _persist_turn(
    session_id: str,
    caller_phone: str,
    messages: List[Dict[str, Any]],
    agent_response: str,
    pending_transfer: Optional[Dict[str, Any]],
    handoff_card: Optional[Dict[str, str]] = None,
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
        from src.services.delivery import DeliveryOutbox
        storage = StorageService()
        card = with_caller_phone(handoff_card, caller_phone) if handoff_card else None
        if card and not caller_phone and sum(c.isdigit() for c in card.get('contact_phone', '')) < 7:
            raise ValueError('callback_contact_missing')
        notification = None
        if pending_transfer or card:
            message = f"Обращение {session_id}. "
            if pending_transfer:
                message += (f"Запрошен перевод на {pending_transfer.get('department', '?')}: "
                            f"{pending_transfer.get('reason', '')}. ")
            if card:
                message += "Карточка сохранена. " + "; ".join(f"{k}: {v}" for k, v in card.items())
            notification = DeliveryOutbox.notification(session_id, message)
        await storage.save_turn(
            call_id=session_id,
            caller_phone=caller_phone,
            transcript=_extract_transcript(messages),
            agent_response=agent_response,
            action="transfer" if pending_transfer else "in_progress",
            card=card,
            notification=notification,
        )
    except Exception as e:
        persisted = False
        logger.error(f"[{session_id}] Hermes persist error: {str(e)}")

    return persisted


async def _deliver_chat(session_id: str, messages: List[str]) -> None:
    """Доставить сообщения чата во внешний канал. Новый канал — ещё одна ветка здесь."""
    if not messages:
        return
    from src.services.delivery import DeliveryOutbox
    outbox = DeliveryOutbox()
    for text in messages:
        await outbox.enqueue(session_id, outbox.notification(session_id, text, category="chat"))


async def _finalize_call(session_id: str, caller_phone: str) -> Dict[str, Any]:
    """Persist a conservative result once the phone transport has actually ended."""
    from src.services.storage import StorageService

    storage = StorageService()
    previous = await storage.get_call(session_id)
    if previous and previous["action"] not in ("in_progress", "transfer"):
        return previous

    messages = await _get_session(session_id)
    if not (tenant_dir() / "qualification.json").is_file():
        await storage.upsert_call(
            call_id=session_id, caller_phone=caller_phone,
            transcript=_extract_transcript(messages) or (previous or {}).get("transcript", ""),
            agent_response=(previous or {}).get("agent_response", ""),
            action="transfer" if previous and previous["action"] == "transfer" else "finished",
        )
        return {"call_id": session_id, "action": "finished"}
    cards = await storage.get_handoff_cards(session_id)
    prior_card = cards[-1] if cards else {}
    if previous and previous["action"] == "transfer" and "Итог: " in (prior_card.get("comment") or ""):
        return previous
    facts: Dict[str, Any] = {}
    for message in messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            extract_facts(facts, message["content"])
    if prior_card.get("need"):
        facts["need"] = prior_card["need"]
    if prior_card.get("budget") and "budget_byn" not in facts:
        extract_facts(facts, f"Бюджет {prior_card['budget']}")
    if prior_card.get("urgency") and "days" not in facts:
        extract_facts(facts, prior_card["urgency"])
    result = build_card(session_id, facts)
    comment = f"Итог: {result['category']}. {result['reason']}. {result['next_step']}"
    if prior_card.get("comment"):
        original_comment = prior_card["comment"].split(" | Итог: ", 1)[0]
        if not original_comment.startswith("Итог: "):
            comment = f"{original_comment} | {comment}"
    card = {
        "name": prior_card.get("name"),
        "need": facts.get("need") or "Потребность не уточнена",
        "budget": str(facts["budget_byn"]) if "budget_byn" in facts else prior_card.get("budget"),
        "urgency": prior_card.get("urgency") or (f"через {facts['days']} дн." if "days" in facts else None),
        "contact_phone": prior_card.get("contact_phone") or caller_phone or "Номер не определён",
        "comment": comment,
    }
    await storage.save_handoff_card(session_id, card)
    action = "transfer" if previous and previous["action"] == "transfer" else result["category"]
    await storage.upsert_call(
        call_id=session_id,
        caller_phone=caller_phone,
        transcript=_extract_transcript(messages) or (previous or {}).get("transcript", ""),
        agent_response=(previous or {}).get("agent_response", ""),
        action=action,
    )
    logger.info(f"[{session_id}] call finalized category={result['category']} action={action}")
    return result


async def _run_turn(
    session_id: str,
    caller_phone: str,
    user_message: str,
    model_profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Один ход диалога. Возвращает {response, transfer, chat, links, persisted}; ошибки провайдера и хранилища не глотает."""
    history = await _get_session(session_id)
    history.append({"role": "user", "content": user_message})
    extra = {k: v for k, v in (await _notes_for_turn(session_id)).items() if v}
    extra = _channel_context(extra, caller_phone)

    result = await agent_for(model_profile).run(messages=history, caller_phone=caller_phone, **extra)
    _normalize_reply(result)
    _browser_handoff(result, caller_phone)

    await _save_session(session_id, caller_phone, result["messages"])
    _schedule_notes(session_id, result["messages"])
    persisted = await _persist_turn(
        session_id=session_id,
        caller_phone=caller_phone,
        messages=result["messages"],
        agent_response=result["response"],
        pending_transfer=result["pending_transfer"],
        handoff_card=result.get("handoff_card"),
    )
    if not persisted and (result.get("handoff_card") or result["pending_transfer"]):
        _replace_final_response(result, SAVE_FAILED_REPLY)
        await _save_session(session_id, caller_phone, result['messages'])

    chat = result.get("chat", [])
    await _deliver_chat(session_id, chat)

    return {
        "response": result["response"],
        "transfer": result["pending_transfer"] if caller_phone else None,
        "handoff_card": result.get("handoff_card"),
        "chat": chat,
        "links": result.get("links", []),
        "persisted": persisted,
    }


INTERRUPT_MARK = " [абонент перебил]"


async def _run_turn_stream(
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
    extra = {k: v for k, v in (await _notes_for_turn(session_id)).items() if v}
    extra = _channel_context(extra, caller_phone)
    if not caller_phone:
        # Preserve the caller’s facts if browser playback is interrupted before done.
        await _save_session(session_id, caller_phone, history)
    awaiting_handoff = False
    buffered_sentences = []
    reply_started = False
    async for event in agent_for(model_profile).run_stream(history, caller_phone, **extra):
        if event["type"] != "done":
            if event['type'] == 'sentence' and not reply_started:
                text = _without_greeting(event['text'])
                if not text:
                    continue
                event = {**event, 'text': text}
                reply_started = True
            if event["type"] == "tool" and (event.get("name") == "save_handoff_card" or
                                            (not caller_phone and event.get("name") == "transfer_to_department")):
                awaiting_handoff = True
            if awaiting_handoff and event["type"] == "sentence":
                buffered_sentences.append(event)
                continue
            yield event
            continue
        result = event["result"]
        _normalize_reply(result)
        if not reply_started:
            buffered_sentences = [{'type': 'sentence', 'text': result['response']}]
        browser_handoff = _browser_handoff(result, caller_phone)
        if browser_handoff:
            buffered_sentences = [{"type": "sentence", "text": result['response']}]
        await _save_session(session_id, caller_phone, result["messages"])
        _schedule_notes(session_id, result["messages"])
        persisted = await _persist_turn(
            session_id=session_id,
            caller_phone=caller_phone,
            messages=result["messages"],
            agent_response=result["response"],
            pending_transfer=result["pending_transfer"],
            handoff_card=result.get("handoff_card"),
        )
        if not persisted and (result.get("handoff_card") or result["pending_transfer"]):
            _replace_final_response(result, SAVE_FAILED_REPLY)
            await _save_session(session_id, caller_phone, result['messages'])
            yield {"type": "sentence", "text": SAVE_FAILED_REPLY}
        else:
            for sentence in buffered_sentences:
                yield sentence
        chat = result.get("chat", [])
        await _deliver_chat(session_id, chat)
        yield {
            "type": "done",
            "response": result["response"],
            "transfer": result["pending_transfer"] if caller_phone else None,
            "handoff_card": result.get("handoff_card"),
            "chat": chat,
            "links": result.get("links", []),
            "persisted": persisted,
        }


async def _mark_interrupted(
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


async def run_turn(session_id: str, caller_phone: str, user_message: str, model_profile: Optional[str] = None):
    async with session_guard(session_id):
        return await _run_turn(session_id, caller_phone, user_message, model_profile)


async def run_turn_stream(session_id: str, caller_phone: str, user_message: str, model_profile: Optional[str] = None):
    async with session_guard(session_id):
        async with aclosing(_run_turn_stream(session_id, caller_phone, user_message, model_profile)) as events:
            async for event in events:
                yield event


async def mark_interrupted(session_id: str, caller_phone: str, user_message: str, spoken: str, committed: bool):
    async with session_guard(session_id):
        await _mark_interrupted(session_id, caller_phone, user_message, spoken, committed)


async def finalize_call(session_id: str, caller_phone: str):
    async with session_guard(session_id):
        return await _finalize_call(session_id, caller_phone)
