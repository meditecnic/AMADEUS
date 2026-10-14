"""Versioned schema contract tests for the isolated TTS diagnostic side channel.

These tests intentionally precede the implementation. They validate the
diagnostic contract without starting the application, TTS sidecar, or any
production observation hook.
"""

from datetime import datetime, timezone

import pytest

from app.services.diagnostics import (
    ALLOWED_EVENT_FIELDS,
    LANGUAGE_VALUES,
    LATE_REJECTED_REASONS,
    REASON_CODES,
    SCHEMA_VERSION,
    SKIP_REASONS,
    STATUS_VALUES,
    SUPPORTED_SCHEMA_VERSIONS,
    V1_EVENT_FIELDS,
    DiagnosticSchemaError,
    DiagnosticEventLedger,
    classify_source_language,
    describe_segment_source,
    build_tts_terminal_event,
    validate_event,
)


def _event(**overrides):
    event = {
        "schema_version": 1,
        "event_id": "evt-1",
        "turn_id": "turn-1",
        "segment_id": "segment-1",
        "stage": "tts",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "server",
        "status": "started",
        "error_summary": None,
        "reason_code": None,
        "attempt_no": 1,
        "server_seq": 1,
    }
    event.update(overrides)
    return event


def _event_v2(**overrides):
    event = _event(
        schema_version=2,
        source_hash=None,
        source_length=None,
        language=None,
        audio_bytes=None,
    )
    event.update(overrides)
    return event


def test_schema_versions_and_enums_are_closed():
    assert SCHEMA_VERSION == 2
    assert SUPPORTED_SCHEMA_VERSIONS == {1, 2}
    assert LANGUAGE_VALUES == {"ja", "zh", "mixed", "symbol_only", "unknown"}
    assert STATUS_VALUES == {
        "started",
        "succeeded",
        "failed",
        "cancelled",
        "late_rejected",
        "skipped",
        "overflow",
    }
    assert REASON_CODES == {
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
    assert LATE_REJECTED_REASONS == {
        "voice_stopped",
        "user_turn_cancel",
        "superseded_generation",
        "turn_terminal",
    }
    assert SKIP_REASONS == {
        "action_only",
        "too_short",
        "invalid_language",
        "text_too_long",
        "user_disabled",
    }


def test_event_allowlist_rejects_silent_field_expansion():
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(unapproved_field="must fail"))


def test_v1_and_v2_are_parsed_by_their_own_field_allowlists():
    assert validate_event(_event())["schema_version"] == 1
    assert validate_event(_event_v2())["schema_version"] == 2
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(source_hash="0" * 16))
    with pytest.raises(DiagnosticSchemaError):
        validate_event({key: value for key, value in _event_v2().items() if key != "audio_bytes"})


def test_client_reported_events_cannot_claim_server_sequence():
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(source="client_reported", server_seq=9))


def test_cancelled_and_skipped_require_their_reason_codes():
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(status="cancelled"))
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(status="skipped", reason_code="voice_stopped"))


def test_late_rejected_accepts_only_late_reason_subset():
    for reason in LATE_REJECTED_REASONS:
        validate_event(_event(status="late_rejected", reason_code=reason))

    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(status="late_rejected", reason_code="too_short"))


def test_overflow_is_only_a_terminal_turn_event():
    validate_event(
        _event(
            segment_id=None,
            stage=None,
            status="overflow",
            reason_code=None,
            server_seq=200,
        )
    )
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event(status="overflow", stage="tts"))


def test_error_summary_is_redacted_and_bounded():
    with pytest.raises(DiagnosticSchemaError):
        validate_event(
            _event(
                status="failed",
                error_summary="sqlite error at C:\\Users\\example\\secret.db",
            )
        )


def test_event_schema_contains_only_documented_fields():
    assert V1_EVENT_FIELDS == {
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
    assert ALLOWED_EVENT_FIELDS == V1_EVENT_FIELDS | {
        "source_hash",
        "source_length",
        "language",
        "audio_bytes",
    }


def test_segment_source_description_is_deterministic_and_redacted():
    composed = "ガ\u30FCル"
    decomposed = "カ\u3099\u30FCル"
    first = describe_segment_source(composed)
    second = describe_segment_source(decomposed)
    assert first == second
    assert first == {
        "source_hash": first["source_hash"],
        "source_length": 3,
        "language": "ja",
    }
    assert len(first["source_hash"]) == 16
    assert set(first["source_hash"]) <= set("0123456789abcdef")
    assert composed not in repr(first)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("これはテストです。", "ja"),
        ("这是测试。", "zh"),
        ("これは中文的测试です。", "mixed"),
        ("……！？", "symbol_only"),
        ("hello", "unknown"),
    ],
)
def test_language_classification_is_local_and_deterministic(text, expected):
    assert classify_source_language(text) == expected


def test_schema_v2_tts_expectation_matrix():
    source = describe_segment_source("これはテストです。")
    validate_event(
        _event_v2(
            status="succeeded",
            audio_bytes=2048,
            **source,
        )
    )
    validate_event(
        _event_v2(
            status="failed",
            error_summary="synthesis_failed",
            **source,
        )
    )
    validate_event(
        _event_v2(
            status="skipped",
            reason_code="too_short",
            **source,
        )
    )
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event_v2(status="succeeded"))
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event_v2(status="failed", error_summary="synthesis_failed"))
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event_v2(stage="delivery", audio_bytes=1))
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event_v2(status="succeeded", audio_bytes=1, attempt_no=2))
    with pytest.raises(DiagnosticSchemaError):
        validate_event(_event_v2(status="started", **source))


