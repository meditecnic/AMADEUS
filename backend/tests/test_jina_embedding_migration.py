"""Upgrade synthetic E5 v11 stores without changing any non-index rows."""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest
import sqlite_vec

from app import db as storage
from app.services.memory import EmbeddingAdapter


def connect(path):
    db = sqlite3.connect(path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def snapshot(path, *, exclude=()):
    with connect(path) as db:
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        rows = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if name not in exclude:
                rows[name] = sorted(db.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
        return [row for row in schema if row[2] not in exclude], rows


def seed_legacy_embedding_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript(storage.MEMORY_SCHEMA.replace("CHECK(dimensions=768)", "CHECK(dimensions=384)"))
        storage._ensure_experiences_is_pinned(db)
        storage._ensure_memory_ingest_jobs_retryable(db)
        storage._ensure_memory_deleted_sources(db)
        db.executemany("INSERT INTO schema_meta VALUES(?,?)", (
            ("schema_version", "11"), ("embedding_model", "intfloat/multilingual-e5-small"),
            ("embedding_dimension", "384"),
        ))
        db.execute("INSERT INTO memory_topics VALUES('topic','session','self','coffee','Coffee','t','t')")
        db.execute("INSERT INTO memory_topic_aliases VALUES('topic','session','self','cafe')")
        for fact_id in ('a', 'b', 'c'):
            db.execute("""INSERT INTO stable_facts(fact_id,session_id,identity_mode,topic_id,state,active_version,created_at,updated_at)
                       VALUES(?,'session','self','topic','active',?,'t','t')""", (fact_id, 2 if fact_id == 'a' else 1))
            for version in range(1, 3 if fact_id == 'a' else 2):
                db.execute("""INSERT INTO stable_fact_versions(fact_id,version_no,display_text,semantic_json,semantic_fingerprint,
                           confidence,change_kind,valid_from,invalid_at) VALUES(?,?,?,'{}',?,0.9,'create','t',?)""",
                           (fact_id, version, f'用户喜欢咖啡 {fact_id}{version}', f'{fact_id}{version}', 't' if fact_id == 'a' and version == 1 else None))
                if fact_id != 'a' or version != 1:
                    db.execute("INSERT INTO stable_fact_version_embeddings VALUES(?,?,'intfloat/multilingual-e5-small',384,?)",
                               (fact_id, version, struct.pack('384f', *([1.0] + [0.0] * 383))))
        db.execute("""INSERT INTO memory_observations(observation_id,session_id,identity_mode,display_text,evidence_kind,
                   memory_class,status,created_at,updated_at) VALUES('obs','session','self','咖啡经历','direct_user','episodic','attached','t','t')""")
        db.execute("""INSERT INTO experiences(experience_id,session_id,conversation_id,identity_mode,observation_id,display_text,
                   semantic_json,semantic_fingerprint,confidence,status,created_at,updated_at)
                   VALUES('exp','session','conv','self','obs','咖啡经历','{}','exp-fp',0.9,'active','t','t')""")
        db.execute("INSERT INTO stable_fact_evidence VALUES('a',1,'obs','primary')")
        db.execute("""INSERT INTO core_facts(session_id,fact_key,fact_value,confidence,importance,source_message_ids,created_at)
                   VALUES('session','old','legacy',0.9,0.5,'[1]','t')""")
        db.execute("""INSERT INTO episodic_memories(session_id,content,confidence,importance,source_message_ids,created_at)
                   VALUES('session','legacy episode',0.9,0.5,'[1]','t')""")
        for kind in ('core', 'episodic'):
            blob = struct.pack('384f', *([0.125] * 384))
            db.execute("INSERT INTO memory_embeddings VALUES(?,1,'intfloat/multilingual-e5-small',384,?)", (kind, blob))
            db.execute(f"CREATE VIRTUAL TABLE {kind}_vec USING vec0(embedding float[384])")
            db.execute(f"INSERT INTO {kind}_vec(rowid,embedding) VALUES(1,?)", (blob,))
    return path


@pytest.fixture
def legacy_db(tmp_path):
    return seed_legacy_embedding_db(tmp_path / 'memory.db')


def fake_embedder(monkeypatch, *, fail_at=None):
    texts = []

    async def encode(self, text):
        texts.append(text)
        if len(texts) == fail_at:
            raise RuntimeError('synthetic model failure')
        return [float(len(texts))] + [0.0] * 767

    monkeypatch.setattr(EmbeddingAdapter, 'encode_passage', encode)
    return texts


def embedding_keys(path):
    with connect(path) as db:
        return db.execute('SELECT fact_id,version_no FROM stable_fact_version_embeddings ORDER BY fact_id,version_no').fetchall()


def assert_upgraded(path, original_keys):
    with connect(path) as db:
        meta = dict(db.execute('SELECT * FROM schema_meta'))
        assert meta == {'schema_version': '12', 'embedding_model': storage.EMBEDDING_MODEL, 'embedding_dimension': '768'}
        rows = db.execute('SELECT fact_id,version_no,model,dimensions,vector FROM stable_fact_version_embeddings ORDER BY fact_id,version_no').fetchall()
        assert [(r[0], r[1]) for r in rows] == original_keys
        assert len(rows) == 3
        for n, row in enumerate(rows, 1):
            assert row[2:4] == (storage.EMBEDDING_MODEL, 768)
            assert struct.unpack('768f', row[4]) == (float(n),) + (0.0,) * 767
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            db.execute('UPDATE stable_fact_version_embeddings SET dimensions=384')


def test_existing_index_rows_reindexed_other_tables_unchanged_and_idempotent(legacy_db, monkeypatch):
    unchanged = snapshot(legacy_db, exclude=('schema_meta', 'stable_fact_version_embeddings'))
    original_keys = embedding_keys(legacy_db)
    assert original_keys == [('a', 2), ('b', 1), ('c', 1)]
    texts = fake_embedder(monkeypatch)
    assert storage._initialize_sync(str(legacy_db), storage.MEMORY_SCHEMA, 'memory')
    assert texts == ['用户喜欢咖啡 a2', '用户喜欢咖啡 b1', '用户喜欢咖啡 c1']
    assert_upgraded(legacy_db, original_keys)
    assert snapshot(legacy_db, exclude=('schema_meta', 'stable_fact_version_embeddings')) == unchanged
    after = snapshot(legacy_db)
    assert storage._initialize_sync(str(legacy_db), storage.MEMORY_SCHEMA, 'memory')
    assert len(texts) == 3
    assert snapshot(legacy_db) == after


def test_model_failure_rolls_back_everything_then_next_start_completes(legacy_db, monkeypatch, caplog):
    before = snapshot(legacy_db)
    original_keys = embedding_keys(legacy_db)
    fake_embedder(monkeypatch, fail_at=3)
    assert not storage._initialize_sync(str(legacy_db), storage.MEMORY_SCHEMA, 'memory')
    assert snapshot(legacy_db) == before
    warning = next(record for record in caplog.records if record.name == 'app.db' and record.exc_info)
    assert warning.exc_info[0] is RuntimeError
    assert 'synthetic model failure' in caplog.text
    fake_embedder(monkeypatch)
    assert storage._initialize_sync(str(legacy_db), storage.MEMORY_SCHEMA, 'memory')
    assert_upgraded(legacy_db, original_keys)


@pytest.mark.parametrize('model,dimension', [('unknown', '384'), ('intfloat/multilingual-e5-small', '768'), (storage.EMBEDDING_MODEL, '123')])
def test_unknown_embedding_metadata_still_raises(legacy_db, model, dimension):
    with connect(legacy_db) as db:
        db.execute("UPDATE schema_meta SET value=? WHERE key='embedding_model'", (model,))
        db.execute("UPDATE schema_meta SET value=? WHERE key='embedding_dimension'", (dimension,))
    before = snapshot(legacy_db)
    with pytest.raises(RuntimeError, match='embedding .* mismatch'):
        storage._initialize_sync(str(legacy_db), storage.MEMORY_SCHEMA, 'memory')
    assert snapshot(legacy_db) == before
