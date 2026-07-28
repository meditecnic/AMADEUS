"""Pure schema validation for the local TTS diagnostic side channel.

This module deliberately has no dependency on the Turn, segment, TTS, or
playback implementations. Storage and observation hooks can be added later
without making validation part of the user-visible audio path.
"""

from __future__ import annotations

from collections.abc import Mapping
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable
import unicodedata
from uuid import uuid4


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
DIAGNOSTIC_STORAGE_VERSION = 2

STATUS_VALUES = frozenset(
    {
        "started",
        "succeeded",
        "failed",
        "cancelled",
        "late_rejected",
        "skipped",
        "overflow",
    }
)
REASON_CODES = frozenset(
    {
        "action_only",
        "too_short",
        "invalid_language",
        "text_too_long",
        "user_disabled",
        "voice_stopped",
        "user_turn_cancel",
        "superseded_generation",
        "turn_terminal",
    }
)
LATE_REJECTED_REASONS = frozenset(
    {
        "voice_stopped",
        "user_turn_cancel",
        "superseded_generation",
        "turn_terminal",
    }
)
SKIP_REASONS = frozenset({
    "action_only",
    "too_short",
    "invalid_language",
    "text_too_long",
    "user_disabled",
})
STAGES = frozenset({"generation", "tts", "delivery", "decode", "playback"})
SOURCES = frozenset({"server", "client_reported"})
LANGUAGE_VALUES = frozenset({"ja", "zh", "mixed", "symbol_only", "unknown"})
V1_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "turn_id",
        "segment_id",
        "stage",
        "observed_at",
        "source",
        "status",
        "error_summary",
        "reason_code",
        "attempt_no",
        "server_seq",
    }
)
V2_OBSERVATION_FIELDS = frozenset(
    {"source_hash", "source_length", "language", "audio_bytes"}
)
ALLOWED_EVENT_FIELDS = V1_EVENT_FIELDS | V2_OBSERVATION_FIELDS
DIAGNOSTIC_EXPORT_EXCLUSIONS = frozenset(
    {"diagnostics.sqlite3", "diagnostic_events", "diagnostic_verbose"}
)
FAILURE_CATEGORIES = frozenset(
    {"open_failed", "write_failed", "readonly", "disk_full"}
)

_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_SOURCE_HASH = re.compile(r"^[0-9a-f]{16}$")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LETTER_OR_NUMBER = re.compile(r"[^\W_]", re.UNICODE)
_CONSERVATIVE_ZH_MARKERS = frozenset("这们个为说吗语发后里还让从对与会没么过门间时样的")
KNOWN_AUDIO_ERROR_CODES = frozenset(
    {
        "empty_text",
        "action_only",
        "too_short",
        "user_disabled",
        "voice_stopped",
        "timeout",
        "rate_limited",
        "service_unavailable",
        "synthesis_failed",
    }
)
STOP_REASON_CODES = frozenset(
    {"voice_stopped", "user_turn_cancel", "superseded_generation"}
)


class DiagnosticSchemaError(ValueError):
    """Raised when an event violates a supported closed schema contract."""


def normalize_segment_source(text: str) -> str:
    """Normalize committed Segment source text without retaining it."""

    if not isinstance(text, str):
        raise TypeError("segment source must be text")
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def classify_source_language(text: str) -> str:
    """Return a conservative local script classification with no provider call."""

    normalized = normalize_segment_source(text)
    if not _LETTER_OR_NUMBER.search(normalized):
        return "symbol_only"
    has_kana = bool(_KANA.search(normalized))
    has_han = bool(_HAN.search(normalized))
    has_zh_marker = any(character in _CONSERVATIVE_ZH_MARKERS for character in normalized)
    if has_kana and has_zh_marker:
        return "mixed"
    if has_kana:
        return "ja"
    if has_han:
        return "zh"
    return "unknown"


def describe_segment_source(text: str) -> dict[str, str | int]:
    """Build the non-reversible schema-v2 source observation fields."""

    normalized = normalize_segment_source(text)
    return {
        "source_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16],
        "source_length": len(normalized),
        "language": classify_source_language(normalized),
    }