def test_schema_v2_generation_segment_requires_source_observations():
    source = describe_segment_source("これはテストです。")
    event = _event_v2(
        stage="generation",
        segment_id="3",
        status="succeeded",
        **source,
    )
    validate_event(event)
    with pytest.raises(DiagnosticSchemaError):
        validate_event(
            _event_v2(stage="generation", segment_id="3", status="succeeded")
        )
    with pytest.raises(DiagnosticSchemaError):
        validate_event(
            _event_v2(
                stage="generation",
                segment_id=None,
                status="started",
                **source,
            )
        )


@pytest.mark.parametrize(
    ("audio_error", "status", "reason", "error_summary"),
    [
        (None, "succeeded", None, None),
        ("action_only", "skipped", "action_only", None),
        ("too_short", "skipped", "too_short", None),
        ("user_disabled", "skipped", "user_disabled", None),
        ("voice_stopped", "cancelled", "voice_stopped", None),
        ("user_turn_cancel", "cancelled", "user_turn_cancel", None),
        ("superseded_generation", "cancelled", "superseded_generation", None),
        ("empty_text", "failed", None, "empty_text"),
        ("timeout", "failed", None, "timeout"),
        ("rate_limited", "failed", None, "rate_limited"),
        ("service_unavailable", "failed", None, "service_unavailable"),
        ("synthesis_failed", "failed", None, "synthesis_failed"),
        ("provider leaked exception text", "failed", None, "audio_failure_generic"),
    ],
)
def test_tts_terminal_mapping_is_complete_and_redacted(
    audio_error, status, reason, error_summary
):
    event, used_fallback = build_tts_terminal_event(
        turn_id="turn-1",
        segment_id=3,
        source_text="これはテストです。",
        audio=b"wav" if audio_error is None else None,
        audio_error=audio_error,
        server_seq=1,
    )
    assert event["status"] == status
    assert event["reason_code"] == reason
    assert event["error_summary"] == error_summary
    if status == "cancelled":
        assert event["source_hash"] is None
        assert event["source_length"] is None
        assert event["language"] is None
    else:
        assert event["source_hash"]
        assert event["source_length"] == len("これはテストです。")
    assert event["audio_bytes"] == (3 if audio_error is None else None)
    assert used_fallback is (audio_error == "provider leaked exception text")
    validate_event(event)


def test_generation_and_tts_use_identical_local_source_classification():
    source = describe_segment_source("これは中文的测试です。")
    generation = _event_v2(
        stage="generation",
        segment_id="3",
        status="succeeded",
        **source,
    )
    tts, _ = build_tts_terminal_event(
        turn_id="turn-1",
        segment_id=3,
        source_text="これは中文的测试です。",
        audio=b"wav",
        audio_error=None,
        server_seq=2,
    )
    assert generation["source_hash"] == tts["source_hash"]
    assert generation["source_length"] == tts["source_length"]
    assert generation["language"] == tts["language"]


def test_folded_summary_exposes_submitted_segment_without_tts_terminal():
    ledger = DiagnosticEventLedger()
    source = describe_segment_source("これはテストです。")
    ledger.append(
        _event_v2(
            stage="generation",
            segment_id="3",
            status="succeeded",
            **source,
        )
    )
    summary = ledger.segment_summary("turn-1", "3")
    assert summary == {"generation": "succeeded"}
    assert "tts" not in summary


def test_terminal_invariant_is_scoped_to_attempt_and_late_events_do_not_close_it():
    ledger = DiagnosticEventLedger()
    ledger.append(_event(status="started", attempt_no=1, server_seq=1))
    ledger.append(
        _event(
            status="failed",
            attempt_no=1,
            server_seq=2,
            error_summary="synthesis_failed",
        )
    )
    ledger.append(_event(status="started", attempt_no=2, server_seq=3))
    ledger.append(
        _event(
            status="late_rejected",
            reason_code="turn_terminal",
            attempt_no=2,
            server_seq=4,
        )
    )
    assert ledger.incomplete_attempts() == {
        ("turn-1", "segment-1", "tts", 2)
    }


def test_summary_folds_terminal_state_per_segment():
    ledger = DiagnosticEventLedger()
    ledger.append(_event(status="started", server_seq=1))
    ledger.append(_event(status="succeeded", server_seq=2))
    assert ledger.segment_summary("turn-1", "segment-1")["tts"] == "succeeded"


def test_overflow_uses_the_reserved_final_slot_and_seals_the_turn():
    ledger = DiagnosticEventLedger(max_events=200)
    for seq in range(1, 200):
        ledger.append(_event(status="started", event_id=f"evt-{seq}", server_seq=seq))
    ledger.append(
        _event(
            event_id="evt-overflow",
            segment_id=None,
            stage=None,
            status="overflow",
            server_seq=200,
        )
    )
    with pytest.raises(DiagnosticSchemaError):
        ledger.append(_event(event_id="after-overflow", server_seq=201))
