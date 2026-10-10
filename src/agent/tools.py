"""
Инструменты (tools) для Hermes-агента.
Каждый tool — функция, которую Claude может вызвать сам в процессе рассуждения.
"""

import json
import math
import os
import glob
from typing import List, Dict, Any
from src.tenant import tenant_dir
from src.utils import logger, mask_phone

KNOWLEDGE_DIR = str(tenant_dir() / "knowledge")


# ---- Схемы инструментов для Claude (tool_use API) ----

TOOLS_SCHEMA = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Поиск информации в базе знаний МТС (тарифы, техподдержка, биллинг). "
            "Используй это ПЕРЕД тем, как отвечать на вопрос по существу — "
            "не придумывай цифры и инструкции, бери их отсюда."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Что искать, например 'низкая скорость интернета' или 'тариф XL'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "check_account_status",
        "description": "Проверить состояние лицевого счёта абонента (баланс, тариф, блокировки).",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Номер телефона абонента"}
            },
            "required": ["phone"]
        }
    },
    {
        "name": "transfer_to_department",
        "description": (
            "Перевести звонок на отдел, когда ты не можешь решить вопрос сам. "
            "Вызывай это ТОЛЬКО когда реально нужен человек-специалист."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "department": {
                    "type": "string",
                    "enum": ["техподдержка", "биллинг", "продажи", "менеджер"]
                },
                "reason": {"type": "string", "description": "Краткая причина перевода"}
            },
            "required": ["department", "reason"]
        }
    },
    {
        "name": "send_to_chat",
        "description": (
            "Отправить абоненту в чат подробности, которые плохо слушать голосом: цены и "
            "сравнение тарифов, больше двух шагов инструкции, ссылки, номера. Голосом при "
            "этом скажи одной фразой, что отправила, и не зачитывай текст."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Готовое сообщение для чата, без markdown"}
            },
            "required": ["text"]
        }
    }
]


# ---- Реализация инструментов ----

KB_CONTEXT_MAX_CHARS = 1500  # 900 резало вторую выборку: на 50 тестовых абонентах 1500 даёт 31 попадание из 34, 900 только 28
KB_TOOL_MAX_CHARS = 3000
KB_MIN_WORD_LEN = 4  # короткие слова («не», «у», «как») совпадают с чем угодно


def _is_searchable(word: str) -> bool:
    """Длинные слова, а также короткие названия латиницей/цифрами («RED», «XL», «5G»)."""
    return len(word) >= KB_MIN_WORD_LEN or (len(word) >= 2 and word.isascii())
KB_STEM_LEN = 5  # «тарифы» / «тарифа» / «тарифов» находят один и тот же кусок
KB_CHUNK_MAX_CHARS = 900
KB_MAX_CHUNKS = 3


def _kb_chunks() -> List[tuple]:
    """Файлы базы знаний, нарезанные на куски по заголовкам и абзацам: (файл, текст)."""
    chunks = []
    for filepath in sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))):
        name = os.path.basename(filepath)
        if name.lower() == "readme.md":
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            sections, current = [], []
            for line in f.read().splitlines():
                if line.startswith("#") and current:
                    sections.append("\n".join(current))
                    current = []
                current.append(line)
            sections.append("\n".join(current))
        for section in sections:
            piece = ""
            for paragraph in section.split("\n\n"):
                if piece and len(piece) + len(paragraph) > KB_CHUNK_MAX_CHARS:
                    chunks.append((name, piece.strip()))
                    piece = ""
                piece += paragraph + "\n\n"
            if piece.strip():
                chunks.append((name, piece.strip()))
    return chunks


# как название тарифа звучит в речи и как оно записано в базе («ред» говорят, RED написано)
KB_ALIASES = {"ред": "red", "джуниор": "junior", "джунир": "junior", "рийл": "риил", "рил": "риил", "налегке": "налегке"}
KB_SHORT_BRANDS = {"топ", "ред"}


def _kb_stems(query: str) -> List[str]:
    words = [w.strip(".,!?;:«»\"'()") for w in query.lower().split()]
    words = [KB_ALIASES.get(w, w) for w in words]
    long_words = [w for w in words if _is_searchable(w) or w in KB_SHORT_BRANDS]
    return list(dict.fromkeys(w[:KB_STEM_LEN] for w in (long_words or words) if w))


