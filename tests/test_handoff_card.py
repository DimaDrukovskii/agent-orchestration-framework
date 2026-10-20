import asyncio
from types import SimpleNamespace

from src.agent import tools
from src.agent.hermes_agent import HermesAgent
from src.agent.hermes_prompts import HERMES_SYSTEM_PROMPT
from src.config import settings
from src.services import dialog
from src.services.sqlite_store import SqliteStorage

CARD_COLUMNS = {"id", "call_id", "created_at", "name", "need", "budget", "urgency", "contact_phone", "comment"}


def run(coro):
    return asyncio.run(coro)


# --- инструмент ------------------------------------------------------------------------------


def test_tool_is_registered_in_both_schemas_and_dispatcher():
    assert "save_handoff_card" in [t["name"] for t in tools.TOOLS_SCHEMA]
    assert "save_handoff_card" in [t["function"]["name"] for t in tools.TOOLS_SCHEMA_OPENAI]
    schema = next(t for t in tools.TOOLS_SCHEMA if t["name"] == "save_handoff_card")["input_schema"]
    assert schema["required"] == ["need", "contact_phone"]
    assert set(schema["properties"]) == set(tools.HANDOFF_FIELDS)


def test_tool_returns_card_with_given_fields():
    result = tools.execute_tool("save_handoff_card", {
        "name": "Иван", "need": "подключить интернет в офис", "contact_phone": "+79990000000", "urgency": "на этой неделе",
    })
    assert result["action"] == "handoff_card"
    assert result["card"] == {
        "name": "Иван", "need": "подключить интернет в офис", "urgency": "на этой неделе", "contact_phone": "+79990000000",
    }


def test_tool_refuses_without_need():
    result = tools.execute_tool("save_handoff_card", {"contact_phone": "+79990000000"})
    assert "card" not in result and "need" in result["error"]


def test_tool_refuses_without_contact_phone():
    result = tools.execute_tool("save_handoff_card", {"need": "тариф для семьи"})
    assert "card" not in result and "contact_phone" in result["error"]


def test_tool_treats_blank_required_fields_as_missing():
    result = tools.execute_tool("save_handoff_card", {"need": "   ", "contact_phone": ""})
    assert "card" not in result
    assert "need" in result["error"] and "contact_phone" in result["error"]


def test_missing_fields_skip_what_caller_already_said():
    # абонент уже назвал, что ему нужно: спросить осталось только номер
    known = tools.build_handoff_card({"name": "Ольга", "need": "перенести номер"})
    assert tools.missing_handoff_fields(known) == ["contact_phone"]
    assert tools.missing_handoff_fields({**known, "contact_phone": "+7"}) == []
    error = tools.save_handoff_card(name="Ольга", need="перенести номер")["error"]
    assert "contact_phone" in error and "need" not in error


def test_card_drops_empty_and_unknown_fields():
    result = tools.execute_tool("save_handoff_card", {
        "need": "роуминг в Турции", "contact_phone": "+7", "budget": "", "comment": "  ",
        "passport": "4510 123456", "card_number": "4111111111111111",
    })
    assert result["card"] == {"need": "роуминг в Турции", "contact_phone": "+7"}


# --- хранение --------------------------------------------------------------------------------


def test_card_saved_and_read_back(tmp_path):
    storage = SqliteStorage(str(tmp_path / "t.db"))
    card = {"name": "Иван", "need": "тариф для семьи", "budget": "до 1500", "urgency": "сегодня",
            "contact_phone": "+79990000000", "comment": "двое детей"}

    saved = run(storage.save_handoff_card("call-1", card))

    rows = run(storage.get_handoff_cards("call-1"))
    assert rows == [saved]
    assert {k: rows[0][k] for k in card} == card
    assert rows[0]["call_id"] == "call-1" and rows[0]["created_at"]
    assert run(storage.get_handoff_cards("other")) == []


