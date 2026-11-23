"""Browser-to-browser audio. Voice frames are relayed by this single-worker service."""
import asyncio
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from src.api.auth import verify_api_key, verify_manager_key

router = APIRouter()


@dataclass
class Room:
    caller: WebSocket
    manager: WebSocket | None = None


rooms: dict[str, Room] = {}


@router.get("/live/waiting", dependencies=[Depends(verify_manager_key)])
async def waiting():
    return [key for key, room in rooms.items() if room.manager is None]


@router.websocket("/live/connect")
async def connect(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and urlsplit(origin).netloc != ws.headers.get("host"):
        await ws.close(code=1008)
        return
    await ws.accept()
    room = None
    key = None
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 5)
        if not isinstance(hello, dict) or not isinstance(hello.get("key"), str):
            await ws.close(code=1008)
            return
        role = hello.get("role")
        if role == "caller":
            await verify_api_key(hello.get("key"))
            if len(rooms) >= 32:
                await ws.close(code=1013)
                return
            key = secrets.token_urlsafe(16)
            room = rooms[key] = Room(ws)
            await ws.send_json({"type": "waiting"})
        elif role == "manager":
            await verify_manager_key(hello.get("key"))
            key = hello.get("room")
            candidate = rooms.get(key)
            if candidate is None or candidate.manager:
                await ws.close(code=1008)
                return
            room = candidate
            room.manager = ws
            await room.caller.send_json({"type": "joined"})
            await ws.send_json({"type": "joined"})
        else:
            await ws.close(code=1008)
            return
        while True:
            packet = await asyncio.wait_for(ws.receive(), 120 if room.manager is None else 60)
            if packet["type"] == "websocket.disconnect":
                break
            frame = packet.get("bytes")
            if not frame or len(frame) > 8192 or len(frame) % 2:
                break
            peer = room.manager if role == "caller" else room.caller
            if peer:
                await asyncio.wait_for(peer.send_bytes(frame), 3)
    except (WebSocketDisconnect, HTTPException, TimeoutError, RuntimeError, ValueError, TypeError, AttributeError):
        pass
    finally:
        if room and rooms.pop(key, None):
            peer = room.manager if ws is room.caller else room.caller
            if peer:
                try:
                    await peer.close()
                except (WebSocketDisconnect, RuntimeError):
                    pass
        try:
            await ws.close()
        except (WebSocketDisconnect, RuntimeError):
            pass
