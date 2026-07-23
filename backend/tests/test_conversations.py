from __future__ import annotations

import asyncio
import sqlite3
import json
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock

from app import models
from app import db as db_module
from app.db import init_db, reset_initialization_cache
from app.routers.chat_ws import SessionState, repair_turn_translation
from app.services.conversations import ConversationNotFound, conversation_service
from app.services.credentials import InMemoryCredentialStore
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderSnapshot,
    ProviderTask,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


@pytest.mark.asyncio
async def test_default_conversation_preserves_legacy_session_api(isolated_store):
    default = await conversation_service.get_or_create_default("okabe", "steins_gate")

    await models.save_message("okabe", "user", "El Psy Kongroo")
    await models.save_memory_summary("okabe", "A default-thread summary")

    messages = await models.get_session_messages("okabe")
    assert [message["content"] for message in messages] == ["El Psy Kongroo"]
    assert messages[0]["conversation_id"] == default["id"]
    assert await models.get_latest_summary("okabe") == "A default-thread summary"
    assert (await conversation_service.list_for_session("okabe", "steins_gate"))[0]["is_default"] is True


@pytest.mark.asyncio
async def test_assistant_translation_roundtrips_with_conversation_history(isolated_store):
    conversation = await conversation_service.create("okabe", "steins_gate", title="Bilingual")
    await models.save_message(
        "okabe",
        "assistant",
        "[EMO:intellectual] 理論は再現できる。",
        translation="理论可以复现。",
        conversation_id=conversation["id"],
    )

    messages = await models.get_session_messages(
        "okabe", "steins_gate", conversation["id"]
    )
    assert messages[0]["translation"] == "理论可以复现。"


@pytest.mark.asyncio
async def test_translation_repair_updates_only_owned_assistant_message(isolated_store):
    conversation = await conversation_service.create("okabe", "steins_gate", title="Repair")
    message_id = await models.save_message(
        "okabe",
        "assistant",
        "[EMO:intellectual] 再試行する。",
        conversation_id=conversation["id"],
    )

    updated = await models.update_message_translation(
        message_id,
        "okabe",
        conversation["id"],
        "重试。",
        worldline="steins_gate",
    )
    wrong_owner = await models.update_message_translation(
        message_id,
        "darling",
        conversation["id"],
        "不应写入",
        worldline="steins_gate",
    )

    messages = await models.get_session_messages(
        "okabe", "steins_gate", conversation["id"]
    )
    assert updated is True
    assert wrong_owner is False
    assert messages[0]["translation"] == "重试。"


@pytest.mark.asyncio
async def test_whole_turn_translation_repair_persists_and_rejects_stale_ui_event(isolated_store):
    conversation = await conversation_service.create("okabe", "steins_gate", title="Repair")
    message_id = await models.save_message(
        "okabe",
        "assistant",
        "[EMO:neutral] 全文。",
        conversation_id=conversation["id"],
    )
    session = SessionState("okabe")
    session.conversation_id = conversation["id"]
    session.current_epoch = 2
    session.worldline = "beta"
    queue = asyncio.Queue()

    async def translate(_text: str) -> str:
        return "全文翻译。"

    repaired = await repair_turn_translation(
        session,
        text="全文。",
        message_id=message_id,
        conversation_id=conversation["id"],
        worldline="steins_gate",
        turn_id="turn-1",
        generation=1,
        send_queue=queue,
        translate=translate,
        timeout=0.1,
    )

    messages = await models.get_session_messages(
        "okabe", "steins_gate", conversation["id"]
    )
    assert repaired == "全文翻译。"
    assert messages[0]["translation"] == "全文翻译。"
    assert queue.empty()


@pytest.mark.asyncio
async def test_conversations_are_isolated_by_owner_and_worldline(isolated_store):
    sg = await conversation_service.create("okabe", "steins_gate", title="PhoneWave")
    beta = await conversation_service.create("okabe", "beta", title="Amadeus")
    other = await conversation_service.create("darling", "steins_gate", title="Lab")

    await models.save_message("okabe", "user", "SG message", worldline="steins_gate", conversation_id=sg["id"])
    await models.save_message("okabe", "user", "Beta message", worldline="beta", conversation_id=beta["id"])

    assert [row["content"] for row in await models.get_session_messages("okabe", "steins_gate", sg["id"])] == ["SG message"]
    assert [row["content"] for row in await models.get_session_messages("okabe", "beta", beta["id"])] == ["Beta message"]
    with pytest.raises(ConversationNotFound):
        await conversation_service.require_owned(other["id"], "okabe", "steins_gate")
    with pytest.raises(ConversationNotFound):
        await conversation_service.require_owned(sg["id"], "okabe", "beta")


