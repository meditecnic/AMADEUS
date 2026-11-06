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
