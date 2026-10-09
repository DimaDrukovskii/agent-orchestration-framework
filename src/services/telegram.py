"""
Уведомления/перевод в чат через Telegram (docs/TRACK1_PLAN.md).

settings.telegram_bot_token / settings.telegram_chat_id пустые по умолчанию —
функция выключена, агент работает без неё, ничего не падает.
"""

from src.config import settings
from src.utils import logger


async def send_message(chat_id: str, text: str) -> bool:
    """Отправить текст в Telegram-чат через Bot API. False = не отправлено (не настроено или ошибка)."""
    if not settings.telegram_bot_token or not chat_id:
        return False

    import httpx

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Telegram send_message error: {str(e)}")
        return False
