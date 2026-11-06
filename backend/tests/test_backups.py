from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app import db
from app.services import backups


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('AMADEUS_DB_PATH', raising=False)
    db.reset_initialization_cache()
    yield tmp_path
    db.reset_initialization_cache()


def make_database(root, name='control.sqlite3', value='before'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT);"
                                 f"INSERT INTO schema_meta VALUES('schema_version','{db.SCHEMA_VERSION}');"
                                 "CREATE TABLE sample(value TEXT);")
        connection.execute('INSERT INTO sample VALUES(?)', (value,))
        connection.commit()
    return path


def value_at(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute('SELECT value FROM sample').fetchone()[0]


@pytest.mark.parametrize('version', [None, '0.1.1', '0.2.0-rc.1'])
def test_manifest_uses_launcher_version_or_dev(data_root, monkeypatch, version):
    make_database(data_root)
    if version is None:
        monkeypatch.delenv('AMADEUS_APP_VERSION', raising=False)
    else:
        monkeypatch.setenv('AMADEUS_APP_VERSION', version)
    manifest = backups.create_backup()
    assert manifest['app_version'] == (version or 'dev')
    assert backups.list_backups()[0]['app_version'] == (version or 'dev')


def test_snapshot_includes_wal_and_only_product_data(data_root):
    path = make_database(data_root)
    config = data_root / 'auth/passcode.json'
    config.parent.mkdir()
    config.write_text('{}', encoding='utf-8')
    for relative in ['models/weights.bin', 'voice-pack/voice.bin', 'asset-pack/image.png', 'logs/backend.log']:
        target = data_root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b'excluded')
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute("UPDATE sample SET value='in-wal'")
        connection.commit()
        manifest = backups.create_backup()
        directory = data_root / 'backups' / manifest['id']
        assert value_at(directory / 'control.sqlite3') == 'in-wal'
    assert {f['path'] for f in manifest['files']} == {'control.sqlite3', 'auth/passcode.json'}
    assert backups.list_backups()[0]['can_restore']
    (directory / 'control.sqlite3').write_bytes(b'changed')
    assert not backups.list_backups()[0]['can_restore']
    with pytest.raises(backups.BackupError, match='checksum mismatch'):
        backups.schedule_restore(manifest['id'])


