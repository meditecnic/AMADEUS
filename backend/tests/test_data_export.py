from contextlib import closing
from datetime import datetime, timezone
import io
import json
import sqlite3
from types import SimpleNamespace
import zipfile

import pytest

from app import db
from app.services.data_export import PreparedExport


@pytest.fixture
def export_store(tmp_path, monkeypatch):
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('AMADEUS_DB_PATH', raising=False)
    monkeypatch.setattr(db, '_initialize_vec0', lambda path: None)
    db.reset_initialization_cache()
    for wl in ['sg', 'beta']:
        for kind, schema in [('history', db.HISTORY_SCHEMA), ('memory', db.MEMORY_SCHEMA)]:
            db._initialize_sync(db._resolve_path(wl, kind), schema, kind, db.normalize_worldline(wl))
    yield tmp_path
    db.reset_initialization_cache()


def seed_conversation(wl, cid, text='こんにちは 红莉栖'):
    with closing(sqlite3.connect(db._resolve_path(wl, 'history'))) as conn:
        conn.execute("INSERT INTO conversations(id,session_id,title,identity_mode) VALUES(?,'owner',?,'self')", (cid, 'title '+cid))
        message = conn.execute("INSERT INTO messages(session_id,conversation_id,role,content,translation) VALUES('owner',?,'user',?,'译文')", (cid, text)).lastrowid
        conn.commit()
        return message


def unpack(export):
    root = export.root
    chunks = list(export.stream())
    assert len(chunks) > 1 and not root.exists()
    with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name).decode('utf-8') for name in archive.namelist()}


@pytest.mark.parametrize('format', ['json', 'md'])
@pytest.mark.parametrize('scope,expected', [('sg', {'a', 'b'}), ('beta', {'c'}), ('all', {'a', 'b', 'c'})])
def test_conversation_scopes_unicode_roundtrip(export_store, format, scope, expected):
    for wl, cid in [('sg', 'a'), ('sg', 'b'), ('beta', 'c')]:
        seed_conversation(wl, cid)
    files = unpack(PreparedExport('conversations', format, scope, None).prepare())
    assert set(files) == {f'{wl}/conversations.{format}' for wl in (['sg', 'beta'] if scope == 'all' else [scope])}
    ids = set()
    for content in files.values():
        assert 'こんにちは 红莉栖' in content and '译文' in content
        if format == 'json':
            data = json.loads(content)
            assert (data['format'], data['format_version']) == ('amadeus.conversations', 1)
            ids.update(c['id'] for c in data['conversations'])
        else:
            ids.update(cid for cid in ['a', 'b', 'c'] if 'title '+cid in content)
    assert ids == expected


def test_single_conversation_snapshot_includes_wal_and_ignores_later_writes(export_store):
    seed_conversation('sg', 'a', 'committed')
    seed_conversation('sg', 'b', 'other conversation')
    with closing(sqlite3.connect(db._resolve_path('sg', 'history'))) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_autocheckpoint=0')
        conn.execute("UPDATE messages SET content='from WAL' WHERE conversation_id='a'")
        conn.commit()
        # An in-flight writer must not block export or leak uncommitted content.
        conn.execute("UPDATE messages SET content='not committed' WHERE conversation_id='a'")
        export = PreparedExport('conversations', 'json', 'sg', 'a').prepare()
        conn.commit()
        content = unpack(export)['sg/conversations.json']
        assert 'from WAL' in content and 'not committed' not in content and 'other conversation' not in content


