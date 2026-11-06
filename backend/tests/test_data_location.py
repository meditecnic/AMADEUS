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
