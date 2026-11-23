from fastapi.testclient import TestClient

from src.config import settings
from src.main import app
from src.services.spoken_numbers import normalize_phone_sequences


def test_two_authenticated_participants_relay_and_disconnect(monkeypatch):
    monkeypatch.setattr(settings, 'manager_api_key', 'manager-test')
    client = TestClient(app)
    manager = {'X-API-Key': 'manager-test'}
    path = '/api/live/connect'
    assert client.get('/api/live/waiting', headers={'X-API-Key':'test-local-key'}).status_code == 401
    with client.websocket_connect(path) as caller:
        caller.send_json({'role':'caller','key':'test-local-key'})
        assert caller.receive_json()['type'] == 'waiting'
        room, = client.get('/api/live/waiting', headers=manager).json()
        with client.websocket_connect(path) as operator:
            operator.send_json({'role':'manager','key':'manager-test','room':room})
            assert operator.receive_json()['type'] == caller.receive_json()['type'] == 'joined'
            assert not client.get('/api/live/waiting', headers=manager).json()
            for a,b,data in [(caller,operator,b'\x12\x34'),(operator,caller,b'\x45\x67')]:
                a.send_bytes(data)
                assert b.receive_bytes() == data
    assert not client.get('/api/live/waiting', headers=manager).json()


def test_spoken_phone_keeps_zeros_and_incomplete_sequence():
    assert normalize_phone_sequences('плюс три семь пять два девять ноль ноль один два три четыре пять') == '+375290012345'
    assert normalize_phone_sequences('ноль один два') == 'ноль один два'
