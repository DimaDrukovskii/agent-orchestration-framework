"""Durable Telegram outbox. At-least-once delivery; no request-path network calls.

SQLite must be on persistent storage shared by this installation's workers.
An external send followed by a crash before acknowledgment can produce a duplicate.
"""

import asyncio
import hashlib
import time
import uuid
from typing import Optional

from src.config import settings
from src.services.sqlite_store import _db, init_db
from src.utils import logger


class DeliveryOutbox:
    def __init__(self, path: Optional[str] = None):
        self.path = init_db(path or settings.sqlite_path)

    @staticmethod
    def notification(call_id: str, message: str, *, category: str = "handoff") -> dict:
        digest = hashlib.sha256(f"{category}\0{call_id}\0{message}".encode()).hexdigest()
        return {"event_key": digest, "chat_id": settings.telegram_chat_id, "message": message}

    async def enqueue(self, call_id: str, notification: dict) -> None:
        def op():
            with _db(self.path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO delivery_outbox "
                    "(event_key, call_id, chat_id, message, created_at) VALUES (?, ?, ?, ?, ?)",
                    (notification['event_key'], call_id, notification['chat_id'], notification['message'], time.time()),
                )
        await asyncio.to_thread(op)

    async def claim(self) -> Optional[dict]:
        def op():
            now, owner = time.time(), uuid.uuid4().hex
            with _db(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM delivery_outbox WHERE "
                    "(status='pending' AND available_at<=?) OR (status='sending' AND lease_until<=?) "
                    "ORDER BY id LIMIT 1", (now, now),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE delivery_outbox SET status='sending', owner=?, lease_until=?, "
                    "attempts=attempts+1 WHERE id=?", (owner, now + 60, row['id']),
                )
                return {**dict(row), 'owner': owner, 'attempts': row['attempts'] + 1}
        return await asyncio.to_thread(op)

    async def settle(self, job: dict, delivered: bool, error: str = "delivery_failed") -> None:
        def op():
            now = time.time()
            with _db(self.path) as conn:
                conn.execute(
                    "UPDATE delivery_outbox SET status=?, delivered_at=?, available_at=?, "
                    "lease_until=0, owner=NULL, last_error=? WHERE id=? AND owner=? AND status='sending'",
                    ('delivered' if delivered else 'pending', now if delivered else None,
                     now + min(600, 5 * 2 ** min(job['attempts'], 7)),
                     None if delivered else error, job['id'], job['owner']),
                )
        await asyncio.to_thread(op)

    async def report(self) -> dict:
        def op():
            with _db(self.path) as conn:
                counts = {s: 0 for s in ('pending', 'sending', 'delivered')}
                counts.update({r['status']: r['n'] for r in conn.execute(
                    "SELECT status, COUNT(*) n FROM delivery_outbox GROUP BY status"
                )})
                # No contact details, messages or bot credentials in operational status.
                rows = [dict(r) for r in conn.execute(
                    "SELECT id, call_id, status, attempts, last_error, created_at FROM delivery_outbox "
                    "WHERE status!='delivered' ORDER BY created_at LIMIT 100"
                )]
                oldest = conn.execute(
                    "SELECT MIN(created_at) FROM delivery_outbox WHERE status!='delivered'"
                ).fetchone()[0]
            configured = bool(settings.telegram_bot_token and settings.telegram_chat_id)
            return {'configured': configured, 'counts': counts, 'items': rows,
                    'oldest_pending_seconds': max(0, round(time.time() - oldest)) if oldest is not None else 0,
                    'needs_attention': bool(counts['pending'] or counts['sending'])}
        return await asyncio.to_thread(op)


async def deliver_one(outbox: DeliveryOutbox) -> bool:
    # Unconfigured jobs remain visible and do not burn attempts.
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    job = await outbox.claim()
    if job is None:
        return False
    from src.services.telegram import send_message
    try:
        delivered = await asyncio.wait_for(
            send_message(job['chat_id'] or settings.telegram_chat_id, job['message']), timeout=15,
        )
        await outbox.settle(job, delivered)
    except asyncio.CancelledError:
        # Lease recovery handles a process stop, including cancellation during a send.
        raise
    except Exception as exc:
        await outbox.settle(job, False, type(exc).__name__)
    return True


async def run_worker() -> None:
    outbox = DeliveryOutbox()
    while True:
        try:
            if await deliver_one(outbox):
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"delivery worker: {type(exc).__name__}")
        await asyncio.sleep(2)
