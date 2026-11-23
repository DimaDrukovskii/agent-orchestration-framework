import asyncio

import pytest

from src.config import settings
from src.services import dialog
from src.services.cache import CacheService


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'sqlite_path', str(tmp_path / 'voice.db'))
    monkeypatch.setattr(settings, 'cache_backend', 'sqlite')
    monkeypatch.setattr(settings, 'storage_backend', 'sqlite')
    monkeypatch.setattr(dialog, 'cache', CacheService())


def test_reply_does_not_repeat_salutation_after_widget_intro(conversation, monkeypatch):
    class Agent:
        async def run(self, messages, caller_phone, **kwargs):
            text = 'Здравствуйте! Для Камчатки сначала уточним даты.'
            return {'response': text, 'messages': messages + [{'role': 'assistant', 'content': text}], 'pending_transfer': None}
    monkeypatch.setattr(dialog, 'agent_for', lambda p: Agent())
    async def go():
        result = await dialog.run_turn('hello', '', 'Здравствуйте, хочу на Камчатку')
        history = await dialog._get_session('hello')
        return result, history
    result, history = asyncio.run(go())
    assert result['response'] == 'Для Камчатки сначала уточним даты.'
    assert history[-1]['content'] == result['response']


@pytest.mark.parametrize('sentences,expected', [
    (['Здравствуйте!', 'Добрый день! Слушаю вас.'], 'Слушаю вас.'),
    (['Здравствуйте!'], 'Слушаю вас.'),
])
def test_stream_suppresses_separate_repeated_greetings(conversation, monkeypatch, sentences, expected):
    class Agent:
        async def run_stream(self, history, caller, **kwargs):
            for sentence in sentences:
                yield {'type': 'sentence', 'text': sentence}
            text = ' '.join(sentences)
            yield {'type': 'done', 'result': {'response': text, 'messages': history + [{'role': 'assistant', 'content': text}],
                   'pending_transfer': None}}
    monkeypatch.setattr(dialog, 'agent_for', lambda p: Agent())
    async def go():
        return [e async for e in dialog.run_turn_stream('stream-hello', '', 'Здравствуйте')]
    events = asyncio.run(go())
    assert [e['text'] for e in events if e['type'] == 'sentence'] == [expected]
    assert events[-1]['response'] == expected
