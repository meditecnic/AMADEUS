import json

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import data_location as location
from test_backups import data_root


@pytest.fixture
def managed(data_root, monkeypatch):
    file = data_root.parent / (data_root.name + '-location.json')
    monkeypatch.setenv('AMADEUS_LOCATION_FILE', str(file))
    return file


def test_location_sources_and_previous(data_root, managed, monkeypatch):
    assert location.location()['source'] == 'default'
    managed.write_text(json.dumps({'path': str(data_root), 'previous_path': str(data_root / 'absent')}), encoding='utf-8')
    assert location.location()['source'] == 'file'
    assert not location.location()['previous_exists']
    monkeypatch.delenv('AMADEUS_LOCATION_FILE')
    assert location.location()['source'] == 'env'
    assert not location.location()['movable']


def test_usage_counts_each_file_once_and_skips_links(data_root):
    files = {'worldlines/sg/history.sqlite3-wal': 'conversations_memory',
             'control.sqlite3-shm': 'conversations_memory', 'voice-pack/ref.wav': 'voice_models',
             'models/speech/model.onnx': 'speech_models', 'asset-pack/a.png': 'asset_pack',
             'pi-agent/run/a': 'pi_components', 'logs/a': 'logs',
             'backups/id/control.sqlite3': 'backups', 'unknown/a': 'other'}
    expected = {}
    for relative, category in files.items():
        path = data_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'123')
        expected[category] = expected.get(category, 0) + 3
    try:
        (data_root / 'linked').symlink_to(data_root / 'models', target_is_directory=True)
    except OSError:
        pass
    result = location.disk_usage()
    assert result['total_bytes'] == 3 * len(files)
    for key, count in expected.items():
        assert result['categories'][key] == count
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/data/location').json()['movable'] is False
        assert client.get('/api/data/disk').status_code == 200


def codes(result, key='errors'):
    return {item['code'] for item in result[key]}


def test_target_validation_and_probe_cleanup(data_root, tmp_path_factory, monkeypatch):
    target = tmp_path_factory.mktemp('move-parent') / 'nested' / 'new'
    assert location.check_target(str(target))['ok']
    assert not target.parent.exists()
    assert 'path_not_absolute' in codes(location.check_target('relative'))
    assert 'path_overlap' in codes(location.check_target(str(data_root / 'child')))
    assert 'path_overlap' in codes(location.check_target(str(data_root.parent)))
    target.mkdir(parents=True)
    (target / 'keep').write_bytes(b'x')
    assert 'target_not_empty' in codes(location.check_target(str(target)))
    assert (target / 'keep').read_bytes() == b'x'
    monkeypatch.setenv('ProgramFiles', str(target.parent))
    assert 'system_directory' in codes(location.check_target(str(target)))


def test_space_fat_and_dangerous_drives(data_root, tmp_path_factory, monkeypatch):
    from types import SimpleNamespace
    import shutil

    (data_root / 'file').write_bytes(b'1234567890')
    target = tmp_path_factory.mktemp('sync') / 'new'
    monkeypatch.setenv('OneDriveConsumer', str(target.parent))
    monkeypatch.setattr(location, '_volume', lambda _: (2, 'FAT32'))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    result = location.check_target(str(target))
    assert result['needed_bytes'] == 12
    assert codes(result) == {'insufficient_space', 'filesystem_fat32'}
    assert codes(result, 'warnings') == {'sync_directory', 'removable_drive'}
    monkeypatch.setattr(location, '_volume', lambda _: (4, 'NTFS'))
    assert 'network_drive' in codes(location.check_target(str(target)), 'warnings')


def test_dropbox_and_failed_write_probe(data_root, tmp_path_factory, monkeypatch):
    import tempfile

    base = tmp_path_factory.mktemp('dropbox')
    monkeypatch.setenv('APPDATA', str(base))
    (base / 'Dropbox').mkdir()
    (base / 'Dropbox/info.json').write_text(json.dumps({'personal': {'path': str(base)}}), encoding='utf-8')
    assert 'sync_directory' in codes(location.check_target(str(base / 'new')), 'warnings')
    def denied(**kwargs):
        raise PermissionError('injected write denial')
    monkeypatch.setattr(tempfile, 'TemporaryFile', denied)
    assert 'path_unwritable' in codes(location.check_target(str(base / 'new')))
    assert not (base / 'new').exists()


