import asyncio
import json
import sqlite3
import time
from contextlib import aclosing

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.main import app
from src.services import dialog
from src.services.cache import CacheService
from src.services.delivery import DeliveryOutbox, deliver_one
from src.services.service_requests import ServiceRequests, RequestConflict
from src.services.sqlite_store import SqliteStorage, _db


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'sqlite_path', str(tmp_path / 'service.db'))
    monkeypatch.setattr(settings, 'storage_backend', 'sqlite')
    monkeypatch.setattr(settings, 'cache_backend', 'sqlite')
    monkeypatch.setattr(settings, 'telegram_bot_token', '')
    monkeypatch.setattr(settings, 'telegram_chat_id', '')
    return SqliteStorage(settings.sqlite_path)


async def save_request(store, call='case-1', card=None):
    card = card or {'need': 'Тур в июне', 'contact_phone': '+375', 'name': 'Анна'}
    notice = DeliveryOutbox.notification(call, 'Новая заявка ' + call)
    return await store.save_turn(call, '+375', 'Разговор', 'Передаю запрос', 'transfer', card, notice)


def test_card_patch_keeps_facts_and_revisions(store):
    run(store.save_handoff_card('c', {'need': 'Тур', 'contact_phone': '+375', 'name': 'Анна', 'budget': '50000'}))
    run(store.save_handoff_card('c', {'need': 'Камчатка', 'contact_phone': '+375', 'budget': '   '}))
    card = run(store.get_handoff_cards('c'))[0]
    assert (card['name'], card['budget'], card['need']) == ('Анна', '50000', 'Камчатка')
    with _db(store.path) as conn:
        versions = [json.loads(r[0]) for r in conn.execute('SELECT card_json FROM handoff_revisions ORDER BY id')]
    assert [v['need'] for v in versions] == ['Тур', 'Камчатка']


def test_failed_card_rolls_back_call_and_notification(store):
    with pytest.raises(sqlite3.IntegrityError):
        run(store.save_turn('c', '+375', 't', 'r', 'transfer', {'name': 'Анна'},
                            DeliveryOutbox.notification('c', 'handoff')))
    assert run(store.get_call('c')) is None
    assert run(DeliveryOutbox().report())['counts']['pending'] == 0


def test_commit_and_repeated_save_create_one_request_and_job(store):
    run(save_request(store))
    run(save_request(store))
    requests = run(ServiceRequests().list())
    assert requests['total'] == 1 and requests['items'][0]['status'] == 'new'
    assert run(DeliveryOutbox().report())['counts']['pending'] == 1
    assert len(run(ServiceRequests().detail('case-1'))['events']) == 1


def test_delivery_survives_reopen_and_waits_for_configuration(store):
    run(save_request(store))
    fresh = DeliveryOutbox(store.path)
    assert run(deliver_one(fresh)) is False
    report = run(fresh.report())
    assert report['needs_attention'] is True and report['configured'] is False
    assert report['items'][0]['attempts'] == 0


def test_failure_is_retried_then_delivered(store, monkeypatch):
    from src.services import telegram
    monkeypatch.setattr(settings, 'telegram_bot_token', 'test-only')
    monkeypatch.setattr(settings, 'telegram_chat_id', 'manager-chat')
    run(save_request(store))
    sent = []
    async def send(chat, text):
        sent.append((chat, text))
        return len(sent) > 1
    monkeypatch.setattr(telegram, 'send_message', send)
    outbox = DeliveryOutbox()
    assert run(deliver_one(outbox))
    report = run(outbox.report())
    assert report['counts']['pending'] == 1 and report['items'][0]['attempts'] == 1
    assert run(deliver_one(outbox)) is False  # backoff, no tight retry loop
    with _db(outbox.path) as conn:
        conn.execute('UPDATE delivery_outbox SET available_at=0')
    assert run(deliver_one(DeliveryOutbox()))
    assert run(outbox.report())['counts']['delivered'] == 1
    assert len(sent) == 2
    assert run(ServiceRequests().list())['items'][0]['status'] == 'new'  # notification != acceptance


def test_only_one_worker_claims_job_and_expired_owner_cannot_ack(store):
    run(save_request(store))
    async def go():
        return await asyncio.gather(DeliveryOutbox().claim(), DeliveryOutbox().claim())
    claims = run(go())
    assert sum(c is not None for c in claims) == 1
    old = next(c for c in claims if c)
    with _db(store.path) as conn:
        conn.execute('UPDATE delivery_outbox SET lease_until=0')
    new = run(DeliveryOutbox().claim())
    assert new['owner'] != old['owner']
    run(DeliveryOutbox().settle(old, True))
    assert run(DeliveryOutbox().report())['counts']['sending'] == 1
    run(DeliveryOutbox().settle(new, True))
    assert run(DeliveryOutbox().report())['counts']['delivered'] == 1


