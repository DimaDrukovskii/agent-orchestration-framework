import asyncio
from types import SimpleNamespace

from src.agent import tools
from src.agent.hermes_agent import HermesAgent, with_kb_context
from src.config import settings


def _kb(tmp_path, monkeypatch, text="Тариф Бизнес Старт стоит 990 рублей в месяц."):
    (tmp_path / "pricing.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(tools, "KNOWLEDGE_DIR", str(tmp_path))


def test_kb_context_returns_matching_snippet(tmp_path, monkeypatch):
    _kb(tmp_path, monkeypatch)
    snippet = tools.kb_context("Сколько стоит тариф Бизнес Старт?")
    assert "990 рублей" in snippet


def test_kb_context_is_empty_without_a_match(tmp_path, monkeypatch):
    _kb(tmp_path, monkeypatch)
    assert tools.kb_context("расскажите про погоду на марсе") == ""


def test_kb_context_ignores_stop_words(tmp_path, monkeypatch):
    _kb(tmp_path, monkeypatch, text="Мы не работаем по воскресеньям, у нас выходной.")
    assert tools.kb_context("а у вас как не так") == ""


def test_kb_context_is_capped(tmp_path, monkeypatch):
    _kb(tmp_path, monkeypatch, text="тариф " + "слово " * 2000)
    assert len(tools.kb_context("тариф", max_chars=500)) <= 500 + len("[…]")


def test_with_kb_context_appends_to_last_user_message_without_mutating():
    history = [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "здравствуйте"},
        {"role": "user", "content": "сколько стоит тариф"},
    ]
    result = with_kb_context(history, "Тариф стоит 990 рублей")
    assert result[-1]["content"].startswith("сколько стоит тариф")
    assert "[СПРАВКА" in result[-1]["content"] and "990 рублей" in result[-1]["content"]
    assert history[-1]["content"] == "сколько стоит тариф"  # исходник не тронут


def test_with_kb_context_is_noop_for_empty_snippet():
    history = [{"role": "user", "content": "привет"}]
    assert with_kb_context(history, "") == history


def _agent(monkeypatch):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    monkeypatch.setattr(settings, "polza_model", "google/gemini-3.5-flash-lite")
    return HermesAgent("polza")


def _capture(agent, monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="Ответ", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    return captured


def test_preretrieval_puts_snippet_in_request_and_drops_search_tool(tmp_path, monkeypatch):
    _kb(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "llm_kb_preretrieval", True)
    agent = _agent(monkeypatch)
    captured = _capture(agent, monkeypatch)
    history = [{"role": "user", "content": "Сколько стоит тариф Бизнес Старт?"}]

    result = asyncio.run(agent.run(history, "+79990000000"))

    names = [t["function"]["name"] for t in captured["tools"]]
    assert "search_knowledge_base" not in names
    assert "transfer_to_department" in names
    assert "990 рублей" in captured["messages"][-1]["content"]
    # в хранилище уходит чистая реплика абонента, без справки
    assert result["messages"][0]["content"] == "Сколько стоит тариф Бизнес Старт?"


def test_preretrieval_off_keeps_search_tool(tmp_path, monkeypatch):
    _kb(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "llm_kb_preretrieval", False)
    agent = _agent(monkeypatch)
    captured = _capture(agent, monkeypatch)
    asyncio.run(agent.run([{"role": "user", "content": "Сколько стоит тариф?"}], "+79990000000"))
    names = [t["function"]["name"] for t in captured["tools"]]
    assert "search_knowledge_base" in names
    assert "[СПРАВКА" not in captured["messages"][-1]["content"]


def test_turn_cap_stops_llm_calls(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_turns", 3)
    agent = _agent(monkeypatch)
    captured = _capture(agent, monkeypatch)
    history = []
    for i in range(4):
        history += [{"role": "user", "content": f"вопрос {i}"}, {"role": "assistant", "content": "ответ"}]
    history.append({"role": "user", "content": "ещё вопрос"})

    result = asyncio.run(agent.run(history, "+79990000000"))

    assert captured == {}  # к модели не обращались
    assert "оператор" in result["response"].lower()
    assert result["messages"][-1]["role"] == "assistant"