def test_failed_snapshot_and_space_leave_no_partial(data_root, monkeypatch):
    make_database(data_root)
    make_database(data_root, 'worldlines/sg/history.sqlite3')
    original = backups._snapshot
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('injected snapshot failure')
        return original(*args)

    monkeypatch.setattr(backups, '_snapshot', fail_second)
    with pytest.raises(backups.BackupError, match='injected snapshot failure'):
        backups.create_backup()
    assert list((data_root / 'backups').iterdir()) == []
    monkeypatch.setattr(backups.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(backups.BackupError) as error:
        backups.create_backup()
    assert error.value.code == 'insufficient_space'
    assert list((data_root / 'backups').iterdir()) == []


@pytest.mark.parametrize('reason', ['manual', 'weekly', 'pre-upgrade', 'pre-restore'])
def test_retention_per_reason(data_root, reason):
    make_database(data_root)
    for _ in range(5):
        backups.create_backup(reason)
    assert len(backups.list_backups()) == (5 if reason == 'manual' else 3)


def test_restore_is_deferred_and_restores_config(data_root):
    path = make_database(data_root)
    config = data_root / 'auth/passcode.json'
    config.parent.mkdir()
    config.write_text('{"saved":true}', encoding='utf-8')
    manifest = backups.create_backup()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE sample SET value='after'")
        connection.commit()
    config.unlink()
    make_database(data_root, 'worldlines/beta/memory.sqlite3')
    assert backups.schedule_restore(manifest['id']) == {'restart_required': True}
    assert value_at(path) == 'after'
    with pytest.raises(backups.BackupError) as error:
        backups.delete_backup(manifest['id'])
    assert error.value.status == 409
    (data_root / '.migration-v12.complete').write_text('{}', encoding='utf-8')
    assert backups.apply_pending_restore() is None
    assert value_at(path) == 'before'
    assert json.loads(config.read_text(encoding='utf-8')) == {'saved': True}
    assert not (data_root / 'worldlines/beta/memory.sqlite3').exists()
    assert not (data_root / '.migration-v12.complete').exists()
    assert any(item['reason'] == 'pre-restore' for item in backups.list_backups())


def test_restore_failure_rolls_back_all_originals(data_root, monkeypatch):
    path = make_database(data_root)
    manifest = backups.create_backup()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE sample SET value='after'")
        connection.commit()
    original_bytes = path.read_bytes()
    backups.schedule_restore(manifest['id'])
    restore = backups._restore_files

    def broken_restore(*args):
        restore(*args)
        raise OSError('injected install failure')

    monkeypatch.setattr(backups, '_restore_files', broken_restore)
    assert 'injected install failure' in backups.apply_pending_restore()
    assert path.read_bytes() == original_bytes
    result = json.loads((data_root / 'restore-result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'restore_failed'
    assert not (data_root / 'restore-request.json').exists()


def test_interrupted_restore_rolls_back_on_restart(data_root):
    path = make_database(data_root)
    original = path.read_bytes()
    selected = backups.create_backup()
    rollback = backups.create_backup('pre-restore')
    saved = data_root / 'backups' / rollback['id'] / 'originals/control.sqlite3'
    saved.parent.mkdir()
    path.replace(saved)
    path.write_bytes(b'incomplete restore')
    backups.atomic_json(data_root / 'restore-request.json', {
        'id': selected['id'], 'phase': 'applying', 'rollback_id': rollback['id'],
        'originals': ['control.sqlite3'],
    })
    assert 'interrupted' in backups.apply_pending_restore()
    assert path.read_bytes() == original


def test_cleanup_failure_does_not_undo_committed_restore(data_root, monkeypatch):
    path = make_database(data_root)
    selected = backups.create_backup()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE sample SET value='after'")
        connection.commit()
    backups.schedule_restore(selected['id'])
    unlink = Path.unlink

    def fail_old_cleanup(self, *args, **kwargs):
        if 'originals' in self.parts:
            raise PermissionError('injected cleanup sharing violation')
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_old_cleanup)
    assert backups.apply_pending_restore() is None
    assert value_at(path) == 'before'
    assert not (data_root / 'restore-request.json').exists()
    assert json.loads((data_root / 'restore-result.json').read_text(encoding='utf-8'))['status'] == 'ok'


@pytest.mark.parametrize('backup_id', ['..', '../control.sqlite3', 'anything', '20261106T000000000000Z-manual/..'])
def test_invalid_ids(data_root, backup_id):
    for function in [backups.load_manifest, backups.schedule_restore, backups.delete_backup]:
        with pytest.raises(backups.BackupError) as error:
            function(backup_id)
        assert error.value.status == 400


def test_manifest_cannot_write_arbitrary_files(data_root):
    make_database(data_root)
    manifest = backups.create_backup()
    path = data_root / 'backups' / manifest['id'] / 'manifest.json'
    manifest['files'][0]['path'] = '../control.sqlite3'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(backups.BackupError):
        backups.schedule_restore(manifest['id'])


def test_backup_api_and_active_turn(app_client, monkeypatch):
    from app.routers import chat_ws

    monkeypatch.setattr(chat_ws, 'sessions', {'test': SimpleNamespace(active_task=SimpleNamespace(done=lambda: False))})
    assert app_client.post('/api/data/backups').status_code == 409
    monkeypatch.setattr(chat_ws, 'sessions', {})
    response = app_client.post('/api/data/backups')
    assert response.status_code == 200
    manifest = response.json()
    assert len(manifest['files']) == 6
    backup_id = manifest['id']
    assert app_client.get('/api/data/backups').json()[0]['can_restore']
    assert app_client.delete('/api/data/backups/' + backup_id).status_code == 200
    assert app_client.get('/api/data/backups').json() == []