def test_two_managers_cannot_take_same_request_and_completion_requires_owner(store):
    run(save_request(store))
    async def go():
        return await asyncio.gather(ServiceRequests().transition('case-1', 'Анна'),
                                    ServiceRequests().transition('case-1', 'Иван'), return_exceptions=True)
    results = run(go())
    assert sum(isinstance(r, RequestConflict) for r in results) == 1
    accepted = next(r for r in results if isinstance(r, dict))
    owner = accepted['assigned_to']
    other = 'Иван' if owner == 'Анна' else 'Анна'
    with pytest.raises(RequestConflict):
        run(ServiceRequests().transition('case-1', other, 'Перезвонил'))
    result = run(ServiceRequests().transition('case-1', owner, 'Даты согласованы с клиентом'))
    assert result['status'] == 'completed'
    run(ServiceRequests().transition('case-1', owner, 'Даты согласованы с клиентом'))
    assert len(run(ServiceRequests().detail('case-1'))['events']) == 3
    run(save_request(store))
    assert run(ServiceRequests().list())['total'] == 0  # later snapshots do not reopen completed work


def test_manager_api_requires_key_and_rejects_empty_result(store, monkeypatch):
    run(save_request(store))
    monkeypatch.setattr(settings, 'manager_api_key', 'test-manager-only')
    client = TestClient(app)
    assert client.get('/api/service/requests').status_code == 401
    assert client.get('/api/service/requests', headers={'X-API-Key': 'test-local-key'}).status_code == 401
    headers = {'X-API-Key': 'test-manager-only'}
    assert client.get('/api/service/requests', headers=headers).json()['total'] == 1
    url = '/api/service/requests/case-1'
    assert client.post(url+'/claim', headers=headers, json={'manager': '   '}).status_code == 422
    assert client.post(url+'/claim', headers=headers, json={'manager': 'Анна'}).status_code == 200
    assert client.post(url+'/complete', headers=headers, json={'manager': 'Анна', 'result': '  '}).status_code == 422
    assert client.post(url+'/complete', headers=headers, json={'manager': 'Иван', 'result': 'x'}).status_code == 409
    assert client.post(url+'/complete', headers=headers, json={'manager': 'Анна', 'result': 'Клиенту перезвонили'}).status_code == 200
    assert client.get(url, headers=headers).json()['result'] == 'Клиенту перезвонили'


def test_concurrent_turns_keep_both_user_messages(store, monkeypatch):
    monkeypatch.setattr(dialog, 'cache', CacheService())
    class Agent:
        async def run(self, messages, caller_phone, **kwargs):
            await asyncio.sleep(0.03)
            return {'messages': messages + [{'role': 'assistant', 'content': 'Ответ'}],
                    'response': 'Ответ', 'pending_transfer': None}
    monkeypatch.setattr(dialog, 'agent_for', lambda p: Agent())
    async def go():
        await asyncio.gather(dialog.run_turn('same', '+375', 'Первая'), dialog.run_turn('same', '+375', 'Вторая'))
        return await dialog._get_session('same')
    messages = run(go())
    assert [m['content'] for m in messages if m['role'] == 'user'] == ['Первая', 'Вторая']


def test_cancelled_stream_releases_session(store, monkeypatch):
    from src.services.session_guard import session_guard
    async def go():
        entered = asyncio.Event()
        async def hold():
            async with session_guard('s'):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(hold())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(1):
            async with session_guard('s'):
                pass
    run(go())


def test_service_status_does_not_expose_contacts_or_message(store):
    run(save_request(store))
    report = run(DeliveryOutbox().report())
    text = json.dumps(report)
    assert '+375' not in text and 'Новая заявка' not in text


def test_changed_completed_request_reopens_without_erasing_history(store):
    run(save_request(store))
    run(ServiceRequests().transition('case-1', 'Анна'))
    run(ServiceRequests().transition('case-1', 'Анна', 'Перезвонили и согласовали июнь'))
    run(save_request(store, card={'need': 'Перенести поездку на июль', 'contact_phone': '+375'}))
    request = run(ServiceRequests().detail('case-1'))
    assert request['status'] == 'new' and request['assigned_to'] is None
    assert request['events'][-2]['result'] == 'Перезвонили и согласовали июнь'
    assert request['events'][-1]['status'] == 'new'


