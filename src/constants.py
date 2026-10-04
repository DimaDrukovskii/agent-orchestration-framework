AGENT_SYSTEM_PROMPT = """Ты помощник оператора МТС для обработки входящих звонков.
Твоя задача:
1. Слушать и понимать проблему клиента
2. Собирать необходимую информацию
3. Предоставлять решение или маршрутизировать на специалиста

Будь профессиональным, вежливым и быстрым.
"""

ACTIONS = {
    "TRANSFER": "transfer",
    "ESCALATE": "escalate",
    "RESOLVE": "resolve",
}

CALL_STATES = {
    "INITIATED": "initiated",
    "IN_PROGRESS": "in_progress",
    "COMPLETED": "completed",
    "FAILED": "failed",
}
