"""Durable conversation ownership and selection within isolated worldlines."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.db import default_conversation_id, get_db, normalize_worldline


logger = logging.getLogger(__name__)


class ConversationNotFound(LookupError):
    pass


class DefaultConversationError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(row, worldline: str) -> dict[str, object]:
    result = dict(row)
    result["is_default"] = bool(result["is_default"])
    result["is_pinned"] = bool(result["is_pinned"])
    result["worldline"] = normalize_worldline(worldline)
    return result


class ConversationService:
    def __init__(self) -> None:
        self._title_tasks: set[asyncio.Task] = set()

    async def create(
        self,
        session_id: str,
        worldline: str,
        *,
        title: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        conversation_id = str(uuid.uuid4())
        clean_title = (title or "").strip() or "New Conversation"
        title_source = "manual" if title and title.strip() else "auto"
        db = await get_db(wl, "history")
        try:
            await db.execute(
                """INSERT INTO conversations(
                       id,session_id,title,title_source,is_default,is_pinned,provider_id,model_id
                   ) VALUES(?,?,?,?,0,0,?,?)""",
                (conversation_id, session_id, clean_title, title_source, provider_id, model_id),
            )
            await db.commit()
        finally:
            await db.close()
        return await self.require_owned(conversation_id, session_id, wl)

    async def create_and_select(
        self,
        session_id: str,
        worldline: str,
        *,
        title: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, object]:
        """Materialize a draft and compensate if durable selection fails."""
        created = await self.create(
            session_id,
            worldline,
            title=title,
            provider_id=provider_id,
            model_id=model_id,
        )
        try:
            return await self.select(
                session_id,
                worldline,
                str(created["id"]),
            )
        except Exception:
            try:
                await self.delete(str(created["id"]), session_id, worldline)
            except Exception:
                logger.exception("failed to compensate unselected draft conversation")
            raise

    async def get_or_create_default(self, session_id: str, worldline: str) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        db = await get_db(wl, "history")
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM conversations WHERE session_id=? AND is_default=1",
                    (session_id,),
                )
            ).fetchone()
            if row is None:
                conversation_id = default_conversation_id(session_id, wl)
                await db.execute(
                    """INSERT OR IGNORE INTO conversations(
                           id,session_id,title,title_source,is_default,is_pinned
                       ) VALUES(?,?,?,'auto',1,0)""",
                    (conversation_id, session_id, "Default Conversation"),
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT * FROM conversations WHERE session_id=? AND is_default=1",
                        (session_id,),
                    )
                ).fetchone()
            if row is None:
                raise RuntimeError(f"failed to create default conversation for {session_id!r}")
            return _record(row, wl)
        finally:
            await db.close()

    async def require_owned(
        self,
        conversation_id: str,
        session_id: str,
        worldline: str,
    ) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        db = await get_db(wl, "history")
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM conversations WHERE id=? AND session_id=?",
                    (conversation_id, session_id),
                )
            ).fetchone()
            if row is None:
                raise ConversationNotFound(
                    f"conversation {conversation_id!r} is not owned by session {session_id!r} in {wl}"
                )
            return _record(row, wl)
        finally:
            await db.close()

    async def list_for_session(
        self,
        session_id: str,
        worldline: str,
        *,
        query: str | None = None,
    ) -> list[dict[str, object]]:
        wl = normalize_worldline(worldline)
        await self.get_or_create_default(session_id, wl)
        db = await get_db(wl, "history")
        try:
            where = "session_id=?"
            values: list[object] = [session_id]
            if query and query.strip():
                where += " AND lower(title) LIKE lower(?)"
                values.append(f"%{query.strip()}%")
            rows = await (
                await db.execute(
                    f"""SELECT * FROM conversations WHERE {where}
                        ORDER BY is_pinned DESC, last_active_at DESC, created_at DESC
                        LIMIT 200""",
                    values,
                )
            ).fetchall()
            return [_record(row, wl) for row in rows]
        finally:
            await db.close()

    async def update(
        self,
        conversation_id: str,
        session_id: str,
        worldline: str,
        changes: dict[str, object],
    ) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        await self.require_owned(conversation_id, session_id, wl)
        assignments: list[str] = []
        values: list[object] = []
        if "title" in changes:
            title = str(changes["title"] or "").strip()
            if not title:
                raise ValueError("title must not be empty")
            assignments.extend(["title=?", "title_source='manual'"])
            values.append(title)
        if "is_pinned" in changes:
            assignments.append("is_pinned=?")
            values.append(1 if changes["is_pinned"] else 0)
        if "provider_id" in changes:
            assignments.append("provider_id=?")
            values.append(changes["provider_id"])
        if "model_id" in changes:
            assignments.append("model_id=?")
            values.append(changes["model_id"])
        if not assignments:
            return await self.require_owned(conversation_id, session_id, wl)
        assignments.append("updated_at=?")
        values.append(_utc_now())
        values.extend([conversation_id, session_id])
        db = await get_db(wl, "history")
        try:
            await db.execute(
                f"UPDATE conversations SET {', '.join(assignments)} WHERE id=? AND session_id=?",
                values,
            )
            await db.commit()
        finally:
            await db.close()
        return await self.require_owned(conversation_id, session_id, wl)

    async def set_auto_title(
        self,
        conversation_id: str,
        session_id: str,
        worldline: str,
        title: str,
    ) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        clean = title.strip()
        if not clean:
            raise ValueError("title must not be empty")
        await self.require_owned(conversation_id, session_id, wl)
        db = await get_db(wl, "history")
        try:
            await db.execute(
                """UPDATE conversations SET title=?,updated_at=?
                   WHERE id=? AND session_id=? AND title_source='auto'""",
                (clean, _utc_now(), conversation_id, session_id),
            )
            await db.commit()
        finally:
            await db.close()
        return await self.require_owned(conversation_id, session_id, wl)

    async def generate_auto_title(
        self,
        conversation_id: str,
        session_id: str,
        worldline: str,
        *,
        user_text: str,
        assistant_text: str,
        api_key: str,
        provider_snapshot,
    ) -> dict[str, object]:
        record = await self.require_owned(conversation_id, session_id, worldline)
        if record["title_source"] == "manual":
            return record
        from app.services.provider_registry import ProviderTask

        provider_snapshot.require(ProviderTask.TITLE)
        payload = await provider_snapshot.adapter.complete_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "请为这段对话生成简洁、具体的中文标题。"
                        "只返回 JSON 对象，格式为 {\"title\": \"...\"}。"
                        "不要使用泛化标题，不要超过 20 个汉字。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"用户：{user_text}\n红莉栖：{assistant_text}",
                },
            ],
            api_key=api_key,
            model=provider_snapshot.model_id,
            temperature=0.1,
        )
        title = str(payload.get("title") or "").strip().replace("\n", " ")
        if not title:
            raise ValueError("provider returned an empty conversation title")
        return await self.set_auto_title(
            conversation_id,
            session_id,
            worldline,
            title[:80],
        )

    def schedule_auto_title(self, *args, **kwargs) -> asyncio.Task:
        task = asyncio.create_task(self.generate_auto_title(*args, **kwargs))
        self._title_tasks.add(task)

        def finished(completed: asyncio.Task) -> None:
            self._title_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("automatic conversation title skipped: %s", exc)

        task.add_done_callback(finished)
        return task

    async def close(self) -> None:
        tasks = list(self._title_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def delete(
        self,
        conversation_id: str,
        session_id: str,
        worldline: str,
    ) -> None:
        wl = normalize_worldline(worldline)
        record = await self.require_owned(conversation_id, session_id, wl)
        if record["is_default"]:
            raise DefaultConversationError("default conversation cannot be deleted")
        db = await get_db(wl, "history")
        try:
            await db.execute(
                "DELETE FROM conversations WHERE id=? AND session_id=?",
                (conversation_id, session_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def forget(
        self,
        conversation_id: str,
        session_id: str,
        worldline: str,
        *,
        forget_long_term: bool = False,
    ) -> dict[str, int]:
        wl = normalize_worldline(worldline)
        await self.require_owned(conversation_id, session_id, wl)
        db = await get_db(wl, "history")
        try:
            rows = await (
                await db.execute(
                    "SELECT id FROM messages WHERE session_id=? AND conversation_id=?",
                    (session_id, conversation_id),
                )
            ).fetchall()
            source_ids = {int(row["id"]) for row in rows}
            message_cursor = await db.execute(
                "DELETE FROM messages WHERE session_id=? AND conversation_id=?",
                (session_id, conversation_id),
            )
            summary_cursor = await db.execute(
                "DELETE FROM memory_summaries WHERE session_id=? AND conversation_id=?",
                (session_id, conversation_id),
            )
            await db.commit()
            messages_deleted = max(0, message_cursor.rowcount)
            summaries_deleted = max(0, summary_cursor.rowcount)
        finally:
            await db.close()
        memory_counts = {"episodic_deleted": 0, "core_facts_deleted": 0}
        if forget_long_term:
            from app.services.memory import memory_service

            memory_counts = await memory_service.forget_exclusive_sources(
                session_id,
                wl,
                source_ids,
            )
        return {
            "messages_deleted": messages_deleted,
            "summaries_deleted": summaries_deleted,
            **memory_counts,
        }

    async def select(
        self,
        session_id: str,
        worldline: str,
        conversation_id: str,
    ) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        record = await self.require_owned(conversation_id, session_id, wl)
        db = await get_db("steins_gate", "control")
        try:
            await db.execute(
                """INSERT INTO session_conversation_selections(
                       session_id,worldline,conversation_id,updated_at_utc
                   ) VALUES(?,?,?,?)
                   ON CONFLICT(session_id,worldline) DO UPDATE SET
                       conversation_id=excluded.conversation_id,
                       updated_at_utc=excluded.updated_at_utc""",
                (session_id, wl, conversation_id, _utc_now()),
            )
            await db.commit()
        finally:
            await db.close()
        return record

    async def get_selected(self, session_id: str, worldline: str) -> dict[str, object]:
        wl = normalize_worldline(worldline)
        db = await get_db("steins_gate", "control")
        try:
            row = await (
                await db.execute(
                    """SELECT conversation_id FROM session_conversation_selections
                       WHERE session_id=? AND worldline=?""",
                    (session_id, wl),
                )
            ).fetchone()
        finally:
            await db.close()
        if row is not None:
            try:
                return await self.require_owned(row["conversation_id"], session_id, wl)
            except ConversationNotFound:
                pass
        default = await self.get_or_create_default(session_id, wl)
        return await self.select(session_id, wl, str(default["id"]))


conversation_service = ConversationService()