def search_knowledge_base(query: str, max_chars: int = KB_TOOL_MAX_CHARS) -> str:
    """
    Поиск по markdown-файлам базы знаний: файлы режутся на куски, берутся лучшие куски
    из любых файлов (не один файл целиком), совпадение по основе слова.
    """
    try:
        stems = _kb_stems(query)
        raw = _kb_chunks()
        # «Используй это для вопросов: …» в начале файла: подсказка для поиска. Она помогает куску с фактами
        # (таблице цен) найтись по общим словам, но вводный кусок с ней сам не должен обгонять таблицу
        hints = {name: line for name, chunk in raw for line in chunk.splitlines() if line.startswith("Используй это")}
        chunks = []
        for name, chunk in raw:
            own = "\n".join(l for l in chunk.splitlines() if not l.startswith("Используй это"))
            has_hint = any(l.startswith("Используй это") for l in chunk.splitlines())
            chunks.append((name, chunk, (own if has_hint else chunk + "\n" + hints.get(name, "")).lower()))
        # редкое слово («проще», «red») весит больше общего («тариф», «сколько»): иначе вводный абзац
        # файла, набитый общими словами, обгоняет таблицу с ценами
        weights = {}
        for stem in stems:
            df = sum(1 for _, _, lowered in chunks if stem in lowered)
            weights[stem] = math.log(1 + len(chunks) / df) if df else 0.0
        scored = []
        for name, chunk, lowered in chunks:
            score = sum(weights[stem] * min(lowered.count(stem), 3) for stem in stems)
            if score > 0:
                scored.append((round(score, 1), name, chunk))
        if not scored:
            return "Ничего не найдено в базе знаний по этому запросу."

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:KB_MAX_CHUNKS]
        logger.info(f"Knowledge base: {[(n, sc) for sc, n, _ in top]}")
        found = "\n\n---\n\n".join(f"Источник: {name}\n{chunk}" for _, name, chunk in top)
        return found if len(found) <= max_chars else found[:max_chars] + "[…]"

    except Exception as e:
        logger.error(f"search_knowledge_base error: {str(e)}")
        return "Ошибка доступа к базе знаний."


def kb_context(user_text: str, max_chars: int = KB_CONTEXT_MAX_CHARS) -> str:
    """
    Фрагмент базы знаний под реплику абонента — для подстановки в запрос до
    обращения к модели. Пустая строка, если ничего подходящего нет.
    """
    words = [w.strip(".,!?;:«»\"'()") for w in user_text.lower().split()]
    query = " ".join(w for w in words if _is_searchable(w))
    if not query:
        return ""
    found = search_knowledge_base(query, max_chars)
    if found.startswith(("Ничего не найдено", "Ошибка доступа")):
        return ""
    return found


def check_account_status(phone: str) -> Dict[str, Any]:
    """
    Проверка счёта абонента.
    MVP: заглушка. Подключить реальный биллинг МТС, когда дадут доступ/API.
    """
    logger.info(f"check_account_status: {mask_phone(phone)}")
    return {
        "phone": phone,
        "balance": "245.50 руб",
        "tariff": "Смартфон L",
        "status": "активен",
        "note": "ЗАГЛУШКА — подключить реальный биллинг МТС"
    }


def transfer_to_department(department: str, reason: str) -> Dict[str, Any]:
    """Фиксирует намерение перевода — реальный роутинг звонка делает MTS-сторона."""
    logger.info(f"transfer_to_department: {department} | {reason}")
    return {
        "action": "transfer",
        "department": department,
        "reason": reason
    }


CHAT_MESSAGE_MAX_CHARS = 1500


def send_to_chat(text: str) -> Dict[str, Any]:
    """Голос -> чат: текст попадёт абоненту через канал (виджет, Telegram) — см. hermes_routes."""
    return {"action": "chat", "text": text.strip()[:CHAT_MESSAGE_MAX_CHARS], "status": "отправлено в чат"}


SITE_MAP_PATH = str(tenant_dir() / "site_map.json")


def load_site_map() -> List[Dict[str, str]]:
    """Разделы сайта компании: [{id, title, url, when}]. Нет файла или он пуст — инструмента нет."""
    try:
        with open(SITE_MAP_PATH, "r", encoding="utf-8") as f:
            sections = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in sections if x.get("id") and x.get("url", "").startswith("https://")]


def open_section_tool() -> List[Dict[str, Any]]:
    """Схема инструмента show_section (формат OpenAI) с разделами из site_map.json; [] если карты нет."""
    sections = load_site_map()
    if not sections:
        return []
    listing = "; ".join(f"{x['id']} (когда: {x.get('when', x['title'])})" for x in sections)
    return [{
        "type": "function",
        "function": {
            "name": "show_section",
            "description": "Показать абоненту нужный раздел сайта кнопкой-ссылкой в окне. Разделы: " + listing,
            "parameters": {
                "type": "object",
                "properties": {"section": {"type": "string", "enum": [x["id"] for x in sections]}},
                "required": ["section"],
            },
        },
    }]


def show_section(section: str) -> Dict[str, Any]:
    for x in load_site_map():
        if x["id"] == section:
            return {"action": "link", "title": x.get("title", section), "url": x["url"]}
    return {"error": "неизвестный раздел"}


# Диспетчер вызовов
TOOL_HANDLERS = {
    "show_section": lambda inp: show_section(inp["section"]),
    "send_to_chat": lambda inp: send_to_chat(inp["text"]),
    "search_knowledge_base": lambda inp: search_knowledge_base(inp["query"]),
    "check_account_status": lambda inp: check_account_status(inp["phone"]),
    "transfer_to_department": lambda inp: transfer_to_department(inp["department"], inp["reason"]),
}


def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Any:
    """Выполнить tool по имени с входными данными от модели."""
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return f"Неизвестный инструмент: {tool_name}"
    return handler(tool_input)


# Та же схема в формате OpenAI Chat Completions tools (для polza.ai и
# аналогичных OpenAI-совместимых шлюзов) — src/agent/hermes_agent.py._run_openai.
TOOLS_SCHEMA_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOLS_SCHEMA
]
