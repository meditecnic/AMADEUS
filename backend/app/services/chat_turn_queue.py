"""In-memory FIFO for each worldline in a chat session."""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from app import models
from app.services import credentials
from app.services.conversation_admission import conversation_admission
from app.services.conversations import ConversationNotFound, conversation_service
from app.services.session_manager import cancel_inflight

if TYPE_CHECKING:
    from app.routers.chat_ws import SessionState
    from app.services.provider_runtime import ProviderSnapshot

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ChatTurn:
    id: str
    owner: str
    worldline: str
    conversation_id: str
    content: str
    outbox: asyncio.Queue
    protocol: int
    queued: bool
    state: SessionState | None = None
    provider: ProviderSnapshot | None = None
    task: asyncio.Task | None = None
    hidden: bool = False
    cancelled: bool = False

    async def put(self, message: tuple[int, dict]) -> bool:
        if self.hidden:
            return False
        _, frame = message
        if frame["type"] != "segment.audio":
            self.outbox.put_nowait((*message, self, None))
            return True
        delivered = asyncio.get_running_loop().create_future()
        self.outbox.put_nowait((*message, self, delivered))
        return await delivered


class ChatTurnQueue:
    def __init__(self, view: SessionState):
        self.view = view
        self.owner: str | None = None
        self.lanes: dict[str, list[ChatTurn]] = {}
        self.runners: dict[str, asyncio.Task] = {}

    def turns(self, worldline: str | None = None):
        if worldline is not None:
            return list(self.lanes.get(worldline, ()))
        return [turn for lane in self.lanes.values() for turn in lane]

    def active(self) -> ChatTurn | None:
        return next(
            (
                turn
                for turn in self.turns(self.view.worldline)
                if turn.owner == self.owner
                and turn.task is not None
                and not turn.task.done()
                and turn.conversation_id == self.view.conversation_id
            ),
            None,
        )

    def cancel_pending_start(self) -> None:
        for turn in self.turns(self.view.worldline):
            if (
                not turn.queued
                and turn.task is None
                and turn.owner == self.owner
                and turn.conversation_id == self.view.conversation_id
            ):
                turn.cancelled = True
                return

    def busy(self, worldline: str, conversation_id: str) -> bool:
        return any(turn.conversation_id == conversation_id for turn in self.turns(worldline))

    def emit(self, turn: ChatTurn, frame: dict) -> None:
        if turn.owner == self.owner and turn.protocol >= 2:
            turn.outbox.put_nowait(
                (
                    None,
                    {
                        **frame,
                        "conversation_id": turn.conversation_id,
                        "worldline": turn.worldline,
                    },
                )
            )

    def publish(self, turn: ChatTurn) -> None:
        matching = [
            other
            for other in self.turns(turn.worldline)
            if other.owner == turn.owner and other.conversation_id == turn.conversation_id
        ]
        self.emit(
            turn,
            {
                "type": "conversation.turn_state",
                "running": any(other.task is not None for other in matching),
                "queued": [other.id for other in matching if other.task is None],
            },
        )

    def enqueue(self, owner: str, content: str, outbox: asyncio.Queue) -> None:
        view = self.view
        lane = self.lanes.setdefault(view.worldline, [])
        turn = ChatTurn(
            str(uuid4()),
            owner,
            view.worldline,
            view.conversation_id,
            content,
            outbox,
            view.protocol_version,
            queued=bool(lane),
        )
        lane.append(turn)
        self.emit(turn, {"type": "turn.accepted", "task_id": turn.id, "queued": turn.queued})
        if turn.queued:
            self.publish(turn)
        if turn.worldline not in self.runners:
            self.runners[turn.worldline] = asyncio.create_task(self.run(turn.worldline))

    def remove(self, turn: ChatTurn) -> None:
        lane = self.lanes.get(turn.worldline, [])
        if turn in lane:
            lane.remove(turn)
            self.publish(turn)

    async def withdraw(self, owner: str, task_id: str) -> None:
        for turn in self.turns():
            if turn.owner == owner and turn.id == task_id:
                async with conversation_admission(
                    turn.worldline, self.view.session_id, turn.conversation_id
                ):
                    if turn.task is None:
                        self.remove(turn)
                return

    async def erase(self, worldline: str, conversation_id: str) -> None:
        """Called with the target conversation's admission held."""
        for turn in self.turns(worldline):
            if turn.conversation_id == conversation_id:
                self.remove(turn)
                if turn.state is not None:
                    turn.state.erasure_pending = True
                    if turn.cancelled and not turn.task.done():
                        turn.task.cancel()
                    await self.cancel(turn)

    async def cancel(self, turn: ChatTurn) -> None:
        if not turn.cancelled:
            turn.cancelled = True
            if turn.state is not None:
                await cancel_inflight(turn.state)
        elif turn.task is not None:
            await asyncio.gather(turn.task, return_exceptions=True)

    async def disconnect(self, owner: str) -> bool:
        owned = [turn for turn in self.turns() if turn.owner == owner]
        if self.owner == owner:
            self.owner = None
        for turn in owned:
            turn.hidden = True
        # Cancel active turns across worldlines before waiting on their writes.
        await asyncio.gather(*(self.cancel(turn) for turn in owned if turn.task is not None))
        for turn in owned:
            async with conversation_admission(
                turn.worldline, self.view.session_id, turn.conversation_id
            ):
                self.remove(turn)
        return bool(owned)

    async def hide_active(self) -> None:
        turn = self.active()
        if turn is None or turn.hidden:
            return
        from app.routers.chat_ws import stop_turn_voice

        turn.hidden = True
        if turn.state is not None:
            await stop_turn_voice(turn.state)

    async def snapshot(self, turn: ChatTurn, record: dict) -> SessionState:
        view = self.view
        state = copy.copy(view)
        state.turn = turn
        state.active_turn_id = turn.id
        state.active_streams = set()
        state.active_segment_pipeline = None
        state.voice_stopped_turn_id = None
        state.lock = asyncio.Lock()
        state.worldline = turn.worldline
        state.conversation_id = turn.conversation_id
        state.conversation_mode = "history"
        state.history = view.history if not turn.hidden else []
        state.identity_mode = record["identity_mode"]
        if turn.hidden:
            state.provider_id = record.get("provider_id") or state.provider_id
            state.model = record.get("model_id") or state.model
            state.compression_failures = 0
            state.compression_skip_turns = 0
            state.compressing = False
        state.api_key = credentials.credential_store.get(state.provider_id) or ""
        epoch = await models.get_conversation_content_epoch(
            state.session_id,
            turn.worldline,
            turn.conversation_id,
        )
        if turn.queued or turn.hidden or state.content_epoch != epoch:
            await state.reload_history()
        state.content_epoch = epoch
        return state

    async def run(self, worldline: str) -> None:
        from app.routers.chat_ws import process_queued_turn, schedule_history_compression

        view = self.view
        lane = self.lanes[worldline]
        try:
            while lane:
                turn = lane[0]
                lock = conversation_admission(worldline, view.session_id, turn.conversation_id)
                async with lock:
                    if turn not in lane:
                        continue
                    if turn.cancelled or turn.owner != self.owner:
                        self.remove(turn)
                        continue
                    try:
                        record = await conversation_service.require_owned(
                            turn.conversation_id,
                            view.session_id,
                            worldline,
                        )
                        turn.hidden = (view.worldline, view.conversation_id) != (
                            worldline,
                            turn.conversation_id,
                        )
                        state = await self.snapshot(turn, record)
                    except ConversationNotFound:
                        self.remove(turn)
                        continue
                    except Exception as exc:
                        logger.warning(
                            "[Turn] could not prepare queued turn (%s)", type(exc).__name__
                        )
                        if turn.owner == self.owner and not turn.hidden:
                            await turn.put(
                                (view.current_epoch, {"type": "error", "message": "Server error"})
                            )
                        self.remove(turn)
                        continue
                    if turn.cancelled or turn.owner != self.owner or turn not in lane:
                        self.remove(turn)
                        continue
                    turn.hidden |= view.current_epoch != state.current_epoch or (
                        view.worldline,
                        view.conversation_id,
                    ) != (worldline, turn.conversation_id)
                    turn.state = state
                    turn.task = state.active_task = asyncio.create_task(
                        process_queued_turn(
                            state,
                            turn.content,
                            turn,
                            state.current_epoch,
                            state.history_epoch,
                        )
                    )
                    self.publish(turn)
                await asyncio.gather(turn.task, return_exceptions=True)
                async with lock:
                    if (
                        not state.erasure_pending
                        and turn.owner == self.owner
                        and (turn.hidden or view.history_epoch == state.history_epoch)
                        and (view.worldline, view.conversation_id)
                        == (worldline, turn.conversation_id)
                    ):
                        view.history = state.history
                        view.memory_summary = state.memory_summary
                        view.content_epoch = state.content_epoch
                        view.last_assistant_emotion = state.last_assistant_emotion
                        view.consecutive_no_emo_tags = state.consecutive_no_emo_tags
                        if turn.provider is not None:
                            await schedule_history_compression(
                                view, turn.provider, turn_state=state
                            )
                    self.remove(turn)
        finally:
            self.runners.pop(worldline, None)
            if not lane:
                self.lanes.pop(worldline, None)
