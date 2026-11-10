from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from app import db
from app.main import app
from app.services import backups, data_startup
from test_backups import data_root, make_database, value_at


async def test_good_databases(data_root):
    assert await data_startup.initialize() == (True, False)
    assert data_startup.current_status() == {'status': 'ok', 'detail': None}
    db.reset_initialization_cache()
    assert await data_startup.initialize() == (True, True)
    assert backups.list_backups() == []


async def test_startup_removes_only_staging_before_restore_or_open(data_root, monkeypatch):
    make_database(data_root)
    selected = backups.create_backup()
    base = data_root / 'backups'
    for name in ['.tmp-interrupted-one', '.tmp-interrupted-two']:
        partial = base / name / 'worldlines/sg'
        partial.mkdir(parents=True)
        (partial / 'history.sqlite3').write_bytes(b'interrupted snapshot')
    unrelated = base / 'unrelated'
    unrelated.mkdir()
    (unrelated / 'keep.txt').write_text('keep', encoding='utf-8')
    (base / '.tmp-file').write_text('not a directory', encoding='utf-8')
    restore = backups.apply_pending_restore
    check = data_startup.check_databases

    def assert_clean():
        assert not any(path.is_dir() for path in base.glob('.tmp-*'))

    def restore_after_cleanup():
        assert_clean()
        return restore()

    def check_after_cleanup():
        assert_clean()
        return check()

    monkeypatch.setattr(backups, 'apply_pending_restore', restore_after_cleanup)
    monkeypatch.setattr(data_startup, 'check_databases', check_after_cleanup)
    assert await data_startup.initialize() == (True, True)
    assert_clean()
    assert backups.load_manifest(selected['id'])['id'] == selected['id']
    assert (unrelated / 'keep.txt').read_text(encoding='utf-8') == 'keep'
    assert (base / '.tmp-file').is_file()


def test_corruption_keeps_recovery_api_and_existing_ws_error(data_root, monkeypatch):
    from app.security import local_transport

    path = make_database(data_root)
    path.write_bytes(path.read_bytes()[:100])  # All connections are closed.
    monkeypatch.setattr(local_transport, 'LOCAL_API_TOKEN', b'test-local-token')
    headers = {'authorization': 'Bearer test-local-token', 'origin': 'http://localhost:1420'}
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/data/status').status_code == 401
        assert client.get('/health', headers=headers).json() == {'status': 'ok'}
        state = client.get('/api/data/status', headers=headers).json()
        assert state['status'] == 'corrupt'
        assert state['detail']
        assert state['backups'] == []
        response = client.get('/api/settings', headers=headers)
        assert response.status_code == 503
        assert response.json()['detail']['code'] == 'data_unavailable'
        assert client.get('/api/data/backups', headers=headers).json() == []
        with client.websocket_connect('/ws/chat', headers={'origin': 'http://localhost:1420', 'host': 'localhost'},
                subprotocols=['amadeus.local.v1', 'amadeus.token.test-local-token']) as websocket:
            assert websocket.receive_json() == {'type': 'error', 'message': 'Server error'}
            assert websocket.receive()['code'] == 1000


def test_recover_truncated_database_after_restart(data_root, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.sidecar_supervisor, 'ensure_available', AsyncMock(return_value=SimpleNamespace(url=None)))
    monkeypatch.setattr(main.sidecar_supervisor, 'close', AsyncMock())
    path = make_database(data_root)
    selected = backups.create_backup()
    corrupted = path.read_bytes()[:100]
    path.write_bytes(corrupted)
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/data/status').json()['status'] == 'corrupt'
        response = client.post('/api/data/backups/' + selected['id'] + '/restore')
        assert response.json() == {'restart_required': True}
        assert path.read_bytes() == corrupted
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/api/data/status').json()['status'] == 'ok'
        assert client.get('/api/settings').status_code == 200
        assert value_at(path) == 'before'
    saved = list((data_root / 'backups').glob('*-pre-restore/originals/control.sqlite3'))
    assert len(saved) == 1 and saved[0].read_bytes() == corrupted


async def test_quick_check_failure_even_when_database_opens(data_root):
    path = make_database(data_root)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('UPDATE sample SET value=NULL')
        connection.execute('PRAGMA writable_schema=ON')
        connection.execute("UPDATE sqlite_master SET sql='CREATE TABLE sample(value TEXT NOT NULL)' WHERE name='sample'")
        connection.commit()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute('SELECT value FROM sample').fetchone() == (None,)
        assert connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]
    assert await data_startup.initialize() == (False, True)
    assert 'quick_check' in data_startup.current_status()['detail']


