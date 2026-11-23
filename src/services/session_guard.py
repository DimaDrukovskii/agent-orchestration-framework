"""Serialize a conversation on one installation, including separate local workers.

SQLite deployments share POSIX file locks next to the database. OS locks release
on process exit; no expiring lease can silently allow two simultaneous writers.
Redis deployments currently receive only process-local protection; distributed
fencing is a separate prerequisite for deployment across multiple hosts.
"""
import asyncio
import hashlib
import os
import weakref
from contextlib import asynccontextmanager

from src.config import settings
from src.services.sqlite_store import resolve_path

_locks = weakref.WeakValueDictionary()


class SessionBusy(RuntimeError):
    pass


@asynccontextmanager
async def session_guard(session_id: str):
    key = (id(asyncio.get_running_loop()), resolve_path(settings.sqlite_path), session_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    try:
        async with asyncio.timeout(20):
            await lock.acquire()
    except asyncio.TimeoutError as exc:
        raise SessionBusy('Предыдущая реплика ещё обрабатывается') from exc
    fd = None
    try:
        if settings.cache_backend == 'sqlite':
            import fcntl
            directory = resolve_path(settings.sqlite_path) + '.session-locks'
            os.makedirs(directory, exist_ok=True)
            digest = hashlib.sha256(session_id.encode()).hexdigest()
            fd = os.open(os.path.join(directory, digest + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
            deadline = asyncio.get_running_loop().time() + 20
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise SessionBusy('Предыдущая реплика ещё обрабатывается')
                    await asyncio.sleep(0.025)
        yield
    finally:
        if fd is not None:
            os.close(fd)
        lock.release()