@pytest.fixture
def relocation_state(data_root):
    from app.services import data_relocation
    data_relocation.reset()
    data_relocation.pause_background = data_relocation.resume_background = None
    yield data_relocation
    data_relocation.abort()
    data_relocation.pause_background = data_relocation.resume_background = None


def test_copy_snapshot_wal_and_switch_retains_old(data_root, managed, tmp_path_factory):
    from contextlib import closing
    import sqlite3
    from test_backups import make_database, value_at

    original = make_database(data_root)
    (data_root / 'notes').write_bytes(b'keep')
    (data_root / 'desktop.lock').write_bytes(b'not portable')
    target = tmp_path_factory.mktemp('copy') / 'new'
    with closing(sqlite3.connect(original)) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute("UPDATE sample SET value='wal'")
        connection.commit()
        assert location.copy_and_switch(str(target)) == {'restart_required': True, 'path': str(target)}
        assert value_at(target / 'control.sqlite3') == 'wal'
        assert value_at(original) == 'wal'
    assert (target / 'notes').read_bytes() == b'keep'
    assert not (target / 'desktop.lock').exists()
    assert not (target / 'control.sqlite3-wal').exists()
    assert json.loads(managed.read_text(encoding='utf-8'))['previous_path'] == str(data_root)
    assert json.loads((target / location.MARKER).read_text(encoding='utf-8'))['format'] == 1


def test_failed_half_copy_cleans_target_and_old_stays_writable(data_root, managed, tmp_path_factory, monkeypatch):
    from app.services import backups
    from test_backups import make_database, value_at
    from contextlib import closing
    import sqlite3

    original = make_database(data_root)
    make_database(data_root, 'worldlines/sg/history.sqlite3')
    target = tmp_path_factory.mktemp('failed') / 'new'
    target.mkdir()
    snapshot = backups._snapshot
    calls = 0
    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('injected halfway failure')
        return snapshot(*args)
    monkeypatch.setattr(backups, '_snapshot', fail_second)
    with pytest.raises(backups.BackupError, match='injected halfway failure'):
        location.copy_and_switch(str(target))
    assert list(target.iterdir()) == []
    assert not managed.exists()
    with closing(sqlite3.connect(original)) as connection:
        connection.execute("UPDATE sample SET value='still writable'")
        connection.commit()
    assert value_at(original) == 'still writable'


def test_checksum_and_position_write_failures_roll_back(data_root, managed, tmp_path_factory, monkeypatch):
    from app.services import backups
    import app.security.passcode as passcode

    (data_root / 'file').write_bytes(b'bytes')
    target = tmp_path_factory.mktemp('checksum') / 'new'
    real_hash = backups._hash
    monkeypatch.setattr(backups, '_hash', lambda p: 'wrong' if p != data_root / 'file' else real_hash(p))
    with pytest.raises(backups.BackupError) as failure:
        location.copy_and_switch(str(target))
    assert failure.value.code == 'move_checksum_failed'
    assert not target.exists()
    monkeypatch.setattr(backups, '_hash', real_hash)
    real_write = passcode.atomic_json
    def fail_pointer(path, value):
        if path == managed:
            raise OSError('position file locked')
        real_write(path, value)
    monkeypatch.setattr(passcode, 'atomic_json', fail_pointer)
    with pytest.raises(backups.BackupError, match='position file locked'):
        location.copy_and_switch(str(target))
    assert not target.exists()
    assert (data_root / 'file').read_bytes() == b'bytes'


