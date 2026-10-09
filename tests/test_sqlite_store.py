import asyncio
import sqlite3

import pytest

from src.config import settings
from src.services.cache import CacheService
from src.services.sqlite_store import SqliteCache, SqliteStorage
from src.services.storage import StorageService


def run(coro):
    return asyncio.run(coro)


def test_cache_roundtrip_and_delete(tmp_path):
    cache = SqliteCache(str(tmp_path / "t.db"))

    assert run(cache.get("k")) is None
    run(cache.set("k", {"messages": [1, 2]}, ttl=60))
    assert run(cache.get("k")) == {"messages": [1, 2]}

    run(cache.set("k", {"messages": [3]}, ttl=60))
    assert run(cache.get("k")) == {"messages": [3]}

    run(cache.delete("k"))
    assert run(cache.get("k")) is None


def test_cache_entry_expires(tmp_path):
    cache = SqliteCache(str(tmp_path / "t.db"))
    run(cache.set("k", {"a": 1}, ttl=0))
    assert run(cache.get("k")) is None


def test_cache_increment(tmp_path):
    cache = SqliteCache(str(tmp_path / "t.db"))
    assert run(cache.increment("rl")) == 1
    assert run(cache.increment("rl", 4)) == 5


def test_upsert_call_keeps_one_row_per_call_id(tmp_path):
    storage = SqliteStorage(str(tmp_path / "t.db"))

    run(storage.upsert_call("c1", "+79990000000", "USER: привет", "Здравствуйте", "in_progress"))
    row = run(storage.upsert_call("c1", "+79990000000", "USER: привет\nASSISTANT: ...", "Ответ 2", "transfer"))

    assert row["call_id"] == "c1"
    assert row["agent_response"] == "Ответ 2"
    assert row["action"] == "transfer"
    assert len(run(storage.get_caller_history("+79990000000"))) == 1
    assert run(storage.get_call("c1"))["transcript"].endswith("...")
    assert run(storage.get_call("missing")) is None


def test_save_call_rejects_duplicate_call_id(tmp_path):
    storage = SqliteStorage(str(tmp_path / "t.db"))
    run(storage.save_call("c1", "+7", "t", "r", "resolve"))
    with pytest.raises(sqlite3.IntegrityError):
        run(storage.save_call("c1", "+7", "t", "r", "resolve"))


def test_caller_history_newest_first_and_limited(tmp_path):
    storage = SqliteStorage(str(tmp_path / "t.db"))
    for i in range(3):
        run(storage.save_call(f"c{i}", "+7", "t", "r", "resolve"))

    history = run(storage.get_caller_history("+7", limit=2))

    assert [r["call_id"] for r in history] == ["c2", "c1"]


def test_services_use_sqlite_backend_from_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_backend", "sqlite")
    monkeypatch.setattr(settings, "storage_backend", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "t.db"))

    cache = CacheService()
    storage = StorageService()

    run(cache.set("s", {"x": 1}))
    assert run(cache.get("s")) == {"x": 1}
    run(storage.upsert_call("c1", "+7", "t", "r", "in_progress"))
    assert run(storage.get_call("c1"))["call_id"] == "c1"
    assert run(storage.get_client_by_api_key_hash("abc")) is None


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "cache_backend", "memcached")
    with pytest.raises(ValueError):
        CacheService()
