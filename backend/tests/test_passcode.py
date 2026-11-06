import json
from unittest.mock import AsyncMock

import pytest

from app.security import passcode


def test_lifecycle_and_legacy(app_client):
    c = app_client
    assert c.get('/api/auth/state').json() == {'passcode_set': False}
    assert c.post('/api/auth/login', json={}).json() == {'status': 'success', 'easter_egg': False}
    assert c.post('/api/auth/login', json={'passcode': 'Gott würfelt nicht'}).json()['easter_egg']
    assert c.put('/api/auth/passcode', json={'new': 'test-only-phrase'}).status_code == 200
    record = json.loads(passcode.passcode_path().read_text(encoding='utf-8'))
    assert 'test-only-phrase' not in json.dumps(record)
    assert record['algorithm'] == 'scrypt'
    assert c.post('/api/auth/login', json={'passcode': 'Gott würfelt nicht'}).status_code == 401
    assert c.post('/api/auth/salieri', json={'password': 'Gott würfelt nicht'}).status_code == 401
    assert c.post('/api/auth/salieri', json={'password': 'test-only-phrase'}).json() == {'status': 'success'}
    assert c.put('/api/auth/passcode', json={'new': None}).status_code == 401
    assert c.put('/api/auth/passcode', json={'current': 'test-only-phrase', 'new': 'gott wurfelt nicht'}).status_code == 200
    assert c.post('/api/auth/login', json={'passcode': 'gott wurfelt nicht'}).json()['easter_egg']
    assert c.put('/api/auth/passcode', json={'current': 'gott wurfelt nicht', 'new': None}).status_code == 200
    assert c.get('/api/auth/state').json() == {'passcode_set': False}


@pytest.mark.parametrize('content', ['{', 'null', '[]', '{"algorithm":"other"}'])
def test_bad_record_does_not_lock_out(app_client, content):
    path = passcode.passcode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    assert app_client.get('/api/auth/state').json()['passcode_error']
    assert app_client.post('/api/auth/login', json={}).status_code == 200
    path.unlink()
    assert app_client.get('/api/auth/state').json() == {'passcode_set': False}


async def test_backoff_and_atomic_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    await passcode.change(None, 'test-only-phrase')
    monkeypatch.setattr(passcode, '_failures', 0)
    sleep = AsyncMock()
    monkeypatch.setattr(passcode.asyncio, 'sleep', sleep)
    for _ in range(6):
        with pytest.raises(Exception) as error:
            await passcode.login('incorrect')
        assert error.value.status_code == 401
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]
    await passcode.login('test-only-phrase')
    assert passcode._failures == 0
    old = passcode.passcode_path().read_bytes()

    def fail(*args):
        raise OSError('test replace failure')

    monkeypatch.setattr(passcode.os, 'replace', fail)
    with pytest.raises(Exception):
        await passcode.change('test-only-phrase', 'next-test-only-phrase')
    assert passcode.passcode_path().read_bytes() == old
    assert not list(passcode.passcode_path().parent.glob('.tmp-*'))
