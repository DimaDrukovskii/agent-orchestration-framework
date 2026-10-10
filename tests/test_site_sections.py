import asyncio
import json
from types import SimpleNamespace

from src.agent import tools
from src.agent.hermes_agent import HermesAgent
from src.config import settings


def _site_map(tmp_path, monkeypatch, sections):
    path = tmp_path / "site_map.json"
    path.write_text(json.dumps(sections, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tools, "SITE_MAP_PATH", str(path))


SECTIONS = [
    {"id": "tariffs", "title": "Тарифы", "url": "https://example.test/tariffs", "when": "выбор тарифа"},
    {"id": "support", "title": "Поддержка", "url": "https://example.test/support"},
]


def test_show_section_returns_link_for_known_section(tmp_path, monkeypatch):
    _site_map(tmp_path, monkeypatch, SECTIONS)
    assert tools.execute_tool("show_section", {"section": "tariffs"}) == {
        "action": "link", "title": "Тарифы", "url": "https://example.test/tariffs"}
    assert "error" in tools.execute_tool("show_section", {"section": "unknown"})


def test_tool_schema_lists_sections_and_is_absent_without_map(tmp_path, monkeypatch):
    _site_map(tmp_path, monkeypatch, SECTIONS)
    schema = tools.open_section_tool()[0]["function"]
    assert schema["parameters"]["properties"]["section"]["enum"] == ["tariffs", "support"]
    monkeypatch.setattr(tools, "SITE_MAP_PATH", str(tmp_path / "missing.json"))
    assert tools.open_section_tool() == []


def test_only_https_urls_are_accepted(tmp_path, monkeypatch):
    _site_map(tmp_path, monkeypatch, [{"id": "bad", "title": "x", "url": "javascript:alert(1)"}, SECTIONS[0]])
    assert [x["id"] for x in tools.load_site_map()] == ["tariffs"]


def test_agent_returns_links_from_show_section_call(tmp_path, monkeypatch):
    _site_map(tmp_path, monkeypatch, SECTIONS)
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    monkeypatch.setattr(settings, "polza_model", "google/gemini-3.5-flash-lite")
    agent = HermesAgent("polza")
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="show_section", arguments=json.dumps({"section": "tariffs"})))
    call.model_dump = lambda: {"id": "c1", "type": "function", "function": {"name": "show_section", "arguments": "{\"section\": \"tariffs\"}"}}
    replies = [
        (SimpleNamespace(content=None, tool_calls=[call]), "tool_calls"),
        (SimpleNamespace(content="Показываю тарифы.", tool_calls=None), "stop"),
    ]
    seen_tools = []

    async def fake_create(**kwargs):
        seen_tools.append([t["function"]["name"] for t in kwargs.get("tools", [])])
        message, finish = replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=None)

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    result = asyncio.run(agent.run([{"role": "user", "content": "покажите тарифы"}], "+79990000000"))
    assert result["links"] == [{"title": "Тарифы", "url": "https://example.test/tariffs"}]
    assert "show_section" in seen_tools[0]