@pytest.mark.asyncio
async def test_selected_conversation_is_scoped_per_worldline(isolated_store):
    sg = await conversation_service.create("okabe", "steins_gate", title="SG")
    beta = await conversation_service.create("okabe", "beta", title="Beta")

    await conversation_service.select("okabe", "steins_gate", sg["id"])
    await conversation_service.select("okabe", "beta", beta["id"])

    assert (await conversation_service.get_selected("okabe", "steins_gate"))["id"] == sg["id"]
    assert (await conversation_service.get_selected("okabe", "beta"))["id"] == beta["id"]


@pytest.mark.asyncio
async def test_session_state_restores_only_the_selected_conversation(isolated_store):
    selected = await conversation_service.create("okabe", "steins_gate", title="Selected")
    other = await conversation_service.create("okabe", "steins_gate", title="Other")
    await models.save_message(
        "okabe", "user", "selected history", conversation_id=selected["id"]
    )
    await models.save_message(
        "okabe", "user", "other history", conversation_id=other["id"]
    )
    await conversation_service.select("okabe", "steins_gate", selected["id"])

    session = SessionState("okabe")
    await session.load_from_db()

    assert session.conversation_id == selected["id"]
    assert [row["content"] for row in session.history] == ["selected history"]


@pytest.mark.asyncio
async def test_session_restores_credential_for_selected_conversation_provider(
    isolated_store,
    monkeypatch,
):
    selected = await conversation_service.create(
        "okabe",
        "steins_gate",
        title="Kimi",
        provider_id="kimi",
        model_id="kimi-k2.6",
    )
    await conversation_service.select("okabe", "steins_gate", selected["id"])
    store = InMemoryCredentialStore()
    store.set("kimi", "kimi-provider-secret")
    monkeypatch.setattr("app.routers.chat_ws.credential_store", store)

    session = SessionState("okabe")
    await session.load_from_db()

    assert session.provider_id == "kimi"
    assert session.model == "kimi-k2.6"
    assert session.api_key == "kimi-provider-secret"


@pytest.mark.asyncio
async def test_conversation_forget_removes_only_exclusive_memory_evidence(isolated_store):
    target = await conversation_service.create("okabe", "steins_gate", title="Target")
    other = await conversation_service.create("okabe", "steins_gate", title="Other")
    target_message = await models.save_message(
        "okabe", "user", "target", conversation_id=target["id"]
    )
    other_message = await models.save_message(
        "okabe", "user", "other", conversation_id=other["id"]
    )
    now = datetime.now(timezone.utc).isoformat()
    memory_db = await db_module.get_db("steins_gate", "memory")
    try:
        await memory_db.execute(
            """INSERT INTO episodic_memories(
                   session_id,content,confidence,importance,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,?)""",
            ("okabe", "exclusive", 0.9, 0.8, json.dumps([target_message]), now),
        )
        await memory_db.execute(
            """INSERT INTO episodic_memories(
                   session_id,content,confidence,importance,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                "okabe",
                "mixed",
                0.9,
                0.8,
                json.dumps([target_message, other_message]),
                now,
            ),
        )
        await memory_db.execute(
            """INSERT INTO core_facts(
                   session_id,fact_key,fact_value,confidence,importance,is_current,
                   is_pinned,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,1,0,?,?)""",
            ("okabe", "exclusive", "yes", 0.9, 0.8, json.dumps([target_message]), now),
        )
        await memory_db.commit()
    finally:
        await memory_db.close()

    result = await conversation_service.forget(
        str(target["id"]),
        "okabe",
        "steins_gate",
        forget_long_term=True,
    )

    assert result == {
        "messages_deleted": 1,
        "summaries_deleted": 0,
        "episodic_deleted": 1,
        "core_facts_deleted": 1,
    }
    assert await models.get_session_messages(
        "okabe", conversation_id=str(target["id"])
    ) == []
    assert [row["content"] for row in await models.get_session_messages(
        "okabe", conversation_id=str(other["id"])
    )] == ["other"]
    memory_db = await db_module.get_db("steins_gate", "memory")
    try:
        episodes = await (
            await memory_db.execute(
                "SELECT content FROM episodic_memories WHERE session_id='okabe'"
            )
        ).fetchall()
        facts = await (
            await memory_db.execute(
                "SELECT fact_key FROM core_facts WHERE session_id='okabe'"
            )
        ).fetchall()
    finally:
        await memory_db.close()
    assert [row["content"] for row in episodes] == ["mixed"]
    assert facts == []


@pytest.mark.asyncio
async def test_auto_title_uses_turn_provider_and_never_overwrites_manual_title(
    isolated_store,
):
    auto = await conversation_service.create("okabe", "steins_gate")
    manual = await conversation_service.create(
        "okabe", "steins_gate", title="Manual title"
    )
    adapter = AsyncMock()
    adapter.complete_json.return_value = {"title": "时间机器实验"}
    snapshot = ProviderSnapshot(
        provider_id="fake",
        model_id="title-model",
        capabilities=ProviderCapabilities(
            tasks=frozenset({ProviderTask.TITLE}),
            structured_output=True,
        ),
        adapter=adapter,
    )

    updated_auto = await conversation_service.generate_auto_title(
        str(auto["id"]),
        "okabe",
        "steins_gate",
        user_text="时间机器为什么会失败？",
        assistant_text="先检查实验数据。",
        api_key="provider-secret",
        provider_snapshot=snapshot,
    )
    updated_manual = await conversation_service.generate_auto_title(
        str(manual["id"]),
        "okabe",
        "steins_gate",
        user_text="ignored",
        assistant_text="ignored",
        api_key="provider-secret",
        provider_snapshot=snapshot,
    )

    assert updated_auto["title"] == "时间机器实验"
    assert updated_auto["title_source"] == "auto"
    assert updated_manual["title"] == "Manual title"
    assert updated_manual["title_source"] == "manual"
    adapter.complete_json.assert_awaited_once()
    assert adapter.complete_json.await_args.kwargs["model"] == "title-model"


def _create_v1_history(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key,value) VALUES('schema_version','1');
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                turn_id TEXT,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE memory_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            INSERT INTO messages(session_id,role,content) VALUES
                ('okabe','user','legacy one'),
                ('okabe','assistant','legacy two'),
                ('darling','user','legacy other');
            INSERT INTO memory_summaries(session_id,summary) VALUES
                ('okabe','legacy summary');
            """
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_v1_history_migration_backfills_one_default_per_session(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    history_path = tmp_path / "worldlines" / "sg" / "history.sqlite3"
    _create_v1_history(history_path)
    reset_initialization_cache()

    await init_db()

    connection = sqlite3.connect(history_path)
    connection.row_factory = sqlite3.Row
    try:
        version = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        conversations = connection.execute(
            "SELECT session_id,id,is_default,identity_mode FROM conversations ORDER BY session_id"
        ).fetchall()
        messages = connection.execute(
            "SELECT session_id,conversation_id FROM messages ORDER BY id"
        ).fetchall()
        summaries = connection.execute(
            "SELECT session_id,conversation_id FROM memory_summaries ORDER BY id"
        ).fetchall()
        message_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(messages)")
        }
        conversation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)")
        }
    finally:
        connection.close()

    assert version == "4"
    assert "translation" in message_columns
    assert "identity_mode" in conversation_columns
    assert [(row["session_id"], row["is_default"], row["identity_mode"]) for row in conversations] == [
        ("darling", 1, "okabe"),
        ("okabe", 1, "okabe"),
    ]
    by_session = {row["session_id"]: row["id"] for row in conversations}
    assert all(row["conversation_id"] == by_session[row["session_id"]] for row in messages)
    assert summaries[0]["conversation_id"] == by_session["okabe"]


