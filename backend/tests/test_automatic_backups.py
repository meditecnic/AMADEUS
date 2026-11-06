from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app import db
from app.services import backups
from test_backups import data_root, make_database


async def test_new_install_and_current_version_do_not_backup(data_root):
    await db.init_db()
    assert backups.list_backups() == []
    db.reset_initialization_cache()
    await db.init_db()
    assert backups.list_backups() == []


async def test_upgrade_snapshot_precedes_every_schema_write(data_root):
    path = make_database(data_root)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE schema_meta SET value='11' WHERE key='schema_version'")
        connection.commit()
    await db.init_db()
    items = backups.list_backups()
    assert len(items) == 1 and items[0]['reason'] == 'pre-upgrade'
    snapshot = data_root / 'backups' / items[0]['id'] / 'control.sqlite3'
    with closing(sqlite3.connect(snapshot)) as connection:
        assert backups._schema(connection) == 11
    with closing(sqlite3.connect(path)) as connection:
        assert backups._schema(connection) == 12


async def test_failed_upgrade_backup_prevents_migration(data_root, monkeypatch):
    path = make_database(data_root)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE schema_meta SET value='11'")
        connection.commit()
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise backups.BackupError('insufficient_space', 'No space for pre-upgrade backup')

    monkeypatch.setattr(backups, 'create_backup', fail)
    with pytest.raises(backups.BackupError, match='No space'):
        await db.init_db()
    assert path.read_bytes() == before
    assert db._migration_state['status'] == 'error'


def test_weekly_threshold_uses_automatic_only(data_root):
    make_database(data_root)
    backups.create_backup('manual')
    backups.weekly_backup_if_due()
    assert len(backups.list_backups()) == 2
    backups.weekly_backup_if_due()
    assert len(backups.list_backups()) == 2
    item = next(item for item in backups.list_backups() if item['reason'] == 'weekly')
    path = data_root / 'backups' / item['id'] / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['created_at'] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    path.write_text(json.dumps(manifest), encoding='utf-8')
    backups.weekly_backup_if_due()
    assert len(backups.list_backups()) == 3


def test_diagnostic_schema_upgrade_is_backed_up(data_root):
    make_database(data_root, 'diagnostics.sqlite3')
    # Diagnostics uses PRAGMA user_version, without schema_meta.
    with closing(sqlite3.connect(data_root / 'diagnostics.sqlite3')) as connection:
        connection.execute('DROP TABLE schema_meta')
        connection.execute('PRAGMA user_version=1')
        connection.commit()
    backups.backup_before_upgrade()
    assert backups.list_backups()[0]['reason'] == 'pre-upgrade'
