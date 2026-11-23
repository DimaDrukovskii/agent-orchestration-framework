from src.agent.tools import execute_tool
from src.config import settings
from src.services.sqlite_store import SqliteStorage
import asyncio


def test_corrected_budget_replaces_previous_card(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tenant", "far_east_travel")
    store = SqliteStorage(str(tmp_path / "calls.db"))
    fields = {"need": "Сикачи-Алян", "contact_phone": "тестовый контакт"}
    travel = {"destination": "Сикачи-Алян", "dates": "август", "travelers": 2,
              "budget_rub": 50000, "intent": "ready"}

    async def run():
        for scope in ("per_person", "group"):
            result = execute_tool("save_handoff_card", {**fields, "travel": {**travel, "budget_scope": scope}})
            await store.save_handoff_card("qa", result["card"])
        return await store.get_handoff_cards("qa")

    card, = asyncio.run(run())
    assert card["budget"] == "50 000 ₽ на всю группу"
    assert card["urgency"] == "август"
    assert "HOT" in card["comment"] and "Путешественников: 2" in card["comment"]