def test_move_api_warnings_busy_and_failure_reopens_gate(data_root, managed, tmp_path_factory, monkeypatch, relocation_state):
    from app.routers import chat_ws
    from app.services import backups
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import asyncio

    target = tmp_path_factory.mktemp('api') / 'new'
    monkeypatch.setenv('OneDrive', str(target.parent))
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/api/data/location/move', json={'path': str(target)})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'warnings_not_acknowledged'
        monkeypatch.setattr(chat_ws, 'sessions', {'test': SimpleNamespace(active_task=SimpleNamespace(done=lambda: False))})
        response = client.post('/api/data/location/move', json={'path': str(target), 'acknowledge_warnings': True})
        assert response.json()['detail']['code'] == 'conversation_active'
        monkeypatch.setattr(chat_ws, 'sessions', {})
        (data_root / 'restore-request.json').write_text('{}', encoding='utf-8')
        response = client.post('/api/data/location/move', json={'path': str(target), 'acknowledge_warnings': True})
        assert response.json()['detail']['code'] == 'restore_pending'
        (data_root / 'restore-request.json').unlink()
        async def drained(_):
            assert relocation_state.relocating()
        monkeypatch.setattr(relocation_state, 'drain', drained)
        resume = AsyncMock()
        relocation_state.resume_background = resume
        def fail(_):
            raise backups.BackupError('move_failed', 'injected')
        monkeypatch.setattr(location, 'copy_and_switch', fail)
        response = client.post('/api/data/location/move', json={'path': str(target), 'acknowledge_warnings': True})
        assert response.status_code == 500
        assert not relocation_state.relocating()
        resume.assert_awaited_once()
        assert client.get('/api/data/location').status_code == 200


async def test_barrier_drains_http_and_idle_websocket(data_root, relocation_state):
    import asyncio
    from app.services.data_relocation import RelocationMiddleware

    admitted, release = asyncio.Event(), asyncio.Event()
    sent = []
    async def http_app(scope, receive, send):
        admitted.set()
        await release.wait()
    gate = RelocationMiddleware(http_app)
    async def unused():
        await asyncio.Future()
    async def send(message):
        sent.append(message)
    request = asyncio.create_task(gate({'type': 'http'}, unused, send))
    await admitted.wait()
    relocation_state.begin()
    drain = asyncio.create_task(relocation_state.drain(asyncio.current_task()))
    await asyncio.sleep(0)
    assert not drain.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(request, drain), 2)
    await gate({'type': 'http'}, unused, send)
    assert sent[0]['status'] == 503
    relocation_state.abort()
    sent.clear()
    admitted.clear()
    async def ws_app(scope, receive, send):
        await send({'type': 'websocket.accept'})
        admitted.set()
        assert (await receive())['type'] == 'websocket.disconnect'
    socket = asyncio.create_task(RelocationMiddleware(ws_app)({'type': 'websocket', 'path': '/ws/chat'}, unused, send))
    await admitted.wait()
    relocation_state.begin()
    await asyncio.wait_for(relocation_state.drain(asyncio.current_task()), 2)
    await socket
    assert json.loads(sent[1]['text']) == {'type': 'error', 'message': 'Server error'}
    assert sent[2] == {'type': 'websocket.close', 'code': 1000}


def test_successful_move_stays_blocked_until_restart(data_root, managed, tmp_path_factory, monkeypatch, relocation_state):
    from app.routers import chat_ws
    from test_backups import value_at

    monkeypatch.setattr(chat_ws, 'sessions', {})
    target = tmp_path_factory.mktemp('success') / 'new'
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/api/data/location/move', json={'path': str(target)})
        assert response.status_code == 200, response.text
        assert response.json() == {'restart_required': True, 'path': str(target)}
        for method, path in [('get', '/api/settings'), ('get', '/api/data/backups'),
                             ('post', '/api/data/backups'), ('get', '/health')]:
            denied = getattr(client, method)(path)
            assert denied.status_code == 503
            assert denied.json()['detail']['code'] == 'data_relocating'
        with client.websocket_connect('/ws/chat', headers={'origin': 'http://localhost:1420', 'host': 'localhost'}) as ws:
            assert ws.receive_json() == {'type': 'error', 'message': 'Server error'}
            assert ws.receive()['code'] == 1000
    assert (target / 'control.sqlite3').is_file()
    assert (data_root / 'control.sqlite3').is_file()


def test_backup_in_progress_refuses_move(data_root, managed, tmp_path_factory, monkeypatch, relocation_state):
    from app.services import backups
    import threading

    target = tmp_path_factory.mktemp('busy') / 'new'
    started, release = threading.Event(), threading.Event()
    def busy():
        with backups._lock:
            started.set()
            release.wait(5)
    with TestClient(app, base_url='http://localhost') as client:
        thread = threading.Thread(target=busy)
        thread.start()
        assert started.wait(2)
        try:
            response = client.post('/api/data/location/move', json={'path': str(target)})
            assert response.status_code == 409 and response.json()['detail']['code'] == 'backup_busy'
            assert not relocation_state.relocating()
        finally:
            release.set()
            thread.join()