def seed_memory(wl='sg'):
    source_id = seed_conversation(wl, 'c1')
    with closing(sqlite3.connect(db._resolve_path(wl, 'memory'))) as conn:
        for name, state, source_state in [('visible', 'active', 'present'), ('forgotten', 'deleted', 'present'),
                                           ('source-deleted', 'active', 'deleted'), ('masked', 'active', 'present'),
                                           ('missing-history', 'active', 'present'), ('receipt-deleted', 'active', 'present')]:
            conn.execute('''INSERT INTO memory_observations(observation_id,session_id,identity_mode,display_text,
                semantic_json,evidence_kind,memory_class,status,created_at,updated_at)
                VALUES(?,'owner','self','RAW PRIVATE SOURCE','{"raw":"PRIVATE"}','direct_user','stable_candidate','attached','now','now')''', (name,))
            conn.execute('''INSERT INTO memory_observation_sources(observation_id,source_message_id,conversation_id,
                source_role,source_fingerprint,source_created_at,source_state,excerpt)
                VALUES(?,?,'c1','user','fp','now',?,'RAW EXCERPT')''', (name, 99999 if name == 'missing-history' else source_id, source_state))
            conn.execute('''INSERT INTO stable_facts(fact_id,session_id,identity_mode,state,active_version,created_at,updated_at)
                VALUES(?,'owner','self',?,1,'now','now')''', (name, state))
            conn.execute('''INSERT INTO stable_fact_versions(fact_id,version_no,display_text,semantic_json,semantic_fingerprint,
                confidence,change_kind,valid_from) VALUES(?,1,?,'{"raw":"PRIVATE"}','fp',0.9,'create','now')''', (name, '[已打码]' if name == 'masked' else name+' text'))
            conn.execute("INSERT INTO stable_fact_evidence VALUES(?,1,?,'primary')", (name, name))
            conn.execute('''INSERT INTO experiences(experience_id,session_id,conversation_id,identity_mode,observation_id,
                display_text,semantic_json,semantic_fingerprint,confidence,status,created_at,updated_at)
                VALUES(?,'owner','c1','self',?,?,'{"raw":"PRIVATE"}','fp',0.9,?,'now','now')''',
                (name, name, '[已打码]' if name == 'masked' else name+' experience', 'deleted' if state == 'deleted' else 'active'))
        conn.execute("INSERT INTO memory_deleted_sources VALUES('owner','self','c1',?,'receipt-deleted','now')", (source_id,))
        # Use a separate source for the receipt, otherwise it correctly suppresses all
        # memories sharing the deleted message, including the visible examples.
        conn.execute("UPDATE memory_deleted_sources SET source_message_id=88888")
        # Explicit fact receipt still excludes that fact; source_state guards experience.
        conn.execute("UPDATE memory_observation_sources SET source_state='deleted' WHERE observation_id='receipt-deleted'")
        conn.commit()


def test_memory_only_visible_display_versions_no_forgotten_or_deleted_sources(export_store):
    seed_memory()
    content = unpack(PreparedExport('memory', 'json', 'all', None).prepare())
    data = json.loads(content['sg/memory.json'])
    assert data['format'] == 'amadeus.memory' and data['format_version'] == 1
    assert {f['fact_id'] for f in data['stable_facts']} == {'visible', 'masked'}
    assert {f['experience_id'] for f in data['experiences']} == {'visible', 'masked'}
    assert data['stable_facts'][0]['identity_mode'] == 'self'
    assert '[已打码]' in content['sg/memory.json']
    for hidden in ['forgotten', 'source-deleted', 'receipt-deleted', 'missing-history', 'RAW', 'PRIVATE', 'semantic_json', 'excerpt']:
        assert hidden not in content['sg/memory.json']
    assert json.loads(content['beta/memory.json'])['stable_facts'] == []


def test_prepared_export_cleanup_on_snapshot_failure_and_early_close(export_store, monkeypatch):
    from app.services import data_export
    seed_conversation('sg', 'a')
    export = PreparedExport('conversations', 'json', 'sg', 'a').prepare()
    stream = export.stream()
    assert next(stream).startswith(b'PK')
    stream.close()
    assert not export.root.exists()
    def fail(*args):
        raise OSError('simulated disk error')
    monkeypatch.setattr(data_export, '_snapshot', fail)
    export = PreparedExport('conversations', 'json', 'all', None)
    with pytest.raises(OSError):
        export.prepare()
    assert not export.root.exists()


def test_zip_stream_has_bounded_chunks_for_large_history(export_store):
    seed_conversation('sg', 'a')
    with closing(sqlite3.connect(db._resolve_path('sg', 'history'))) as conn:
        conn.executemany("INSERT INTO messages(session_id,conversation_id,role,content) VALUES('owner','a','assistant',?)",
                         [(str(i)*4096,) for i in range(600)])
        conn.commit()
    export = PreparedExport('conversations', 'json', 'sg', None).prepare()
    largest = total = 0
    for chunk in export.stream():
        largest = max(largest, len(chunk))
        total += len(chunk)
    assert largest < 100000 and total > 10000