def build_tts_terminal_event(
    *,
    turn_id: str,
    segment_id: int | str,
    source_text: str,
    audio: bytes | None,
    audio_error: str | None,
    server_seq: int,
) -> tuple[dict[str, Any], bool]:
    """Build one redacted aggregate TTS event and report generic fallback use."""

    source = describe_segment_source(source_text)
    fallback = False
    if audio is not None:
        status, reason, error_summary, audio_bytes = "succeeded", None, None, len(audio)
        observations = source
    elif audio_error in SKIP_REASONS:
        status, reason, error_summary, audio_bytes = "skipped", audio_error, None, None
        observations = source
    elif audio_error in STOP_REASON_CODES:
        status, reason, error_summary, audio_bytes = "cancelled", audio_error, None, None
        observations = {}
    else:
        fallback = audio_error not in KNOWN_AUDIO_ERROR_CODES
        status, reason, audio_bytes = "failed", None, None
        error_summary = audio_error if not fallback and audio_error else "audio_failure_generic"
        observations = source
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "turn_id": str(turn_id),
        "segment_id": str(segment_id),
        "stage": "tts",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "server",
        "status": status,
        "error_summary": error_summary,
        "reason_code": reason,
        "attempt_no": 1,
        "server_seq": server_seq,
        "source_hash": observations.get("source_hash"),
        "source_length": observations.get("source_length"),
        "language": observations.get("language"),
        "audio_bytes": audio_bytes,
    }
    validate_event(event)
    return event, fallback


def build_generation_event(
    *,
    turn_id: str,
    status: str,
    server_seq: int,
    reason_code: str | None = None,
    error_summary: str | None = None,
) -> dict[str, Any]:
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "turn_id": str(turn_id),
        "segment_id": None,
        "stage": "generation",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "server",
        "status": status,
        "error_summary": error_summary,
        "reason_code": reason_code,
        "attempt_no": 1,
        "server_seq": server_seq,
        "source_hash": None,
        "source_length": None,
        "language": None,
        "audio_bytes": None,
    }
    validate_event(event)
    return event


def build_generation_segment_event(
    *,
    turn_id: str,
    segment_id: int | str,
    source_text: str,
    server_seq: int,
) -> dict[str, Any]:
    event = build_generation_event(
        turn_id=turn_id,
        status="succeeded",
        server_seq=server_seq,
    )
    event.update(
        segment_id=str(segment_id),
        **describe_segment_source(source_text),
    )
    validate_event(event)
    return event


def build_delivery_event(
    *,
    turn_id: str,
    segment_id: int | str | None,
    status: str,
    server_seq: int,
    error_summary: str | None = None,
) -> dict[str, Any]:
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "turn_id": str(turn_id),
        "segment_id": None if segment_id is None else str(segment_id),
        "stage": "delivery",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "server",
        "status": status,
        "error_summary": error_summary,
        "reason_code": None,
        "attempt_no": 1,
        "server_seq": server_seq,
        "source_hash": None,
        "source_length": None,
        "language": None,
        "audio_bytes": None,
    }
    validate_event(event)
    return event


def _require_string(event: Mapping[str, Any], name: str, *, nullable: bool = False):
    value = event[name]
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value:
        raise DiagnosticSchemaError(f"{name} must be a non-empty string")