def test_v1_backfill_rolls_back_all_data_changes_on_failure(tmp_path):
    history_path = tmp_path / "history.sqlite3"
    _create_v1_history(history_path)
    connection = sqlite3.connect(history_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        db_module._prepare_history_v2_ddl(connection)
        connection.executescript(
            """
            CREATE TRIGGER fail_conversation_backfill
            BEFORE UPDATE OF conversation_id ON messages BEGIN
              SELECT RAISE(ABORT, 'forced backfill failure');
            END;
            """
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="forced backfill failure"):
            db_module._backfill_history_v2(connection, "steins_gate")

        assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id IS NULL"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "1"
    finally:
        connection.close()


def test_v4_identity_mode_backfill_rejects_non_enum_and_rolls_back(tmp_path, monkeypatch):
    history_path = tmp_path / "history_v3_bad.sqlite3"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(history_path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key,value) VALUES('schema_version','3');
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                title_source TEXT NOT NULL DEFAULT 'auto',
                is_default INTEGER NOT NULL DEFAULT 0,
                is_pinned INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO conversations(id,session_id,title,title_source,is_default,is_pinned)
            VALUES('c1','s1','T','auto',1,0);
            """
        )
        connection.commit()
        db_module._prepare_history_v4_ddl(connection)

        def sabotaged_backfill(conn):
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE conversations SET identity_mode='garbage'")
                bad = conn.execute(
                    """SELECT COUNT(*) FROM conversations
                        WHERE identity_mode IS NULL OR identity_mode NOT IN ('okabe','self')"""
                ).fetchone()[0]
                if bad:
                    raise RuntimeError(
                        f"identity_mode backfill incomplete: invalid_rows={bad}"
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                    ("4",),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        monkeypatch.setattr(db_module, "_backfill_history_v4", sabotaged_backfill)
        with pytest.raises(RuntimeError, match="identity_mode backfill incomplete"):
            db_module._backfill_history_v4(connection)
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "3"
        # Rolled back garbage write: column still okabe from ADD DEFAULT
        assert connection.execute(
            "SELECT identity_mode FROM conversations WHERE id='c1'"
        ).fetchone()[0] == "okabe"
    finally:
        connection.close()
