import asyncio
import time
from array import array
from types import SimpleNamespace

import pytest

from src.agent.hermes_agent import HermesAgent, pop_sentences
from src.config import settings
from src.services import dialog
from src.telephony import audio
from src.telephony.session import CallSession, Pipeline

TONE = array("h", [6000, -6000] * 80)


# --- поток ответа агента ---------------------------------------------------------------------


def _chunk(content=None, tool=None, finish=None):
    calls = None
    if tool:
        index, call_id, name, args = tool
        calls = [SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=args))]
    delta = SimpleNamespace(content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def _stream_of(*chunks):
    async def gen():
        for c in chunks:
            yield c
    return gen()


def _agent(monkeypatch, rounds):
    monkeypatch.setattr(settings, "polza_api_key", "pza-test")
    agent = HermesAgent("polza")
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return _stream_of(*rounds[len(sent) - 1])

    monkeypatch.setattr(agent.client.chat.completions, "create", create)
    return agent, sent


def _collect(agent, messages):
    async def go():
        return [e async for e in agent.run_stream(messages, "+375290000000")]
    return asyncio.run(go())


def test_pop_sentences_splits_on_terminators_only():
    ready, rest = pop_sentences("Тариф стоит 1.5 рубля. Подключить? Да, конечно")
    assert ready == ["Тариф стоит 1.5 рубля.", "Подключить?"] and rest == "Да, конечно"


def test_stream_emits_sentences_as_they_form_and_keeps_history(monkeypatch):
    agent, sent = _agent(monkeypatch, [[_chunk("Тариф RED стоит 750 ₽. "), _chunk("Безлимит и 800 мин"), _chunk("ут.", finish="stop")]])
    events = _collect(agent, [{"role": "user", "content": "сколько стоит RED"}])
    assert [e["type"] for e in events] == ["sentence", "sentence", "done"]
    assert [e["text"] for e in events[:2]] == ["Тариф RED стоит 750 ₽.", "Безлимит и 800 минут."]
    done = events[-1]["result"]
    assert done["response"] == "Тариф RED стоит 750 ₽. Безлимит и 800 минут."
    assert done["messages"][-1] == {"role": "assistant", "content": done["response"]}
    assert sent[0]["stream"] is True and sent[0]["tools"]


def test_stream_speaks_before_tool_call_and_closes_after_transfer(monkeypatch):
    agent, sent = _agent(monkeypatch, [
        [_chunk("Секунду, соединю. "), _chunk(tool=(0, "c1", "transfer_to_department", '{"department": "биллинг",')),
         _chunk(tool=(0, None, None, ' "reason": "списание"}'), finish="tool_calls")],
        [_chunk("Передаю вас в биллинг.", finish="stop")],
    ])
    events = _collect(agent, [{"role": "user", "content": "соедините с оператором"}])
    assert [e["type"] for e in events] == ["sentence", "tool", "sentence", "done"]
    assert events[1]["name"] == "transfer_to_department"
    result = events[-1]["result"]
    assert result["pending_transfer"]["department"] == "биллинг"
    assert result["response"] == "Секунду, соединю. Передаю вас в биллинг."
    assert "tools" not in sent[1]  # после перевода модель только прощается
    roles = [m["role"] for m in result["messages"]]
    assert roles[-3:] == ["assistant", "tool", "assistant"]
    assert result["messages"][-3]["tool_calls"][0]["function"]["arguments"].endswith('"списание"}')


def test_hermes_agent_profile_gets_no_client_tools(monkeypatch):
    monkeypatch.setattr(settings, "hermes_agent_base_url", "http://hermes:8642/v1")
    agent = HermesAgent("hermes_agent")
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return _stream_of(_chunk("Готово.", finish="stop"))

    monkeypatch.setattr(agent.client.chat.completions, "create", create)
    _collect(agent, [{"role": "user", "content": "привет"}])
    assert agent.model == settings.hermes_agent_model and "tools" not in sent[0]


def test_hermes_agent_profile_gets_kb_snippet_in_the_request(monkeypatch):
    # своих инструментов поиска у Hermes нет, поэтому справка из базы МТС подставляется в реплику
    monkeypatch.setattr(settings, "hermes_agent_base_url", "http://hermes:8642/v1")
    agent = HermesAgent("hermes_agent")
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        return _stream_of(_chunk("Готово.", finish="stop"))

    monkeypatch.setattr(agent.client.chat.completions, "create", create)
    _collect(agent, [{"role": "user", "content": "как подключить роуминг за границей"}])
    last_user = [m for m in sent[0]["messages"] if m["role"] == "user"][-1]["content"]
    assert "СПРАВКА ИЗ БАЗЫ ЗНАНИЙ" in last_user and "roaming.md" in last_user


# --- что услышал абонент ---------------------------------------------------------------------


def _store(monkeypatch, history):
    saved = {}

    async def get(session_id):
        return [dict(m) for m in history]

    async def save(session_id, caller, messages):
        saved["messages"] = messages

    monkeypatch.setattr(dialog, "_get_session", get)
    monkeypatch.setattr(dialog, "_save_session", save)
    return saved


def test_interrupt_after_saved_turn_keeps_only_the_heard_part(monkeypatch):
    saved = _store(monkeypatch, [{"role": "user", "content": "тарифы"}, {"role": "assistant", "content": "Длинный ответ про всё."}])
    asyncio.run(dialog.mark_interrupted("s", "+375", "тарифы", "Длинный ответ…", committed=True))
    assert saved["messages"][-1]["content"] == "Длинный ответ… [абонент перебил]"


def test_interrupt_before_save_adds_user_text_and_heard_part(monkeypatch):
    saved = _store(monkeypatch, [])
    asyncio.run(dialog.mark_interrupted("s", "+375", "расскажи про тарифы", "Так, смотрите.", committed=False))
    assert saved["messages"] == [{"role": "user", "content": "расскажи про тарифы"},
                                 {"role": "assistant", "content": "Так, смотрите. [абонент перебил]"}]


def test_interrupt_before_any_words_changes_nothing(monkeypatch):
    saved = _store(monkeypatch, [])
    asyncio.run(dialog.mark_interrupted("s", "+375", "вопрос", "", committed=False))
    assert saved == {}


def test_turn_is_saved_only_when_the_stream_completes(monkeypatch):
    saved = _store(monkeypatch, [])

    class Agent:
        async def run_stream(self, history, phone):
            yield {"type": "sentence", "text": "Привет."}
            yield {"type": "done", "result": {"response": "Привет.", "messages": history + [{"role": "assistant", "content": "Привет."}],
                                              "pending_transfer": None, "chat": [], "links": []}}

    async def persist(**kw):
        return True

    async def deliver(*a):
        return None

    monkeypatch.setattr(dialog, "agent_for", lambda profile: Agent())
    monkeypatch.setattr(dialog, "_persist_turn", persist)
    monkeypatch.setattr(dialog, "_deliver_chat", deliver)

    async def go():
        stream = dialog.run_turn_stream("s", "+375", "привет")
        first = await stream.__anext__()
        assert first["type"] == "sentence" and saved == {}  # оборвали бы здесь: ничего не сохранено
        return [e async for e in stream]

    rest = asyncio.run(go())
    assert rest[-1]["type"] == "done" and saved["messages"][-1]["content"] == "Привет."


# --- живое поведение звонка ------------------------------------------------------------------


def _live_pipeline(events, delay=0.0, tts_seconds=0.5):
    log = {"said": [], "sent": [], "cleared": 0, "marked": [], "turns": []}

    async def synth(text):
        log["said"].append(text)
        return b"\xff" * int(8000 * tts_seconds)

    async def send(mulaw):
        log["sent"].append(len(mulaw))

    async def clear():
        log["cleared"] += 1

    async def noop(*a):
        return None

    async def transcribe(wav):
        return "вопрос"

    async def stream_turn(session_id, caller, text):
        log["turns"].append(text)
        await asyncio.sleep(delay)
        for e in events:
            yield e

    async def mark(*a):
        log["marked"].append(a)

    phrases = {"greeting": "Привет", "goodbye": "Пока", "idle_prompt": "Вы тут?", "error_prompt": "Сбой",
               "transfer_prompt": "Соединяю", "transfer_unavailable": "Передала", "transfer_numbers": {},
               "fillers": ["Секунду."]}
    return Pipeline(transcribe, stream_turn, mark, synth, send, clear, noop, lambda n: noop(), phrases), log


DONE = {"type": "done", "response": "Первая фраза. Вторая фраза.", "transfer": None}
SENTENCES = [{"type": "sentence", "text": "Первая фраза."}, {"type": "sentence", "text": "Вторая фраза."}]


def test_agent_says_a_filler_when_the_answer_is_slow(monkeypatch):
    monkeypatch.setattr(settings, "telephony_filler_after_ms", 50)
    pipeline, log = _live_pipeline(SENTENCES + [DONE], delay=0.3)
    session = CallSession("CA1", "+375", pipeline)
    session._pending = ["вопрос"]
    asyncio.run(session._respond())
    assert log["said"][0] == "Секунду." and log["said"][1:] == ["Первая фраза.", "Вторая фраза."]
    assert session._said and [t for t, _, _ in session._said] == ["Первая фраза.", "Вторая фраза."]  # филлер не в ответе


def test_no_filler_when_the_agent_answers_quickly(monkeypatch):
    monkeypatch.setattr(settings, "telephony_filler_after_ms", 500)
    pipeline, log = _live_pipeline(SENTENCES + [DONE], delay=0.0)
    session = CallSession("CA1", "+375", pipeline)
    session._pending = ["вопрос"]
    asyncio.run(session._respond())
    assert "Секунду." not in log["said"]


def test_heard_part_is_cut_at_the_moment_of_interruption():
    pipeline, _ = _live_pipeline([])
    session = CallSession("CA1", "+375", pipeline)
    now = time.monotonic()
    session._said = [("Первая фраза целиком.", now - 5, 2.0), ("Вторая фраза почти вся здесь.", now - 1, 2.0), ("Третья.", now + 1, 1.0)]
    heard = session._heard(now)
    assert heard.startswith("Первая фраза целиком. Вторая фраза") and heard.endswith("…") and "Третья" not in heard


def test_interrupting_a_long_answer_records_only_what_was_heard():
    async def go():
        pipeline, log = _live_pipeline(SENTENCES + [DONE], tts_seconds=2.0)
        session = CallSession("CA1", "+375", pipeline)
        session._pending = ["вопрос"]
        await session._respond()
        assert session._turn_committed and session.speaking
        session._said = [(t, s - 3, d) for t, s, d in session._said]  # прошло 3 секунды: первая фраза сказана, вторая начата
        await session._interrupt()
        return session, log

    session, log = asyncio.run(go())
    (call_sid, caller, user_text, heard, committed), = log["marked"]
    assert (user_text, committed) == ("вопрос", True)
    assert heard.startswith("Первая фраза.") and "Вторая" in heard and log["cleared"] == 1


def test_interrupting_before_any_words_keeps_the_text_for_merging():
    async def go():
        pipeline, log = _live_pipeline(SENTENCES + [DONE], delay=5.0)
        session = CallSession("CA1", "+375", pipeline)
        session._pending = ["у меня вопрос про тариф"]
        task = asyncio.create_task(session._respond())
        session._respond_task = task
        await asyncio.sleep(0.1)
        session._playback_end = time.monotonic() + 1  # агент «говорил» (филлер), абонент вмешался
        await session._interrupt()
        return session, log

    session, log = asyncio.run(go())
    assert session._pending == ["у меня вопрос про тариф"]  # ничего не услышал: реплика ждёт добавления
    assert log["marked"][0][3] == ""  # в историю не записано ничего (см. mark_interrupted)
