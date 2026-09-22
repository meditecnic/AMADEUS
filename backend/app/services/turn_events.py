"""Normalize provider stream chunks into turn events and lock answer/tool mode."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

EMO_COMPLETE = re.compile(r"^\[EMO:[^\]]+\]")
EMO_PREFIX = re.compile(r"^\[(?:E(?:M(?:O(?::[^\]]{0,24})?)?)?)?$")
MAX_NEUTRAL_PREFIX = 64

TurnMode = Literal["tool_mode", "answer_mode"]


@dataclass(slots=True)
class ProviderTurnEvent:
    seq: int
    content: str
    tool_calls: list[dict[str, Any]]
    reasoning: str = ""
    metadata: bool = False
    raw_boundary: str = ""


@dataclass(slots=True)
class ToolCallAssembler:
    """Stream-local index/id/alias map. One call per index or aliased id."""

    calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    index_to_id: dict[int, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def ingest(self, incoming: list[dict[str, Any]] | None, *, seq: int) -> dict[str, dict[str, Any]]:
        if not incoming:
            return self.calls
        for offset, call in enumerate(incoming):
            stable = self._resolve_id(call, offset, seq)
            function = call.get("function") or {}
            slot = self.calls.get(stable)
            if slot is None:
                self.calls[stable] = {
                    "id": stable,
                    "type": call.get("type") or "function",
                    "function": {
                        "name": function.get("name") or "",
                        "arguments": function.get("arguments") or "",
                    },
                }
                continue
            if function.get("name"):
                slot["function"]["name"] = function["name"]
            if function.get("arguments"):
                slot["function"]["arguments"] += function["arguments"]
            if call.get("type"):
                slot["type"] = call["type"]
        return self.calls

    def _resolve_id(self, call: dict[str, Any], offset: int, seq: int) -> str:
        raw_index = call.get("index")
        try:
            index = int(offset if raw_index is None else raw_index)
        except (TypeError, ValueError):
            index = offset
        names = []
        for key in ("id", "call_id", "item_id"):
            value = call.get(key)
            if value is not None and str(value).strip() != "":
                names.append(str(value).strip())
        if index in self.index_to_id:
            stable = self.index_to_id[index]
            for name in names:
                self.aliases[name] = stable
            return stable
        for name in names:
            mapped = self.aliases.get(name) or (name if name in self.calls else None)
            if mapped:
                self.index_to_id[index] = mapped
                for extra in names:
                    self.aliases[extra] = mapped
                return mapped
        stable = names[0] if names else f"turn-{seq}-{offset}"
        self.index_to_id[index] = stable
        for name in names:
            self.aliases[name] = stable
        return stable


@dataclass(slots=True)
class TurnModeState:
    mode: TurnMode | None = None
    late_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prefix_buffer: str = ""
    events: list[ProviderTurnEvent] = field(default_factory=list)
    executed: bool = False
    cancelled: bool = False
    assembler: ToolCallAssembler = field(default_factory=ToolCallAssembler)


def _is_whitespace(text: str) -> bool:
    return not str(text or "").strip()


def _neutral_prefix(text: str) -> bool:
    raw = str(text or "")
    stripped = raw.lstrip()
    if not stripped:
        return True
    if len(stripped) > MAX_NEUTRAL_PREFIX:
        return False
    if EMO_COMPLETE.match(stripped):
        remainder = EMO_COMPLETE.sub("", stripped, count=1)
        return _is_whitespace(remainder)
    if EMO_PREFIX.match(stripped):
        return True
    return False


def merge_tool_calls(
    existing: dict[str, dict[str, Any]],
    incoming: list[dict[str, Any]] | None,
    *,
    seq: int,
    assembler: ToolCallAssembler | None = None,
) -> dict[str, dict[str, Any]]:
    box = assembler or ToolCallAssembler()
    if assembler is None:
        box.calls = existing
        for key in existing:
            box.aliases[str(key)] = str(key)
    box.ingest(incoming, seq=seq)
    if existing is not box.calls:
        existing.clear()
        existing.update(box.calls)
    return box.calls


def observe_chunk(state: TurnModeState, chunk: dict[str, Any], seq: int) -> ProviderTurnEvent:
    content = chunk.get("content") or ""
    if content is None:
        content = ""
    content = str(content)
    reasoning = str(chunk.get("reasoning") or "")
    metadata = bool(chunk.get("metadata"))
    tool_calls = list(chunk.get("tool_calls") or [])
    event = ProviderTurnEvent(
        seq=seq,
        content=content,
        tool_calls=tool_calls,
        reasoning=reasoning,
        metadata=metadata,
        raw_boundary=str(chunk.get("raw_boundary") or seq),
    )
    state.events.append(event)

    has_tools = bool(tool_calls)
    semantic_text = content
    if state.mode is None:
        if has_tools:
            state.mode = "tool_mode"
            return event
        if (metadata or reasoning) and _is_whitespace(content):
            return event
        combined = state.prefix_buffer + content
        if _neutral_prefix(combined):
            state.prefix_buffer = combined
            return event
        state.mode = "answer_mode"
        return event
    if state.mode == "answer_mode" and has_tools:
        state.late_tool_calls.extend(tool_calls)
    return event


def coalesce_gemini_parts(parts: list[dict[str, Any]], *, stream_seq: int) -> dict[str, Any]:
    """One upstream Gemini event becomes one downstream chunk."""
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    for offset, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if part.get("text"):
            texts.append(str(part["text"]))
        call = part.get("functionCall")
        if isinstance(call, dict):
            import json

            calls.append(
                {
                    "index": offset,
                    "id": f"gemini-{stream_seq}-{offset}-{call.get('name') or 'fn'}",
                    "type": "function",
                    "function": {
                        "name": call.get("name"),
                        "arguments": json.dumps(call.get("args", {}), ensure_ascii=False),
                    },
                }
            )
    return {
        "content": "".join(texts) if texts else None,
        "tool_calls": calls or None,
        "raw_boundary": f"gemini-event-{stream_seq}",
    }