def test_export_and_usage_api_auth_validation_no_network_and_active_turn(app_client, monkeypatch):
    from app.routers import chat_ws
    from app.security import local_transport
    import httpx
    def network_forbidden(*args, **kwargs):
        raise AssertionError('export must be offline')
    monkeypatch.setattr(httpx.AsyncClient, 'send', network_forbidden)
    cid = '../../unsafe name'
    seed_conversation('sg', cid)
    with monkeypatch.context() as active:
        active.setattr(chat_ws, 'sessions', {'active': SimpleNamespace(active_task=SimpleNamespace(done=lambda: False))})
        response = app_client.get('/api/data/export', params=dict(kind='conversations', worldline='sg', conversation_id=cid))
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    filename = response.headers['content-disposition']
    assert datetime.now(timezone.utc).strftime('%Y%m%d') in filename and 'sg' in filename and 'unsafe' not in filename
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ['sg/conversations.json']
    assert app_client.get('/api/data/export?kind=memory&format=md').json() == {'detail': {'code': 'invalid_export_scope'}}
    for params in [dict(kind='other'), dict(kind='conversations', worldline='oops'), dict(kind='memory', conversation_id='x')]:
        assert app_client.get('/api/data/export', params=params).status_code == 422
    missing = app_client.get('/api/data/export?kind=conversations&conversation_id=missing')
    assert missing.status_code == 404 and missing.json()['detail']['code'] == 'conversation_not_found'
    monkeypatch.setattr(local_transport, 'LOCAL_API_TOKEN', b'synthetic-test-token')
    for path in ['/api/data/export?kind=conversations', '/api/data/usage?range=all']:
        assert app_client.get(path).status_code == 401
        cross_origin = app_client.get(path, headers={'authorization': 'Bearer synthetic-test-token', 'origin': 'https://evil.invalid'})
        assert cross_origin.status_code == 200
        assert 'access-control-allow-origin' not in cross_origin.headers
        assert app_client.get(path, headers={'authorization': 'Bearer synthetic-test-token'}).status_code == 200


def test_memory_both_identities_expired_history_and_no_old_versions(export_store):
    seed_memory()
    with closing(sqlite3.connect(db._resolve_path('sg', 'memory'))) as conn:
        conn.execute("UPDATE stable_facts SET identity_mode='okabe' WHERE fact_id='visible'")
        conn.execute("UPDATE memory_observations SET identity_mode='okabe' WHERE observation_id='visible'")
        conn.execute("UPDATE experiences SET identity_mode='okabe',status='expired' WHERE experience_id='visible'")
        conn.execute('''INSERT INTO stable_fact_versions(fact_id,version_no,display_text,semantic_json,
            semantic_fingerprint,confidence,change_kind,valid_from,invalid_at)
            VALUES('masked',2,'OLD UNMASKED CONTENT','{}','fp',0.9,'refine','past','past')''')
        conn.commit()
    content = unpack(PreparedExport('memory', 'json', 'sg', None).prepare())['sg/memory.json']
    data = json.loads(content)
    assert {f['identity_mode'] for f in data['stable_facts']} == {'okabe', 'self'}
    assert any(x['status'] == 'expired' for x in data['experiences'])
    assert 'OLD UNMASKED CONTENT' not in content


def test_real_forget_api_is_absent_from_export(app_client):
    import asyncio
    from app.services.memory_v11.repository import create_stable_fact
    fact = asyncio.run(create_stable_fact(session_id='owner', worldline='sg', identity_mode='self',
        display_text='FORGOTTEN UNIQUE MEMORY', semantic_json={'subject': 'user', 'relation': 'likes', 'object': 'tea'}))
    response = app_client.delete('/api/memory/facts/'+fact['fact_id'], params=dict(
        session_id='owner', worldline='sg', identity_mode='self', expected_version=1))
    assert response.status_code == 200
    response = app_client.get('/api/data/export?kind=memory&worldline=sg')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        content = archive.read('sg/memory.json').decode('utf-8')
    assert fact['fact_id'] not in content and 'FORGOTTEN UNIQUE MEMORY' not in content
