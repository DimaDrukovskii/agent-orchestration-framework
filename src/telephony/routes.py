"""
Телефонный канал Twilio: два адреса.

  POST /telephony/twilio/voice   вебхук «входящий звонок»: проверяет подпись Twilio и отвечает TwiML,
                                 который соединяет звонок с нашим WebSocket
  WS   /telephony/twilio/stream  двусторонний звук звонка (Twilio Media Streams, µ-law 8 кГц, base64)

Тут только транспорт Twilio. Логика звонка (конец реплики, перебивание, перевод) в session.py, диалог в
src/services/dialog.py. Под другого провайдера (SIP, Exolve) пишется такой же файл рядом.
Подключение и проверка: docs/website_side_bar/TELEPHONY.md.
"""

import asyncio
import base64
import json
from collections import OrderedDict
from typing import Dict, Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

from src.config import settings
from src.services import dialog
from src.services.stt import stt_service
from src.services.tts import tts_service
from src.telephony import audio, twilio
from src.telephony.session import CallSession, Pipeline
from src.tenant import load_telephony
from src.utils import logger

router = APIRouter(prefix="/telephony/twilio")

VOICE_PATH = "/telephony/twilio/voice"
STREAM_PATH = "/telephony/twilio/stream"
CHUNK_BYTES = 4000  # 0,5 с звука: кусками, чтобы перебивание (clear) сработало быстро
AUDIO_CACHE_MAX = 200

active_calls = 0
_audio_cache: "OrderedDict[str, bytes]" = OrderedDict()  # текст -> µ-law: приветствие и типовые фразы звучат мгновенно


def _xml(body: str, status: int = 200) -> Response:
    return Response(content=body, media_type="application/xml", status_code=status)


@router.post("/voice")
async def voice_webhook(request: Request):
    if not settings.telephony_enabled:
        return _xml(twilio.reject_twiml("Услуга временно недоступна."), 503)
    params: Dict[str, str] = dict(parse_qsl((await request.body()).decode("utf-8", "replace")))
    if settings.twilio_validate_signature:
        url = settings.public_base_url.rstrip("/") + VOICE_PATH  # ровно тот адрес, что указан в консоли Twilio
        if not twilio.valid_signature(
            settings.twilio_auth_token, url, params, request.headers.get("x-twilio-signature", "")
        ):
            logger.warning("twilio webhook: bad signature")
            return Response(status_code=403)
    if active_calls >= settings.telephony_max_concurrent_calls:
        return _xml(twilio.reject_twiml("Все линии сейчас заняты, перезвоните, пожалуйста, позже."))
    call_sid = params.get("CallSid", "")
    ws_url = twilio.ws_url_for(settings.public_base_url, STREAM_PATH)
    return _xml(twilio.stream_twiml(ws_url, call_sid, params.get("From", "")))


async def synthesize_mulaw(text: str) -> bytes:
    """Озвучка фразы в µ-law 8 кГц; повторяющиеся фразы берутся из памяти."""
    cached = _audio_cache.get(text)
    if cached is not None:
        _audio_cache.move_to_end(text)
        return cached
    mp3 = await tts_service.synthesize(text)
    if not mp3:
        raise RuntimeError("озвучка не настроена")
    mulaw = await asyncio.to_thread(audio.audio_to_mulaw, mp3)
    _audio_cache[text] = mulaw
    while len(_audio_cache) > AUDIO_CACHE_MAX:
        _audio_cache.popitem(last=False)
    return mulaw


async def transcribe_wav(wav: bytes) -> str:
    return (await stt_service.transcribe(wav, "audio/wav"))["text"]


async def stream_turn(session_id: str, caller: str, text: str):
    async for event in dialog.run_turn_stream(session_id, caller, text, settings.telephony_model_profile):
        yield event


async def mark_interrupted(session_id: str, caller: str, user_text: str, spoken: str, committed: bool) -> None:
    await dialog.mark_interrupted(session_id, caller, user_text, spoken, committed)


def build_pipeline(ws: WebSocket, send_lock: asyncio.Lock, stream_sid: str, call_sid: str) -> Pipeline:
    async def send_json(payload: dict) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(payload))

    async def send_audio(mulaw: bytes) -> None:
        for start in range(0, len(mulaw), CHUNK_BYTES):
            chunk = base64.b64encode(mulaw[start : start + CHUNK_BYTES]).decode()
            await send_json({"event": "media", "streamSid": stream_sid, "media": {"payload": chunk}})

    async def clear_audio() -> None:
        await send_json({"event": "clear", "streamSid": stream_sid})

    async def end_call() -> None:
        await ws.close()

    async def transfer_call(number: str) -> bool:
        return await twilio.update_call(call_sid, twilio.dial_twiml(number))

    return Pipeline(
        transcribe=transcribe_wav,
        stream_turn=stream_turn,
        mark_interrupted=mark_interrupted,
        synthesize=synthesize_mulaw,
        send_audio=send_audio,
        clear_audio=clear_audio,
        end_call=end_call,
        transfer_call=transfer_call,
        phrases=load_telephony(),
    )


@router.websocket("/stream")
async def media_stream(ws: WebSocket):
    global active_calls
    await ws.accept()
    session: Optional[CallSession] = None
    send_lock = asyncio.Lock()
    try:
        async for raw in ws.iter_text():
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            event = message.get("event")
            if event == "start" and session is None:
                start = message.get("start", {})
                call_sid, stream_sid = start.get("callSid", ""), start.get("streamSid", "")
                params = start.get("customParameters", {})
                if settings.twilio_validate_signature and not twilio.valid_stream_token(call_sid, params.get("token", "")):
                    logger.warning("twilio stream: bad token")
                    await ws.close(code=4401)
                    return
                active_calls += 1
                session = CallSession(call_sid, params.get("caller", ""), build_pipeline(ws, send_lock, stream_sid, call_sid))
                await session.start()
            elif event == "media" and session is not None:
                await session.on_audio(base64.b64decode(message["media"]["payload"]))
            elif event == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"twilio stream error: {type(e).__name__}")
    finally:
        if session is not None:
            active_calls -= 1
            await session.close()


async def prewarm() -> None:
    """Озвучить приветствие заранее: первый звонок после запуска не ждёт озвучки."""
    try:
        await synthesize_mulaw(load_telephony()["greeting"])
        logger.info("telephony: greeting is warmed up")
    except Exception as e:
        logger.warning(f"telephony prewarm skipped: {type(e).__name__}")
