import asyncio
from types import SimpleNamespace

from src.agent import hermes_agent
from src.agent.hermes_agent import HermesAgent, log_usage, trim_history
from src.config import settings


def _user(i):
    return {"role": "user", "content": f"u{i}"}


def _assistant(i):
    return {"role": "assistant", "content": f"a{i}"}


def test_trim_history_keeps_short_history_untouched():
    history = [_user(1), _assistant(1), _user(2)]
    assert trim_history(history, limit=12) == history


def test_trim_history_keeps_last_messages_and_starts_at_user_turn():
    history = []
    for i in range(10):
        history += [_user(i), _assistant(i)]
    trimmed = trim_history(history, limit=6)
    assert len(trimmed) <= 6
    assert trimmed[0]["role"] == "user"
    assert trimmed[-1] == history[-1]


def test_trim_history_never_splits_a_tool_call_chain():
    history = [
        _user(1), _assistant(1),
        _user(2),
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "результат"},
        _assistant(2),
        _user(3),
    ]
    trimmed = trim_history(history, limit=4)
    assert trimmed[0]["role"] == "user"
    roles = [m["role"] for m in trimmed]
    assert not (roles[0] == "tool")
    for idx, msg in enumerate(trimmed):
        if msg["role"] == "tool":
            assert trimmed[idx - 1].get("tool_calls") or trimmed[idx - 1]["role"] == "tool"


def test_trim_history_does_not_mutate_input():
    history = [_user(i) if i % 2 == 0 else _assistant(i) for i in range(30)]
    copy = list(history)
    trim_history(history, limit=6)
    assert history == copy


def _polza_agent(monkeypatch, model):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    monkeypatch.setattr(settings, "polza_model", model)
    return HermesAgent("polza")


def test_system_message_is_cached_for_claude_on_polza(monkeypatch):
    monkeypatch.setattr(settings, "llm_prompt_cache", True)
    msg = _polza_agent(monkeypatch, "anthropic/claude-sonnet-4").system_message()
    block = msg["content"][0]
    assert msg["role"] == "system"
    assert block["type"] == "text" and block["text"]
    assert block["cache_control"]["type"] == "ephemeral"


def test_system_message_is_plain_for_other_models(monkeypatch):
    monkeypatch.setattr(settings, "llm_prompt_cache", True)
    msg = _polza_agent(monkeypatch, "google/gemini-3.5-flash-lite").system_message()
    assert isinstance(msg["content"], str)


def test_system_message_is_plain_when_cache_disabled(monkeypatch):
    monkeypatch.setattr(settings, "llm_prompt_cache", False)
    msg = _polza_agent(monkeypatch, "anthropic/claude-sonnet-4").system_message()
    assert isinstance(msg["content"], str)


def test_request_respects_token_cap_and_trims_history(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_tokens", 123)
    monkeypatch.setattr(settings, "llm_max_history_messages", 4)
    agent = _polza_agent(monkeypatch, "anthropic/claude-sonnet-4")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    history = []
    for i in range(10):
        history += [_user(i), _assistant(i)]
    history.append(_user(99))
    asyncio.run(agent.run(history, "+79990000000"))

    assert captured["max_tokens"] == 123
    sent = [m for m in captured["messages"] if m["role"] != "system"]
    assert len(sent) <= 4
    assert sent[-1]["content"] == "u99"


def test_log_usage_tolerates_missing_and_partial_usage():
    log_usage(None, "m")
    log_usage(SimpleNamespace(usage=None), "m")
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_tokens_details=None)
    log_usage(SimpleNamespace(usage=usage), "m")


def test_log_usage_reports_cached_tokens(monkeypatch):
    lines = []
    monkeypatch.setattr(hermes_agent.logger, "info", lambda msg, *a, **k: lines.append(msg))
    details = SimpleNamespace(cached_tokens=80)
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_tokens_details=details)
    log_usage(SimpleNamespace(usage=usage), "anthropic/claude-sonnet-4")
    assert "in=100" in lines[0] and "cached=80" in lines[0] and "out=10" in lines[0]


def test_full_history_is_kept_for_storage_while_model_gets_trimmed(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_history_messages", 4)
    agent = _polza_agent(monkeypatch, "anthropic/claude-sonnet-4")

    async def fake_create(**kwargs):
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    history = []
    for i in range(10):
        history += [_user(i), _assistant(i)]
    history.append(_user(99))
    result = asyncio.run(agent.run(history, "+79990000000"))

    assert len(result["messages"]) == len(history) + 1  # всё сохранено + ответ
    assert result["messages"][0] == history[0]


def test_polza_request_disables_reasoning_by_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_reasoning_effort", "none")
    agent = _polza_agent(monkeypatch, "anthropic/claude-haiku-5.5")
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    asyncio.run(agent.run([{"role": "user", "content": "привет"}], "+79990000000"))
    assert captured["extra_body"] == {"reasoning": {"effort": "none"}}


def test_reasoning_param_omitted_when_setting_empty_or_not_polza(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.clear()
        captured.update(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(settings, "llm_reasoning_effort", "")
    agent = _polza_agent(monkeypatch, "anthropic/claude-haiku-5.5")
    monkeypatch.setattr(agent.client.chat.completions, "create", fake_create)
    asyncio.run(agent.run([{"role": "user", "content": "привет"}], "+79990000000"))
    assert "extra_body" not in captured

    monkeypatch.setattr(settings, "llm_reasoning_effort", "none")
    monkeypatch.setattr(settings, "local_base_url", "http://localhost:11434/v1")
    local = HermesAgent("local")
    monkeypatch.setattr(local.client.chat.completions, "create", fake_create)
    asyncio.run(local.run([{"role": "user", "content": "привет"}], "+79990000000"))
    assert "extra_body" not in captured