def validate_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a shallow copy of one versioned event."""

    if not isinstance(event, Mapping):
        raise DiagnosticSchemaError("event must be a mapping")
    schema_version = event.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise DiagnosticSchemaError("unsupported diagnostic schema version")
    expected_fields = V1_EVENT_FIELDS if schema_version == 1 else ALLOWED_EVENT_FIELDS
    unknown = set(event) - expected_fields
    missing = expected_fields - set(event)
    if unknown:
        raise DiagnosticSchemaError("event contains unapproved fields")
    if missing:
        raise DiagnosticSchemaError("event is missing required fields")

    _require_string(event, "event_id")
    _require_string(event, "turn_id")
    _require_string(event, "segment_id", nullable=True)
    _require_string(event, "stage", nullable=True)
    _require_string(event, "source")

    try:
        observed_at = datetime.fromisoformat(event["observed_at"])
    except (TypeError, ValueError) as exc:
        raise DiagnosticSchemaError("observed_at must be an ISO timestamp") from exc
    if observed_at.tzinfo is None:
        raise DiagnosticSchemaError("observed_at must include a timezone")

    status = event["status"]
    if status not in STATUS_VALUES:
        raise DiagnosticSchemaError("unsupported diagnostic status")
    source = event["source"]
    if source not in SOURCES:
        raise DiagnosticSchemaError("unsupported diagnostic source")
    stage = event["stage"]
    if status == "overflow":
        if event["segment_id"] is not None or stage is not None:
            raise DiagnosticSchemaError("overflow must be a turn-level event")
    elif stage not in STAGES:
        raise DiagnosticSchemaError("stage is required for a phase event")

    reason = event["reason_code"]
    if status in {"cancelled", "skipped", "late_rejected"}:
        if reason not in REASON_CODES:
            raise DiagnosticSchemaError("terminal status requires a reason code")
        if status == "late_rejected" and reason not in LATE_REJECTED_REASONS:
            raise DiagnosticSchemaError("invalid late_rejected reason")
        if status == "skipped" and reason not in SKIP_REASONS:
            raise DiagnosticSchemaError("invalid skipped reason")
    elif reason is not None:
        raise DiagnosticSchemaError("reason code is not valid for this status")

    error_summary = event["error_summary"]
    if error_summary is not None and (
        not isinstance(error_summary, str)
        or not _SAFE_ERROR_CODE.fullmatch(error_summary)
    ):
        raise DiagnosticSchemaError("error_summary must be a stable redacted code")

    attempt_no = event["attempt_no"]
    if not isinstance(attempt_no, int) or isinstance(attempt_no, bool) or attempt_no < 1:
        raise DiagnosticSchemaError("attempt_no must be a positive integer")

    server_seq = event["server_seq"]
    if source == "server":
        if not isinstance(server_seq, int) or isinstance(server_seq, bool) or server_seq < 1:
            raise DiagnosticSchemaError("server events require server_seq")
    elif server_seq is not None:
        raise DiagnosticSchemaError("client_reported events cannot claim server_seq")

    if schema_version == 2:
        source_hash = event["source_hash"]
        source_length = event["source_length"]
        language = event["language"]
        audio_bytes = event["audio_bytes"]
        source_values = (source_hash, source_length, language)
        if any(value is not None for value in source_values):
            if not all(value is not None for value in source_values):
                raise DiagnosticSchemaError("source observation fields are atomic")
            if not isinstance(source_hash, str) or not _SAFE_SOURCE_HASH.fullmatch(source_hash):
                raise DiagnosticSchemaError("source_hash must be 16 lowercase SHA-256 hex")
            if (
                not isinstance(source_length, int)
                or isinstance(source_length, bool)
                or source_length < 0
            ):
                raise DiagnosticSchemaError("source_length must be a non-negative codepoint count")
            if language not in LANGUAGE_VALUES:
                raise DiagnosticSchemaError("unsupported source language classification")
        if audio_bytes is not None and (
            not isinstance(audio_bytes, int)
            or isinstance(audio_bytes, bool)
            or audio_bytes < 0
        ):
            raise DiagnosticSchemaError("audio_bytes must be a non-negative integer")
        observations_present = any(
            event[field] is not None for field in V2_OBSERVATION_FIELDS
        )
        if stage not in {"tts", "generation"} and observations_present:
            raise DiagnosticSchemaError("observation fields are not valid for this stage")
        if stage == "generation":
            if event["segment_id"] is None and observations_present:
                raise DiagnosticSchemaError("turn generation cannot carry source observations")
            if event["segment_id"] is not None and status == "succeeded":
                if any(value is None for value in source_values) or audio_bytes is not None:
                    raise DiagnosticSchemaError(
                        "committed generation segment requires source observations only"
                    )
            elif observations_present:
                raise DiagnosticSchemaError("this generation status cannot carry observations")
        if stage == "tts":
            if attempt_no != 1:
                raise DiagnosticSchemaError("schema-v2 tts is one aggregate segment stage")
            if status == "succeeded" and audio_bytes is None:
                raise DiagnosticSchemaError("successful tts requires audio_bytes")
            if status in {"failed", "skipped"} and any(
                value is None for value in source_values
            ):
                raise DiagnosticSchemaError("failed or skipped tts requires source observations")
            if status in {"failed", "skipped"} and audio_bytes is not None:
                raise DiagnosticSchemaError("failed or skipped tts cannot claim audio bytes")
            if status not in {"succeeded", "failed", "skipped"} and observations_present:
                raise DiagnosticSchemaError("this tts status cannot carry observations")

    return dict(event)


class DiagnosticEventLedger:
    """Bounded in-memory fold used by schema tests and summary readers.

    It is intentionally not a persistence layer and has no connection to the
    live Turn pipeline. The eventual SQLite writer can use the same contract
    while retaining append-only rows.
    """

    def __init__(self, *, max_events: int = 200):
        if max_events < 2:
            raise ValueError("max_events must reserve an overflow slot")
        self.max_events = max_events
        self._events: list[dict[str, Any]] = []
        self._sealed = False

    def append(self, event: Mapping[str, Any]) -> None:
        if self._sealed:
            raise DiagnosticSchemaError("turn diagnostic ledger is sealed")
        normalized = validate_event(event)
        if normalized["status"] == "overflow":
            if len(self._events) != self.max_events - 1:
                raise DiagnosticSchemaError("overflow must occupy the reserved final slot")
            self._events.append(normalized)
            self._sealed = True
            return
        if len(self._events) >= self.max_events - 1:
            raise DiagnosticSchemaError("diagnostic event cap reached")
        self._events.append(normalized)

    def incomplete_attempts(self) -> set[tuple[str, str | None, str, int]]:
        started: set[tuple[str, str | None, str, int]] = set()
        terminal: set[tuple[str, str | None, str, int]] = set()
        for event in self._events:
            if event["stage"] is None:
                continue
            key = (
                event["turn_id"],
                event["segment_id"],
                event["stage"],
                event["attempt_no"],
            )
            if event["status"] == "started":
                started.add(key)
            elif event["status"] in {"succeeded", "failed", "cancelled", "skipped"}:
                terminal.add(key)
        return started - terminal

    def segment_summary(self, turn_id: str, segment_id: str) -> dict[str, str]:
        summary: dict[str, str] = {}
        for event in self._events:
            if event["turn_id"] != turn_id or event["segment_id"] != segment_id:
                continue
            if event["stage"] is None or event["status"] in {"started", "late_rejected"}:
                continue
            summary[event["stage"]] = event["status"]
        return summary


class DiagnosticStore:
    """Isolated best-effort SQLite store for diagnostic evidence.

    The store is inert until explicitly constructed. Nothing in the production
    Turn pipeline imports or instantiates it yet.
    """

    def __init__(
        self,
        data_root: Path | str,
        *,
        control_path: Path | str,
        clock: Callable[[], datetime] | None = None,
    ):
        self.data_root = Path(data_root)
        self.path = self.data_root / "diagnostics.sqlite3"
        self.control_path = Path(control_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._consecutive_failures = 0
        self._circuit_open = False
        self._sealed_turns: set[str] = set()
        self._turn_counts: dict[str, int] = {}
        self._write_lock = threading.Lock()
        self.write_attempts = 0

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=0.01)
        connection.row_factory = sqlite3.Row
        return connection

    def _control_connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.control_path, timeout=0.05)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _migrate_storage(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(diagnostic_events)")
        }
        additions = (
            ("source_hash", "TEXT"),
            ("source_length", "INTEGER"),
            ("language", "TEXT"),
            ("audio_bytes", "INTEGER"),
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            for name, sql_type in additions:
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE diagnostic_events ADD COLUMN {name} {sql_type}"
                    )
            connection.execute(f"PRAGMA user_version={DIAGNOSTIC_STORAGE_VERSION}")
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

    def initialize(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        with self._control_connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    cumulative_failures INTEGER NOT NULL DEFAULT 0,
                    last_failure_at TEXT,
                    last_failure_category TEXT,
                    degraded INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO diagnostic_state(singleton) VALUES (1);
                """
            )
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_events (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    turn_id TEXT NOT NULL,
                    segment_id TEXT,
                    stage TEXT,
                    observed_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_summary TEXT,
                    reason_code TEXT,
                    attempt_no INTEGER NOT NULL,
                    server_seq INTEGER,
                    source_hash TEXT,
                    source_length INTEGER,
                    language TEXT,
                    audio_bytes INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_turn
                    ON diagnostic_events(turn_id);
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_segment
                    ON diagnostic_events(segment_id);
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_observed
                    ON diagnostic_events(observed_at);
                CREATE TABLE IF NOT EXISTS verbose_sessions (
                    session_id TEXT PRIMARY KEY,
                    enabled_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    active INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS diagnostic_verbose (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    complete_text TEXT NOT NULL
                );
                """
            )
            self._migrate_storage(connection)

    def journal_mode(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def _persistent_state(self) -> dict[str, Any]:
        with self._control_connect() as connection:
            row = connection.execute(
                "SELECT cumulative_failures, last_failure_at, "
                "last_failure_category, degraded FROM diagnostic_state WHERE singleton=1"
            ).fetchone()
        return dict(row)

    def health(self) -> dict[str, Any]:
        state = self._persistent_state()
        return {
            "status": "degraded" if state["degraded"] or self._circuit_open else "ready",
            "cumulative_failures": state["cumulative_failures"],
            "consecutive_failures": self._consecutive_failures,
            "last_failure_at": state["last_failure_at"],
            "last_failure_category": state["last_failure_category"],
            "circuit_open": self._circuit_open,
        }

    def record_failure(self, category: str, *, force_degraded: bool = False) -> None:
        if category not in FAILURE_CATEGORIES:
            raise ValueError("unsupported diagnostic failure category")
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self._circuit_open = True
        degraded = force_degraded or self._circuit_open
        try:
            with self._control_connect() as connection:
                connection.execute(
                    """
                    UPDATE diagnostic_state
                    SET cumulative_failures = cumulative_failures + 1,
                        last_failure_at = ?, last_failure_category = ?,
                        degraded = CASE WHEN ? THEN 1 ELSE degraded END
                    WHERE singleton = 1
                    """,
                    (self._now().isoformat(), category, int(degraded)),
                )
        except sqlite3.Error:
            pass

    @staticmethod
    def _failure_category(exc: sqlite3.Error, *, opening: bool = False) -> str:
        message = str(exc).lower()
        if "readonly" in message or "read-only" in message:
            return "readonly"
        if "full" in message:
            return "disk_full"
        return "open_failed" if opening else "write_failed"

    @staticmethod
    def _insert(connection: sqlite3.Connection, event: Mapping[str, Any]) -> None:
        connection.execute(
            """
            INSERT INTO diagnostic_events(
                schema_version, event_id, turn_id, segment_id, stage,
                observed_at, source, status, error_summary, reason_code,
                attempt_no, server_seq, source_hash, source_length, language,
                audio_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(event.get(field) for field in (
                "schema_version", "event_id", "turn_id", "segment_id", "stage",
                "observed_at", "source", "status", "error_summary", "reason_code",
                "attempt_no", "server_seq", "source_hash", "source_length",
                "language", "audio_bytes",
            )),
        )

    def startup_probe(self) -> bool:
        probe = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "__diagnostic_probe_event__",
            "turn_id": "__diagnostic_probe__",
            "segment_id": None,
            "stage": "delivery",
            "observed_at": self._now().isoformat(),
            "source": "server",
            "status": "succeeded",
            "error_summary": None,
            "reason_code": None,
            "attempt_no": 1,
            "server_seq": 1,
            "source_hash": None,
            "source_length": None,
            "language": None,
            "audio_bytes": None,
        }
        try:
            validate_event(probe)
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._insert(connection, probe)
                connection.execute(
                    "DELETE FROM diagnostic_events WHERE event_id=?", (probe["event_id"],)
                )
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            self.record_failure(self._failure_category(exc, opening=True), force_degraded=True)
            return False
        self._consecutive_failures = 0
        self._circuit_open = False
        with self._control_connect() as connection:
            connection.execute(
                "UPDATE diagnostic_state SET degraded=0 WHERE singleton=1"
            )
        return True

    def _load_turn_state(self, turn_id: str) -> int:
        if turn_id in self._turn_counts:
            return self._turn_counts[turn_id]
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count,
                       MAX(CASE WHEN status='overflow' THEN 1 ELSE 0 END) AS sealed
                FROM diagnostic_events WHERE turn_id=?
                """,
                (turn_id,),
            ).fetchone()
        count = int(row["event_count"] or 0)
        self._turn_counts[turn_id] = count
        if bool(row["sealed"]) or count >= 200:
            self._sealed_turns.add(turn_id)
        return count

    def append_event(self, event: Mapping[str, Any]) -> bool:
        turn_id = str(event.get("turn_id", ""))
        if self._circuit_open:
            return False
        with self._write_lock:
            if self._circuit_open or turn_id in self._sealed_turns:
                return False
            self.write_attempts += 1
            try:
                normalized = validate_event(event)
                count = self._load_turn_state(turn_id)
                if turn_id in self._sealed_turns:
                    return False
                if count >= 199:
                    overflow = dict(normalized)
                    overflow.update(
                        event_id=f"{turn_id}:overflow",
                        segment_id=None,
                        stage=None,
                        status="overflow",
                        error_summary=None,
                        reason_code=None,
                        server_seq=200,
                    )
                    if normalized["schema_version"] == 2:
                        overflow.update(
                            source_hash=None,
                            source_length=None,
                            language=None,
                            audio_bytes=None,
                        )
                    validate_event(overflow)
                    with self._connect() as connection:
                        self._insert(connection, overflow)
                    self._turn_counts[turn_id] = 200
                    self._sealed_turns.add(turn_id)
                    self._consecutive_failures = 0
                    return False
                with self._connect() as connection:
                    self._insert(connection, normalized)
                self._turn_counts[turn_id] = count + 1
                self._consecutive_failures = 0
                return True
            except (sqlite3.Error, DiagnosticSchemaError) as exc:
                category = (
                    self._failure_category(exc)
                    if isinstance(exc, sqlite3.Error)
                    else "write_failed"
                )
                self.record_failure(category)
                return False

    def read_events(self, turn_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT schema_version, event_id, turn_id, segment_id, stage,
                       observed_at, source, status, error_summary, reason_code,
                       attempt_no, server_seq, source_hash, source_length,
                       language, audio_bytes
                FROM diagnostic_events WHERE turn_id=?
                ORDER BY
                    CASE WHEN source='server' THEN 0 ELSE 1 END,
                    CASE WHEN source='server' THEN server_seq ELSE row_id END,
                    row_id
                """,
                (turn_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def sweep(self) -> int:
        now = self._now()
        cutoff = (now - timedelta(days=7)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM diagnostic_events WHERE observed_at < ?", (cutoff,)
            )
            connection.execute(
                """
                DELETE FROM diagnostic_verbose
                WHERE captured_at < ? OR session_id IN (
                    SELECT session_id FROM verbose_sessions
                    WHERE active=0 OR expires_at <= ?
                )
                """,
                (cutoff, now.isoformat()),
            )
            connection.execute(
                "UPDATE verbose_sessions SET active=0 WHERE expires_at <= ?",
                (now.isoformat(),),
            )
        return cursor.rowcount

    def enable_verbose(self, session_id: str) -> None:
        now = self._now()
        expires = now + timedelta(hours=24)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO verbose_sessions(session_id, enabled_at, expires_at, active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(session_id) DO UPDATE SET
                    enabled_at=excluded.enabled_at,
                    expires_at=excluded.expires_at,
                    active=1
                """,
                (session_id, now.isoformat(), expires.isoformat()),
            )

    def capture_verbose(self, session_id: str, complete_text: str) -> bool:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at, active FROM verbose_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None or not row["active"] or datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    "DELETE FROM diagnostic_verbose WHERE session_id=?", (session_id,)
                )
                connection.execute(
                    "UPDATE verbose_sessions SET active=0 WHERE session_id=?", (session_id,)
                )
                return False
            connection.execute(
                "INSERT INTO diagnostic_verbose(session_id, captured_at, complete_text) "
                "VALUES (?, ?, ?)",
                (session_id, now.isoformat(), complete_text),
            )
        return True

    def read_verbose(self, session_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT complete_text FROM diagnostic_verbose WHERE session_id=? "
                "ORDER BY row_id",
                (session_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def has_foreign_keys(self, table: str) -> bool:
        if table not in {"diagnostic_events", "diagnostic_verbose", "verbose_sessions"}:
            raise ValueError("unsupported diagnostics table")
        with self._connect() as connection:
            return bool(connection.execute(f"PRAGMA foreign_key_list({table})").fetchall())

    def clear_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM diagnostic_events")
            connection.execute("DELETE FROM diagnostic_verbose")
            connection.execute("DELETE FROM verbose_sessions")
        with self._control_connect() as connection:
            connection.execute(
                """
                UPDATE diagnostic_state
                SET cumulative_failures=0, last_failure_at=NULL,
                    last_failure_category=NULL, degraded=0
                WHERE singleton=1
                """
            )
        self._consecutive_failures = 0
        self._circuit_open = False
        self._sealed_turns.clear()
        self._turn_counts.clear()


def _default_store_factory() -> DiagnosticStore:
    from app.db import get_data_root

    root = get_data_root()
    return DiagnosticStore(root, control_path=root / "control.sqlite3")


class DiagnosticRuntime:
    """Non-blocking lifecycle facade with a strictly redacted health surface."""

    def __init__(self, store_factory: Callable[[], Any] | None = None):
        self._store_factory = store_factory or _default_store_factory
        self._store: DiagnosticStore | None = None
        self._fallback_state = "not_ready"
        self._fallback_failures = 0
        self._fallback_last_failure_time: str | None = None
        self._fallback_last_error_category: str | None = None
        self.observing = False
        self.mapping_fallbacks = 0
        self._turn_observers: dict[tuple[str, int], DiagnosticTurnObserver] = {}

    def _degrade(self, category: str) -> None:
        self._fallback_state = "degraded"
        self._fallback_failures += 1
        self._fallback_last_failure_time = datetime.now(timezone.utc).isoformat()
        self._fallback_last_error_category = (
            category if category in FAILURE_CATEGORIES else "open_failed"
        )

    def start(self) -> bool:
        self._store = None
        self._fallback_state = "not_ready"
        self.observing = os.getenv("AMADEUS_DIAGNOSTICS_OBSERVE", "0").lower() in {
            "1", "true", "yes", "on"
        }
        try:
            store = self._store_factory()
            self._store = store
            try:
                store.initialize()
            except Exception:
                try:
                    store.record_failure("open_failed", force_degraded=True)
                    self._fallback_state = "degraded"
                    return False
                except Exception:
                    self._store = None
                    self._degrade("open_failed")
                    return False
            ready = bool(store.startup_probe())
            self._fallback_state = "ready" if ready else "degraded"
            return ready
        except Exception:
            self._degrade("open_failed")
            return False

    def sweep_best_effort(self) -> None:
        if self._store is None:
            return
        try:
            self._store.sweep()
        except Exception:
            try:
                self._store.record_failure("write_failed")
            except Exception:
                self._degrade("write_failed")

    def set_observing(self, enabled: bool) -> None:
        self.observing = bool(enabled)

    def append_event(self, event: Mapping[str, Any]) -> bool:
        if not self.observing or self._store is None:
            return False
        try:
            return bool(self._store.append_event(event))
        except Exception:
            self.record_observer_failure()
            return False

    def record_observer_failure(self) -> None:
        try:
            if self._store is not None:
                self._store.record_failure("write_failed")
            else:
                self._degrade("write_failed")
        except Exception:
            self._degrade("write_failed")

    def record_mapping_fallback(self) -> None:
        self.mapping_fallbacks += 1

    def create_tts_observer(self) -> "DiagnosticTTSObserver | None":
        if not self.observing:
            return None
        return DiagnosticTTSObserver(self)

    def create_turn_observer(self, identity=None) -> "DiagnosticTurnObserver | None":
        if not self.observing:
            return None
        observer = DiagnosticTurnObserver(self)
        if identity is not None:
            key = (str(identity.turn_id), int(identity.generation))
            self._turn_observers[key] = observer
            while len(self._turn_observers) > 256:
                self._turn_observers.pop(next(iter(self._turn_observers)))
        return observer

    def find_turn_observer(self, turn_id: str, generation: int):
        return self._turn_observers.get((str(turn_id), int(generation)))

    def release_turn_observer(self, turn_id: str, generation: int) -> None:
        self._turn_observers.pop((str(turn_id), int(generation)), None)

    def public_health(self) -> dict[str, Any]:
        if self._store is not None:
            try:
                health = self._store.health()
                return {
                    "state": health["status"],
                    "observing": self.observing,
                    "mapping_fallbacks": self.mapping_fallbacks,
                    "cumulative_failures": health["cumulative_failures"],
                    "last_failure_time": health["last_failure_at"],
                    "last_error_category": health["last_failure_category"],
                }
            except Exception:
                self._degrade("open_failed")
        return {
            "state": self._fallback_state,
            "observing": self.observing,
            "mapping_fallbacks": self.mapping_fallbacks,
            "cumulative_failures": self._fallback_failures,
            "last_failure_time": self._fallback_last_failure_time,
            "last_error_category": self._fallback_last_error_category,
        }


class DiagnosticTTSObserver:
    """Schedule one TTS terminal event without blocking SegmentPipeline."""

    def __init__(self, runtime: DiagnosticRuntime):
        self.runtime = runtime
        self.server_seq = 0
        self.fallback_count = 0

    def __call__(self, identity, segment, audio, audio_error) -> None:
        if not self.runtime.observing:
            return
        self.server_seq += 1
        try:
            event, fallback = build_tts_terminal_event(
                turn_id=identity.turn_id,
                segment_id=segment.id,
                source_text=segment.text,
                audio=audio,
                audio_error=audio_error,
                server_seq=self.server_seq,
            )
            if fallback:
                self.fallback_count += 1
                self.runtime.record_mapping_fallback()
            self._schedule(event)
        except Exception:
            self.runtime.record_observer_failure()

    def _schedule(self, event: Mapping[str, Any]) -> None:
        task = asyncio.create_task(asyncio.to_thread(self.runtime.append_event, event))
        task.add_done_callback(self._consume_task)

    def _consume_task(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except BaseException:
            self.runtime.record_observer_failure()


class DiagnosticTurnObserver(DiagnosticTTSObserver):
    """Combined G-01/T-01 observer for one immutable Turn snapshot."""

    def __init__(self, runtime: DiagnosticRuntime):
        super().__init__(runtime)
        self._started_turns: set[tuple[str, int]] = set()
        self._terminal_turns: set[tuple[str, int]] = set()

    def turn_started(self, identity) -> None:
        if not self.runtime.observing:
            return
        key = (str(identity.turn_id), int(identity.generation))
        if key in self._started_turns:
            return
        self._started_turns.add(key)
        self.server_seq += 1
        try:
            self._schedule(
                build_generation_event(
                    turn_id=identity.turn_id,
                    status="started",
                    server_seq=self.server_seq,
                )
            )
        except Exception:
            self.runtime.record_observer_failure()

    def turn_terminal(
        self,
        identity,
        status: str,
        reason_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        if not self.runtime.observing:
            return
        key = (str(identity.turn_id), int(identity.generation))
        if key not in self._started_turns or key in self._terminal_turns:
            return
        if status == "cancelled" and reason_code not in STOP_REASON_CODES:
            return
        if status not in {"succeeded", "cancelled", "failed"}:
            return
        self._terminal_turns.add(key)
        self.server_seq += 1
        try:
            self._schedule(
                build_generation_event(
                    turn_id=identity.turn_id,
                    status=status,
                    reason_code=reason_code,
                    error_summary=error_summary,
                    server_seq=self.server_seq,
                )
            )
        except Exception:
            self.runtime.record_observer_failure()

    def segment_committed(self, identity, segment) -> None:
        if not self.runtime.observing:
            return
        self.server_seq += 1
        try:
            self._schedule(
                build_generation_segment_event(
                    turn_id=identity.turn_id,
                    segment_id=segment.id,
                    source_text=segment.text,
                    server_seq=self.server_seq,
                )
            )
        except Exception:
            self.runtime.record_observer_failure()

    def turn_error(self, identity, error_summary: str | None = None) -> None:
        summary = error_summary if error_summary in KNOWN_AUDIO_ERROR_CODES else "generation_failed"
        self.turn_terminal(identity, "failed", error_summary=summary)

    def delivery(
        self,
        payload: Mapping[str, Any],
        status: str,
        error_summary: str | None = None,
    ) -> None:
        if not self.runtime.observing:
            return
        self.server_seq += 1
        try:
            self._schedule(
                build_delivery_event(
                    turn_id=str(payload["turn_id"]),
                    segment_id=payload.get("segment_id"),
                    status=status,
                    error_summary=error_summary,
                    server_seq=self.server_seq,
                )
            )
        except Exception:
            self.runtime.record_observer_failure()


diagnostic_runtime = DiagnosticRuntime()
