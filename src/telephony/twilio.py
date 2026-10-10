"""
Всё, что касается самого Twilio: подпись вебхука, ответ TwiML, токен потока, управление звонком по REST.

Вебхук голоса подписан Twilio (X-Twilio-Signature): подделать вызов нашего номера нельзя без токена
аккаунта. WebSocket потока Twilio не подписывает, поэтому вебхук кладёт в поток одноразовый токен
(HMAC от CallSid), а поток его проверяет: прямое подключение к /stream без вызова вебхука отклоняется.
"""

import base64
import hashlib
import hmac
from typing import Dict
from xml.sax.saxutils import quoteattr

from src.config import settings
from src.utils import logger

API_BASE = "https://api.twilio.com/2010-04-01"


def signature_for(auth_token: str, url: str, params: Dict[str, str]) -> str:
    """Подпись Twilio: HMAC-SHA1 от URL и параметров формы, отсортированных по имени."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def valid_signature(auth_token: str, url: str, params: Dict[str, str], signature: str) -> bool:
    return bool(auth_token and signature) and hmac.compare_digest(signature_for(auth_token, url, params), signature)


def stream_token(call_sid: str) -> str:
    key = (settings.twilio_auth_token or settings.agent_api_key).encode()
    return hmac.new(key, f"stream:{call_sid}".encode(), hashlib.sha256).hexdigest()[:32]


def valid_stream_token(call_sid: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(stream_token(call_sid), token)


def stream_twiml(ws_url: str, call_sid: str, caller: str) -> str:
    """Ответ вебхука: соединить звонок с нашим WebSocket (двусторонний звук)."""
    params = {"token": stream_token(call_sid), "caller": caller}
    inner = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in params.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Connect><Stream url={quoteattr(ws_url)}>{inner}</Stream></Connect></Response>'


def reject_twiml(message: str = "") -> str:
    say = f'<Say language="ru-RU">{message}</Say>' if message else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{say}<Hangup/></Response>'


def ws_url_for(base_url: str, path: str) -> str:
    """https://host -> wss://host/path (Twilio подключается к потоку только по wss)."""
    host = base_url.rstrip("/").replace("https://", "").replace("http://", "")
    return f"wss://{host}{path}"


async def update_call(call_sid: str, twiml: str) -> bool:
    """Подменить TwiML идущего звонка (перевод на номер, завершение). False = не вышло или не настроено."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token and call_sid):
        return False
    import httpx

    url = f"{API_BASE}/Accounts/{settings.twilio_account_sid}/Calls/{call_sid}.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url, data={"Twiml": twiml}, auth=(settings.twilio_account_sid, settings.twilio_auth_token)
            )
        if not response.is_success:
            logger.error(f"Twilio update_call: HTTP {response.status_code}")
        return response.is_success
    except Exception as e:
        # текст исключения httpx может содержать URL с идентификатором аккаунта: пишем только тип
        logger.error(f"Twilio update_call failed: {type(e).__name__}")
        return False


def dial_twiml(number: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Dial>{number}</Dial></Response>'
