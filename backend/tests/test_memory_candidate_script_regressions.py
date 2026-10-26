"""Regression coverage shared by the isolated runtime and real-run scripts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import memory_candidate_real_run as b3  # noqa: E402
import memory_candidate_runtime as b2  # noqa: E402

PERSONA = "PERSONA BIBLE v1 (canonical runtime persona)\npersona"


def _nested_prompt(own_core: str = "", own_state: str = "") -> str:
    old = (
        "IDENTITY AND SECURITY\nold identity\n\n"
        f"{PERSONA}\n\nCORE FACTS\nrule\n- OLD_FACT\n\n"
        "CURRENT STATE\nold state\n\nMEMORY CANDIDATES\nrules\n"
        'MEMORY_CANDIDATE_DATA\n{"records":{"m1":"OLD_SUPPLEMENT"}}'
    )
    return "\n\n".join(
        part for part in (f"IDENTITY AND SECURITY\n{old}", PERSONA, own_core, own_state)
        if part
    )


def _messages(prompt: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": prompt}]


def _capture_file(tmp_path: Path, prompt: str) -> Path:
    path = tmp_path / "chat.jsonl"
    path.write_text(json.dumps({"messages": _messages(prompt)}) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("state", ["", "CURRENT STATE\nnow"])
def test_own_prompt_omitted_sections_never_reuse_identity(state, tmp_path):
    prompt = _nested_prompt(own_state=state)
    messages = _messages(prompt)
    assert b2.resident_core_fact_lines(prompt) == []
    assert b3._resident_core_fact_lines(prompt) == []
    assert b2.has_core_facts_heading(prompt) is False
    assert b3._system_has_core_facts_heading(messages) is False
    assert b3._system_core_facts_heading_without_facts(messages) is False
    assert b2._supplement_records(prompt) == []
    assert b3._core_facts_from_messages(messages) == ""
    assert b2._stable_fact_block(_capture_file(tmp_path, prompt)) == ""


@pytest.mark.parametrize("own_core", ["CORE FACTS\nrule", "CORE FACTS\n(none)"])
def test_own_prompt_empty_heading_is_still_rejected(own_core, tmp_path, capsys):
    prompt = _nested_prompt(own_core, "CURRENT STATE\nnow")
    assert b2.resident_core_fact_lines(prompt) == []
    assert b3._resident_core_fact_lines(prompt) == []
    assert b3._system_has_core_facts_heading(_messages(prompt)) is True
    assert b3._system_core_facts_heading_without_facts(_messages(prompt)) is True
    with pytest.raises(SystemExit):
        b2._stable_fact_block(_capture_file(tmp_path, prompt))
    assert "v11_system_prompt_core_facts_without_facts" in capsys.readouterr().err


def test_own_prompt_sections_use_current_facts_and_supplement(tmp_path):
    prompt = _nested_prompt(
        "CORE FACTS\nrule\n- NEW_FACT",
        "CURRENT STATE\nnow\n\nMEMORY CANDIDATES\nrules\n"
        'MEMORY_CANDIDATE_DATA\n{"records":{"m1":"NEW_SUPPLEMENT"}}',
    )
    assert b2.resident_core_fact_lines(prompt) == ["NEW_FACT"]
    assert b3._resident_core_fact_lines(prompt) == ["NEW_FACT"]
    assert b2._supplement_records(prompt) == ["NEW_SUPPLEMENT"]
    assert b3._core_facts_from_messages(_messages(prompt)) == "NEW_FACT\nNEW_SUPPLEMENT"
    assert b2._stable_fact_block(_capture_file(tmp_path, prompt)) == "NEW_FACT\nNEW_SUPPLEMENT"


def test_own_prompt_legacy_none_inside_identity_is_ignored(tmp_path):
    prompt = _nested_prompt().replace("rule\n- OLD_FACT", "(none)")
    assert b3._system_has_core_facts_none(_messages(prompt)) is False
    assert b2._stable_fact_block(_capture_file(tmp_path, prompt)) == ""


def test_own_prompt_identity_without_outer_persona_has_no_sections(tmp_path):
    prompt = "IDENTITY AND SECURITY\nold\n\nCORE FACTS\n- OLD_FACT"
    assert b2.resident_core_fact_lines(prompt) == []
    assert b3._system_has_core_facts_heading(_messages(prompt)) is False
    assert b2._stable_fact_block(_capture_file(tmp_path, prompt)) == ""
