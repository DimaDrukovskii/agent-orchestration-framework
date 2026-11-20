import asyncio

from src.config import settings
from src.services import dialog
from src.services.cache import CacheService
from src.services.sqlite_store import SqliteStorage
from src.telephony.session import CallSession, Pipeline


def test_phone_close_persists_qualification_and_card(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tenant", "climateplus")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "calls.db"))
    monkeypatch.setattr(dialog, "cache", CacheService())
    storage = SqliteStorage(settings.sqlite_path)

    async def run():
        messages = [
            {"role": "user", "content": "Нужен монтаж кондиционера. Бюджет 1 500 рублей."},
            {"role": "assistant", "content": "Когда нужен монтаж?"},
            {"role": "user", "content": "Через 3 дня, я сам согласую."},
        ]
        await dialog._save_session("phone-1", "+375291112233", messages)
        await storage.upsert_call("phone-1", "+375291112233", "", "Когда нужен монтаж?", "in_progress")
        async def noop(*args):
            return None
        async def stream(*args):
            if False:
                yield None
        pipeline = Pipeline(noop, stream, noop, noop, noop, noop, noop, noop, {},
                            finalize_call=dialog.finalize_call)
        session = CallSession("phone-1", "+375291112233", pipeline)
        await session.close()
        await session.close()
        call = await storage.get_call("phone-1")
        cards = await storage.get_handoff_cards("phone-1")
        return call, cards

    call, cards = asyncio.run(run())
    assert call["action"] == "HOT"
    assert "1 500" in call["transcript"]
    assert len(cards) == 1
    assert cards[0]["budget"] == "1500"
    assert cards[0]["contact_phone"] == "+375291112233"
    assert "Итог: HOT" in cards[0]["comment"]


def test_transferred_call_keeps_manager_context_on_repeated_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tenant", "climateplus")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "calls.db"))
    monkeypatch.setattr(dialog, "cache", CacheService())
    storage = SqliteStorage(settings.sqlite_path)

    async def run():
        await dialog._save_session("phone-2", "+375291112233", [
            {"role": "user", "content": "Нужна чистка кондиционера. Соедините с менеджером."}
        ])
        await storage.upsert_call("phone-2", "+375291112233", "", "Соединяю", "transfer")
        await storage.save_handoff_card("phone-2", {
            "name": "Анна", "need": "чистка кондиционера", "contact_phone": "+375291112233",
            "comment": "Перезвонить после 18:00",
        })
        await dialog.finalize_call("phone-2", "+375291112233")
        await dialog.finalize_call("phone-2", "+375291112233")
        return await storage.get_call("phone-2"), await storage.get_handoff_cards("phone-2")

    call, cards = asyncio.run(run())
    assert call["action"] == "transfer"
    assert len(cards) == 1
    assert cards[0]["name"] == "Анна"
    assert cards[0]["comment"].count("Итог:") == 1
