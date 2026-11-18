"""Asterisk chan_websocket adapter (JSON control messages, binary G.711 μ-law)."""

import asyncio
import base64
import hmac
import json
from urllib.parse import quote

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.config import settings
from src.telephony import audio, routes
from src.telephony.session import CallSession
from src.utils import logger

router = APIRouter(prefix="/telephony/asterisk")
_active: set[str] = set()


def authorized(header: str) -> bool:
    if not settings.asterisk_media_password:
        return False
    credentials = f"asterisk:{settings.asterisk_media_password}".encode()
    expected = b"Basic " + base64.b64encode(credentials)
    return hmac.compare_digest(header.encode(), expected)


async def transfer_channel(channel_id: str, number: str) -> bool:
    """Return the original Stasis channel to a configured transfer dialplan."""
    base_url = settings.asterisk_ari_url
    context = settings.asterisk_transfer_context
    if not (channel_id and base_url and context and settings.asterisk_ari_username and settings.asterisk_ari_password):
        return False
    async with httpx.AsyncClient(
        auth=(settings.asterisk_ari_username, settings.asterisk_ari_password), timeout=5,
    ) as client:
        result = await client.post(
            f"{base_url.rstrip('/')}/channels/{quote(channel_id, safe='')}/continue",
            params={"context": context, "extension": number, "priority": 1},
        )
    return result.is_success


@router.websocket("/media")
async def media(ws: WebSocket):
    if not authorized(ws.headers.get("authorization", "")):
        await ws.close(code=4401)
        return
    protocols = ws.scope.get("subprotocols", ())
    await ws.accept(subprotocol="media" if "media" in protocols else None)
    session = None
    writable = asyncio.Event()
    writable.set()
    send_lock = asyncio.Lock()
    audio_lock = asyncio.Lock()

    async def command(name: str) -> None:
        async with send_lock:
            await ws.send_json({"command": name})

    async def send_audio(data: bytes) -> None:
        # Asterisk discards incomplete frames. Keep the remainder across TTS chunks.
        async with audio_lock:
            pending.extend(data)
            size = audio.FRAME_BYTES * 10
            while len(pending) >= audio.FRAME_BYTES:
                count = min(len(pending) // audio.FRAME_BYTES * audio.FRAME_BYTES, size)
                await writable.wait()
                async with send_lock:
                    await ws.send_bytes(bytes(pending[:count]))
                del pending[:count]

    async def flush() -> None:
        async with audio_lock:
            pending.clear()
        await command("FLUSH_MEDIA")
        writable.set()

    async def hangup() -> None:
        await command("HANGUP")

    pending = bytearray()
    try:
        incoming = await asyncio.wait_for(ws.receive_text(), 5)
        start = json.loads(incoming)
        if (not isinstance(start, dict) or start.get("event") != "MEDIA_START"
                or start.get("format") != "ulaw" or start.get("optimal_frame_size") != audio.FRAME_BYTES):
            await ws.close(code=1003)
            return

        connection_id = start.get("connection_id")
        if not isinstance(connection_id, str) or not connection_id or len(connection_id) > 128:
            await ws.close(code=1003)
            return
        if connection_id in _active or len(_active) >= settings.telephony_max_concurrent_calls:
            await ws.close(code=1013)
            return
        metadata = start.get("channel_variables") or {}
        if not isinstance(metadata, dict):
            await ws.close(code=1003)
            return

        async def transfer(number: str) -> bool:
            return await transfer_channel(str(metadata.get("AGENT_CALLER_CHANNEL", "")), number)

        session = CallSession(
            f"asterisk-{connection_id}", str(metadata.get("AGENT_CALLER_NUMBER", ""))[:32],
            routes.make_pipeline(send_audio, flush, hangup, transfer),
        )
        _active.add(connection_id)
        await session.start()
        while True:
            event = await ws.receive()
            if event["type"] == "websocket.disconnect":
                break
            if event.get("bytes"):
                await session.on_audio(event["bytes"])
            elif event.get("text"):
                control = json.loads(event["text"])
                if control.get("event") == "MEDIA_XOFF":
                    writable.clear()
                elif control.get("event") == "MEDIA_XON":
                    writable.set()
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError, AttributeError, asyncio.TimeoutError):
        await ws.close(code=1003)
    except Exception as exc:
        logger.warning(f"Asterisk media session failed: {type(exc).__name__}")
        await ws.close(code=1011)
    finally:
        if session is not None:
            _active.discard(connection_id)
            await session.close()
