"""
Инструменты (tools) для Hermes-агента.
Каждый tool — функция, которую Claude может вызвать сам в процессе рассуждения.
"""

import os
import glob
from typing import Dict, Any
from src.utils import logger, mask_phone

KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "../../data/knowledge")


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

def search_knowledge_base(query: str) -> str:
    """Простой поиск по markdown-файлам базы знаний (keyword match)."""
    try:
        results = []
        files = [
            f for f in glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))
            if os.path.basename(f).lower() != "readme.md"
        ]
        query_words = query.lower().split()

        for filepath in files:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            score = sum(1 for word in query_words if word in content.lower())
            if score > 0:
                results.append((score, os.path.basename(filepath), content))

        results.sort(key=lambda x: x[0], reverse=True)

        if not results:
            return "Ничего не найдено в базе знаний по этому запросу."

        top = results[0]
        logger.info(f"Knowledge base: found match in {top[1]} (score={top[0]})")
        return f"Источник: {top[1]}\n\n{top[2]}"

    except Exception as e:
        logger.error(f"search_knowledge_base error: {str(e)}")
        return "Ошибка доступа к базе знаний."


KB_CONTEXT_MAX_CHARS = 1500
KB_MIN_WORD_LEN = 4  # короткие слова («не», «у», «как») совпадают с чем угодно


def kb_context(user_text: str, max_chars: int = KB_CONTEXT_MAX_CHARS) -> str:
    """
    Фрагмент базы знаний под реплику абонента — для подстановки в запрос до
    обращения к модели. Пустая строка, если ничего подходящего нет.
    """
    words = [w.strip(".,!?;:«»\"'()") for w in user_text.lower().split()]
    query = " ".join(w for w in words if len(w) >= KB_MIN_WORD_LEN)
    if not query:
        return ""
    found = search_knowledge_base(query)
    if found.startswith(("Ничего не найдено", "Ошибка доступа")):
        return ""
    return found if len(found) <= max_chars else found[:max_chars] + "[…]"


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


# Диспетчер вызовов
TOOL_HANDLERS = {
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