def test_legacy_duplicate_cards_are_archived_before_migration(tmp_path):
    from src.services.sqlite_store import _SCHEMA
    path = str(tmp_path / 'legacy.db')
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        for need in ('Старый запрос', 'Уточнённый запрос'):
            conn.execute('INSERT INTO handoff_cards (call_id,need,contact_phone) VALUES (?,?,?)', ('c', need, '+375'))
    storage = SqliteStorage(path)
    assert len(run(storage.get_handoff_cards('c'))) == 1
    with _db(path) as conn:
        archived = [json.loads(r[0])['need'] for r in conn.execute('SELECT card_json FROM handoff_revisions ORDER BY id')]
    assert archived == ['Старый запрос', 'Уточнённый запрос']
    SqliteStorage(path)
    with _db(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM handoff_revisions').fetchone()[0] == 2


def test_process_exit_releases_shared_session_lock(store):
    import hashlib
    import os
    import subprocess
    import sys
    from src.services.session_guard import session_guard
    directory = store.path + '.session-locks'
    os.makedirs(directory)
    lock_path = directory + '/' + hashlib.sha256(b'shared').hexdigest() + '.lock'
    code = 'import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read()'
    process = subprocess.Popen([sys.executable, '-c', code, lock_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        async def go():
            entered = asyncio.Event()
            async def acquire():
                async with session_guard('shared'):
                    entered.set()
            task = asyncio.create_task(acquire())
            await asyncio.sleep(0.08)
            assert not entered.is_set()
            process.terminate()
            process.wait(timeout=3)
            await asyncio.wait_for(task, 1)
            assert entered.is_set()
        run(go())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


def test_handoff_confirmation_waits_for_commit_and_failure_is_not_accepted(store, monkeypatch):
    monkeypatch.setattr(dialog, 'cache', CacheService())
    order = []
    class Agent:
        async def run_stream(self, messages, caller_phone, **kwargs):
            yield {'type': 'tool', 'name': 'save_handoff_card'}
            yield {'type': 'sentence', 'text': 'Заявку приняла.'}
            yield {'type': 'done', 'result': {'messages': messages, 'response': 'Заявку приняла.',
                   'pending_transfer': None, 'handoff_card': {'need': 'Тур', 'contact_phone': '+375'}}}
    monkeypatch.setattr(dialog, 'agent_for', lambda p: Agent())
    async def persist(**kwargs):
        order.append('commit')
        return False
    monkeypatch.setattr(dialog, '_persist_turn', persist)
    async def go():
        events = []
        async for event in dialog.run_turn_stream('confirmation', '+375', 'да'):
            events.append(event)
            if event['type'] == 'sentence':
                assert order == ['commit']
        return events
    events = run(go())
    assert all(e.get('text') != 'Заявку приняла.' for e in events)
    assert events[-1]['persisted'] is False
    assert events[-1]['response'] == dialog.SAVE_FAILED_REPLY


def test_return_to_previous_details_gets_new_notification(store):
    run(save_request(store))
    run(save_request(store, card={'need': 'Камчатка', 'contact_phone': '+375'}))
    run(save_request(store))
    assert run(DeliveryOutbox().report())['counts']['pending'] == 3
    run(save_request(store))
    assert run(DeliveryOutbox().report())['counts']['pending'] == 3


def test_browser_escalation_requests_real_contact_instead_of_fake_transfer():
    from src.agent.hermes_agent import escalation_result
    messages = [{'role': 'user', 'content': 'Соедините с менеджером'}]
    result = escalation_result(messages, '')
    assert result['pending_transfer'] is None
    assert 'номер' in result['response'] and 'соединяю' not in result['response'].lower()
    assert escalation_result([{'role': 'user', 'content': 'Мой номер +375291112233'}, *messages], '') is None


def test_browser_handoff_stays_in_widget_after_durable_save(store, monkeypatch):
    monkeypatch.setattr(dialog, 'cache', CacheService())
    class Agent:
        async def run_stream(self, messages, caller_phone, **kwargs):
            yield {'type': 'tool', 'name': 'save_handoff_card'}
            yield {'type': 'tool', 'name': 'transfer_to_department'}
            yield {'type': 'sentence', 'text': 'Соединяю с оператором.'}
            yield {'type': 'done', 'result': {'messages': messages + [{'role': 'assistant', 'content': 'Соединяю с оператором.'}],
                   'response': 'Соединяю с оператором.', 'pending_transfer': {'department': 'менеджер'},
                   'handoff_card': {'need': 'Тур', 'contact_phone': '+375291112233'}}}
    monkeypatch.setattr(dialog, 'agent_for', lambda p: Agent())
    events = run(_events(dialog.run_turn_stream('browser', '', 'Мой номер +375291112233')))
    assert events[-1]['transfer'] is None and events[-1]['persisted'] is True
    assert 'сохранён' in events[-1]['response']
    assert not any('соединяю' in e.get('text', '').lower() for e in events)
    assert run(ServiceRequests().list())['items'][0]['contact_phone'] == '+375291112233'


async def _events(events):
    async with aclosing(events):
        return [event async for event in events]


def test_browser_cannot_save_unknown_caller_marker_as_callback_contact(store):
    assert run(dialog._persist_turn('browser-missing', '', [], 'Принято', None,
                                    {'need': 'Тур', 'contact_phone': 'номер звонящего'})) is False
    assert run(ServiceRequests().list())['total'] == 0