async def test_migration_failure_rolls_back_and_stays_in_recovery(data_root, monkeypatch):
    path = make_database(data_root)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE schema_meta SET value='11'")
        connection.commit()
    initialize = db._initialize_sync

    def fail_after_migration(*args, **kwargs):
        initialize(*args, **kwargs)
        raise RuntimeError('injected failure after schema commit')

    monkeypatch.setattr(db, '_initialize_sync', fail_after_migration)
    assert await data_startup.initialize() == (False, True)
    assert data_startup.current_status()['status'] == 'corrupt'
    assert 'restored pre-upgrade backup' in data_startup.current_status()['detail']
    with closing(sqlite3.connect(path)) as connection:
        assert backups._schema(connection) == 11
    assert value_at(path) == 'before'


async def test_restore_validation_failure_reported_without_lockout(data_root):
    path = make_database(data_root)
    selected = backups.create_backup()
    backups.schedule_restore(selected['id'])
    (data_root / 'backups' / selected['id'] / 'control.sqlite3').write_bytes(b'tampered')
    assert await data_startup.initialize() == (True, True)
    assert data_startup.current_status()['status'] == 'restore_failed'
    assert 'checksum mismatch' in data_startup.current_status()['detail']
    assert value_at(path) == 'before'


def test_weekly_backup_does_not_block_startup(data_root, monkeypatch):
    import app.main as main

    make_database(data_root)
    started, release = threading.Event(), threading.Event()

    def delayed_backup(stop):
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(backups, 'weekly_backup_if_due', delayed_backup)
    monkeypatch.setattr(main.sidecar_supervisor, 'ensure_available', AsyncMock(return_value=SimpleNamespace(url=None)))
    monkeypatch.setattr(main.sidecar_supervisor, 'close', AsyncMock())
    try:
        with TestClient(app, base_url='http://localhost') as client:
            assert started.wait(timeout=1)
            assert client.get('/health').status_code == 200
            release.set()
    finally:
        release.set()


def test_shutdown_stops_sqlite_backup_and_removes_staging(data_root, monkeypatch):
    import app.main as main

    make_database(data_root)
    started = threading.Event()
    connect = sqlite3.connect

    class SlowBackupConnection(sqlite3.Connection):
        def backup(self, target, *, progress, **kwargs):
            def slow_progress(*args):
                started.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    progress(*args)
                    time.sleep(0.01)
                progress(*args)

            return super().backup(target, progress=slow_progress, **kwargs)

    def slow_connect(*args, **kwargs):
        return connect(*args, factory=SlowBackupConnection, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', slow_connect)
    monkeypatch.setattr(main.sidecar_supervisor, 'ensure_available', AsyncMock(return_value=SimpleNamespace(url=None)))
    monkeypatch.setattr(main.sidecar_supervisor, 'close', AsyncMock())
    with TestClient(app, base_url='http://localhost') as client:
        assert started.wait(timeout=2)
        assert list((data_root / 'backups').glob('.tmp-*'))
        assert client.get('/health').status_code == 200
        shutdown_started = time.monotonic()
    assert time.monotonic() - shutdown_started < 2
    assert list((data_root / 'backups').iterdir()) == []


def test_upgrade_backup_failure_has_nonzero_process_exit(data_root):
    path = make_database(data_root)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE schema_meta SET value='11'")
        connection.commit()
    script = '''
import asyncio
from app.services import backups, data_startup
def fail(*args, **kwargs):
    raise backups.BackupError('insufficient_space', 'No space for pre-upgrade backup')
backups.create_backup = fail
asyncio.run(data_startup.initialize())
'''
    env = dict(os.environ, AMADEUS_DATA_DIR=str(data_root))
    env.pop('AMADEUS_DB_PATH', None)
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode != 0
    assert 'No space for pre-upgrade backup' in result.stderr
    with closing(sqlite3.connect(path)) as connection:
        assert backups._schema(connection) == 11


@pytest.mark.parametrize("status", ["ok", "restore_failed"])
async def test_ack_restore_result_survives_next_start(data_root, status):
    backups.atomic_json(data_root / "restore-result.json", {"status": status, "detail": "previous result"})
    assert await data_startup.initialize() == (True, False)
    assert data_startup.current_status()["detail"] == "previous result"
    assert data_startup.acknowledge_restore_result() == {"acknowledged": True}
    assert data_startup.current_status() == {"status": "ok", "detail": None}
    assert not (data_root / "restore-result.json").exists()
    db.reset_initialization_cache()
    assert await data_startup.initialize() == (True, True)
    assert data_startup.current_status() == {"status": "ok", "detail": None}


def test_ack_restore_result_endpoint_is_idempotent(app_client):
    for _ in range(2):
        assert app_client.post("/api/data/restore-result/ack").json() == {"acknowledged": True}
    assert app_client.get("/api/data/status").json()["detail"] is None
