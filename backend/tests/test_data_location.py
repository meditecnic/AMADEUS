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
    incoming = asyncio.Queue()
    async def send(message):
        sent.append(message)
        if message['type'] == 'websocket.close':
            await incoming.put({'type': 'websocket.disconnect', 'code': 1000})
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
    receive_callback = incoming.get
    async def ws_app(scope, receive, send):
        await send({'type': 'websocket.accept'})
        admitted.set()
        assert receive is receive_callback
        assert (await receive())['type'] == 'websocket.disconnect'
    socket = asyncio.create_task(RelocationMiddleware(ws_app)({'type': 'websocket', 'path': '/ws/chat'}, receive_callback, send))
    await admitted.wait()
    relocation_state.begin()
    await asyncio.wait_for(relocation_state.drain(asyncio.current_task()), 2)
    await socket
    assert json.loads(sent[1]['text']) == {'type': 'error', 'message': 'Server error'}
    assert sent[2] == {'type': 'websocket.close', 'code': 1000}
    assert not relocation_state._sockets


@pytest.mark.timeout(10)
def test_successful_move_stays_blocked_until_restart(data_root, managed, tmp_path_factory, monkeypatch, relocation_state):
    from app.routers import chat_ws
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(chat_ws, 'sessions', {})
    target = tmp_path_factory.mktemp('success') / 'new'
    with TestClient(app, base_url='http://localhost') as client:
        with client.websocket_connect('/ws/chat', headers={'origin': 'http://localhost:1420', 'host': 'localhost'}) as existing:
            with ThreadPoolExecutor(max_workers=1) as pool:
                moving = pool.submit(client.post, '/api/data/location/move', json={'path': str(target)})
                try:
                    assert existing.receive_json() == {'type': 'error', 'message': 'Server error'}
                    assert existing.receive()['code'] == 1000
                    assert not moving.done(), 'move must wait for the client close acknowledgement'
                finally:
                    existing.close()
                response = moving.result(timeout=3)
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


@pytest.mark.parametrize('kind', ['missing', 'no_marker', 'bad_marker', 'broken_file'])
def test_startup_invalid_location_never_creates_empty_data(data_root, managed, monkeypatch, kind):
    selected = data_root / 'moved'
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(selected))
    managed.write_text(json.dumps({'path': str(selected)}), encoding='utf-8')
    if kind in {'no_marker', 'bad_marker'}:
        selected.mkdir()
    if kind == 'bad_marker':
        (selected / location.MARKER).write_text('{broken', encoding='utf-8')
    if kind == 'broken_file':
        managed.write_text('{broken', encoding='utf-8')
    with pytest.raises(RuntimeError, match='data_location_missing'):
        with TestClient(app, base_url='http://localhost'):
            pass
    assert not (selected / 'control.sqlite3').exists()
    assert not (selected / 'backups').exists()
    if kind in {'missing', 'broken_file'}:
        assert not selected.exists()


def test_valid_selected_location_allows_startup(data_root, managed):
    managed.write_text(json.dumps({'path': str(data_root)}), encoding='utf-8')
    (data_root / location.MARKER).write_text('{"format":1}', encoding='utf-8')
    location.validate_startup()


def previous_record(managed, current, previous):
    managed.write_text(json.dumps({'path': str(current), 'previous_path': str(previous), 'moved_at': 'test'}), encoding='utf-8')


def test_delete_previous_default_and_reject_arbitrary_path(data_root, managed, tmp_path_factory):
    from test_backups import make_database

    old = tmp_path_factory.mktemp('old-default') / 'data'
    make_database(old)
    arbitrary = tmp_path_factory.mktemp('not-recorded')
    (arbitrary / 'keep').write_bytes(b'keep')
    # Start at the default location; install a pointer only after startup.
    with TestClient(app, base_url='http://localhost') as client:
        previous_record(managed, data_root, old)
        response = client.request('DELETE', '/api/data/location/previous', json={'path': str(arbitrary)})
        assert response.status_code == 422
        assert client.delete('/api/data/location/previous', params={'path': str(arbitrary)}).status_code == 422
        assert old.is_dir()
        assert client.delete('/api/data/location/previous').json() == {'status': 'success'}
    assert not old.exists()
    assert (arbitrary / 'keep').read_bytes() == b'keep'
    assert (data_root / 'control.sqlite3').is_file()
    assert json.loads(managed.read_text(encoding='utf-8'))['previous_path'] is None


@pytest.mark.parametrize('kind,expected', [('current', 'previous_path_unsafe'), ('parent', 'previous_path_unsafe'),
                                         ('unknown', 'previous_unrecognized'), ('missing', 'previous_not_found')])
def test_delete_previous_refuses_unsafe_paths(data_root, managed, tmp_path_factory, kind, expected):
    from app.services.backups import BackupError
    from test_backups import make_database

    make_database(data_root)
    unknown = tmp_path_factory.mktemp('unknown')
    old = {'current': data_root, 'parent': data_root.parent, 'unknown': unknown, 'missing': unknown / 'missing'}[kind]
    previous_record(managed, data_root, old)
    with pytest.raises(BackupError) as failure:
        location.delete_previous()
    assert failure.value.code == expected
    assert (data_root / 'control.sqlite3').is_file()
    assert json.loads(managed.read_text(encoding='utf-8'))['previous_path'] == str(old)