def test_stored_row_has_only_card_columns_and_null_for_skipped(tmp_path):
    storage = SqliteStorage(str(tmp_path / "t.db"))
    row = run(storage.save_handoff_card("call-1", {"need": "тариф", "contact_phone": "+7", "budget": "", "passport": "x"}))
    assert set(row) == CARD_COLUMNS
    assert row["budget"] is None and row["name"] is None and row["comment"] is None


def test_calls_table_is_unchanged_by_card(tmp_path):
    storage = SqliteStorage(str(tmp_path / "t.db"))
    run(storage.upsert_call("call-1", "+7", "t", "r", "transfer"))
    run(storage.save_handoff_card("call-1", {"need": "тариф", "contact_phone": "+7"}))
    assert set(run(storage.get_call("call-1"))) == {
        "id", "call_id", "caller_phone", "transcript", "agent_response", "action", "department", "confidence",
        "created_at", "updated_at",
    }


def test_caller_phone_mark_is_replaced_without_mutating_card():
    card = {"need": "тариф", "contact_phone": "Номер звонящего"}
    assert dialog.with_caller_phone(card, "+79991112233")["contact_phone"] == "+79991112233"
    assert card["contact_phone"] == "Номер звонящего"
    assert dialog.with_caller_phone({"need": "x", "contact_phone": "+7123"}, "+7999")["contact_phone"] == "+7123"


# --- перевод на менеджера: карточка, затем transfer_to_department ---------------------------


def _chunk(content=None, tool=None, finish=None):
    calls = None
    if tool:
        index, call_id, name, args = tool
        calls = [SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=args))]
    delta = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def _agent(monkeypatch, rounds):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    agent = HermesAgent("polza")
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)

        async def gen():
            for c in rounds[len(sent) - 1]:
                yield c
        return gen()

    monkeypatch.setattr(agent.client.chat.completions, "create", create)
    return agent


def test_card_is_stored_when_agent_transfers_to_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_backend", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "t.db"))
    card_args = '{"name": "Иван", "need": "интернет в офис", "contact_phone": "номер звонящего", "budget": ""}'
    agent = _agent(monkeypatch, [
        [_chunk(tool=(0, "c1", "save_handoff_card", card_args)),
         _chunk(tool=(1, "c2", "transfer_to_department", '{"department": "менеджер", "reason": "интернет в офис"}'),
                finish="tool_calls")],
        [_chunk("Одну секунду.", finish="stop")],
    ])

    async def go():
        events = [e async for e in agent.run_stream([{"role": "user", "content": "да, по этому номеру удобно"}], "+79991112233")]
        result = events[-1]["result"]
        persisted = await dialog._persist_turn("call-7", "+79991112233", result["messages"], result["response"],
                                               result["pending_transfer"], result["handoff_card"])
        return result, persisted

    result, persisted = run(go())

    assert persisted is True
    assert result["pending_transfer"]["department"] == "менеджер"
    rows = run(SqliteStorage(str(tmp_path / "t.db")).get_handoff_cards("call-7"))
    assert len(rows) == 1
    assert rows[0]["need"] == "интернет в офис" and rows[0]["contact_phone"] == "+79991112233"
    assert rows[0]["name"] == "Иван" and rows[0]["budget"] is None


def test_turn_without_card_stores_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_backend", "sqlite")
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "t.db"))
    assert run(dialog._persist_turn("call-8", "+7", [{"role": "user", "content": "привет"}], "Здравствуйте", None)) is True
    assert run(SqliteStorage(str(tmp_path / "t.db")).get_handoff_cards("call-8")) == []


# --- поведение в промпте ---------------------------------------------------------------------


def test_prompt_describes_card_collection():
    assert "save_handoff_card" in HERMES_SYSTEM_PROMPT
    assert "по одному вопросу за раз" in HERMES_SYSTEM_PROMPT
    assert "не настаивай" in HERMES_SYSTEM_PROMPT
    assert "Паспортные данные" in HERMES_SYSTEM_PROMPT
