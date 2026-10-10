"""Память дольше окна истории: заметки о звонке дописываются в фоне и подставляются в реплику агента."""

import asyncio

from src.agent.hermes_agent import HermesAgent, with_kb_context
from src.config import settings
from src.services import dialog


class FakeCache:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ttl=0):
        self.data[key] = value


def _history(turns):
    messages = []
    for i in range(turns):
        messages += [{"role": "user", "content": f"вопрос {i}"}, {"role": "assistant", "content": f"ответ {i}"}]
    return messages


def test_notes_block_goes_into_the_last_user_message_only():
    messages = [{"role": "user", "content": "привет"}, {"role": "assistant", "content": "здравствуйте"},
                {"role": "user", "content": "а как меня зовут?"}]
    result = with_kb_context(messages, "", notes="Абонента зовут Иван; в семье трое")
    assert "ЗАМЕТКИ" in result[-1]["content"] and "Иван" in result[-1]["content"]
    assert result[0]["content"] == "привет" and "ЗАМЕТКИ" not in messages[-1]["content"]


def test_notes_cover_what_falls_out_of_the_window(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(dialog, "cache", cache)
    monkeypatch.setattr(settings, "llm_max_history_messages", 12)
    seen = {}

    async def summarize(old, dropped):
        seen["dropped"] = [m["content"] for m in dropped]
        return (old + "; " if old else "") + "Иван, 12 сотрудников"

    monkeypatch.setattr(dialog.hermes, "summarize_notes", summarize)
    asyncio.run(dialog.update_notes("s1", _history(10)))  # 20 сообщений, в окне останутся 10 + 2 новых
    assert seen["dropped"][0] == "вопрос 0" and len(seen["dropped"]) == 10
    stored = cache.data[dialog._notes_key("s1")]
    assert stored["covered"] == 10 and "12 сотрудников" in stored["text"]


def test_short_call_makes_no_notes_and_no_model_call(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(dialog, "cache", cache)

    async def boom(old, dropped):
        raise AssertionError("краткому разговору заметки не нужны")

    monkeypatch.setattr(dialog.hermes, "summarize_notes", boom)
    asyncio.run(dialog.update_notes("s2", _history(3)))
    assert dialog._notes_key("s2") not in cache.data


def test_notes_failure_does_not_break_the_call(monkeypatch):
    monkeypatch.setattr(dialog, "cache", FakeCache())

    async def fail(old, dropped):
        raise RuntimeError("шлюз недоступен")

    monkeypatch.setattr(dialog.hermes, "summarize_notes", fail)
    asyncio.run(dialog.update_notes("s3", _history(10)))  # не бросает


def test_agent_receives_saved_notes_on_the_next_turn(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(dialog, "cache", cache)
    cache.data[dialog._notes_key("s4")] = {"text": "Абонента зовут Иван", "covered": 4}
    got = {}

    class FakeAgent:
        async def run_stream(self, history, caller, notes=""):
            got["notes"] = notes
            yield {"type": "done", "result": {"response": "ok", "messages": history, "pending_transfer": None,
                                              "chat": [], "links": []}}

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(dialog, "agent_for", lambda profile: FakeAgent())
    monkeypatch.setattr(dialog, "_get_session", lambda sid: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(dialog, "_save_session", noop)
    monkeypatch.setattr(dialog, "_persist_turn", lambda **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(dialog, "_deliver_chat", noop)

    async def go():
        return [e async for e in dialog.run_turn_stream("s4", "+375", "как меня зовут?")]

    asyncio.run(go())
    assert got["notes"] == "Абонента зовут Иван"