def test_partial_delete_failure_keeps_record_and_current(data_root, managed, tmp_path_factory, monkeypatch):
    from app.services.backups import BackupError
    from test_backups import make_database, value_at
    import shutil

    make_database(data_root)
    old = tmp_path_factory.mktemp('old-partial')
    (old / location.MARKER).write_text('{"format":1}', encoding='utf-8')
    disposable = old / 'removed-first'
    disposable.write_bytes(b'x')
    previous_record(managed, data_root, old)
    def fail(path):
        assert path == old
        disposable.unlink()
        raise PermissionError('injected locked old file')
    monkeypatch.setattr(shutil, 'rmtree', fail)
    with pytest.raises(BackupError) as failure:
        location.delete_previous()
    assert failure.value.code == 'previous_delete_failed'
    assert json.loads(managed.read_text(encoding='utf-8'))['previous_path'] == str(old)
    assert value_at(data_root / 'control.sqlite3') == 'before'
    assert not disposable.exists() and old.exists()


def test_delete_requires_healthy_current_data(data_root, managed, tmp_path_factory, monkeypatch):
    from app.services import data_startup
    from app.services.backups import BackupError
    from test_backups import make_database

    make_database(data_root)
    old = tmp_path_factory.mktemp('old-healthy')
    make_database(old)
    previous_record(managed, data_root, old)
    monkeypatch.setattr(data_startup, 'available', lambda: False)
    with pytest.raises(BackupError) as failure:
        location.delete_previous()
    assert failure.value.code == 'data_unavailable'
    assert old.exists()


async def test_cancelled_admitted_http_preserves_cancellation(data_root, relocation_state):
    import asyncio
    from app.services.data_relocation import RelocationMiddleware

    started, cleaned = asyncio.Event(), asyncio.Event()
    handlers = []
    async def handler(scope, receive, send):
        handlers.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    async def unused(*args):
        pass
    caller = asyncio.create_task(RelocationMiddleware(handler)({'type': 'http'}, unused, unused))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cleaned.is_set()
    assert handlers == [caller]
    assert caller not in relocation_state._tasks


async def test_cancelled_speech_awaits_actual_thread(data_root):
    import asyncio
    import threading
    from app.services.speech import SpeechService

    service = SpeechService()
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    def operation():
        started.set()
        release.wait(3)
        done.set()
    caller = asyncio.create_task(service._run(operation))
    assert await asyncio.to_thread(started.wait, 2)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    drain = asyncio.create_task(service.drain_pending())
    await asyncio.sleep(0)
    assert not drain.done() and not done.is_set()
    release.set()
    await asyncio.wait_for(drain, 2)
    assert done.is_set()


@pytest.mark.timeout(8)
async def test_move_waits_for_cancelled_requests_sqlite_thread(data_root, managed, tmp_path_factory, monkeypatch, relocation_state):
    import asyncio
    from contextlib import closing
    import sqlite3
    import threading
    import httpx
    from app.routers import chat_ws
    from app.routers.data import _call
    from test_backups import make_database, value_at

    monkeypatch.setattr(chat_ws, 'sessions', {})
    database = make_database(data_root)
    target = tmp_path_factory.mktemp('cancelled-write') / 'new'
    write_started, release_write, write_finished = threading.Event(), threading.Event(), threading.Event()
    copy_started = threading.Event()
    request_cancelled = asyncio.Event()
    move = None

    def write_database():
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE sample SET value='written after client cancellation'")
            write_started.set()
            assert release_write.wait(4), 'test did not release the SQLite writer'
            connection.commit()
        write_finished.set()

    async def handler(scope, receive, send):
        try:
            await _call(write_database)
        except asyncio.CancelledError:
            request_cancelled.set()
            raise

    async def unused(*args):
        pass

    copy = location.copy_and_switch
    def checked_copy(path):
        assert write_finished.is_set(), 'copy started while cancelled request was still writing'
        assert value_at(database) == 'written after client cancellation'
        copy_started.set()
        return copy(path)
    monkeypatch.setattr(location, 'copy_and_switch', checked_copy)
    request = asyncio.create_task(relocation_state.RelocationMiddleware(handler)({'type': 'http'}, unused, unused))
    try:
        assert await asyncio.to_thread(write_started.wait, 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert request_cancelled.is_set() and not write_finished.is_set()
        assert not relocation_state._tasks and relocation_state._thread_work
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            move = asyncio.create_task(client.post('/api/data/location/move', json={'path': str(target)}))
            async def entered_relocation():
                while not relocation_state.relocating():
                    await asyncio.sleep(0)
            await asyncio.wait_for(entered_relocation(), 2)
            # Wait until the barrier has reached its tracked executor work.
            await asyncio.sleep(0.03)
            assert not move.done() and not copy_started.is_set()
            release_write.set()
            response = await asyncio.wait_for(move, 3)
        assert response.status_code == 200, response.text
        assert copy_started.is_set()
        assert value_at(target / 'control.sqlite3') == 'written after client cancellation'
        assert not relocation_state._thread_work
    finally:
        release_write.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        if move is not None:
            await asyncio.gather(move, return_exceptions=True)
        if relocation_state._thread_work:
            await asyncio.gather(*relocation_state._thread_work, return_exceptions=True)
