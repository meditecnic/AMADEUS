"""B5A Gate 1 offline entry. No real provider, no downloads, no production data."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
BACKEND = WORKTREE / "backend"
SCRATCH = WORKTREE / ".scratch" / "memory-recall-b5a"
E5_SNAPSHOT = (
    Path.home()
    / ".cache"
    / "huggingface"
    / "hub"
    / "models--intfloat--multilingual-e5-small"
    / "snapshots"
    / "614241f622f53c4eeff9890bdc4f31cfecc418b3"
)
PYTEST_TARGETS = [
    "tests/test_memory_recall_b5a_r1_product_path.py",
    "tests/test_memory_recall_b5a_r1.py",
    "tests/test_memory_recall_b5a.py",
    "tests/test_memory_v11_prompt.py",
    "tests/test_memory_v11_chat_entry.py",
    "tests/test_memory_v11_retrieval.py",
    "tests/test_provider_adapters.py",
    "tests/test_memory_v11_jobs.py",
    "tests/test_agent_search.py",
]
PRODUCT_PATH_MODULE = "tests/test_memory_recall_b5a_r1_product_path.py"
REQUIRED_CHECK_PREFIX = "tests/test_memory_recall_b5a_r1_product_path.py::"


def _run_e5_coverage() -> dict:
    """Real local E5 query encode + candidate pack. Coverage only, not semantics."""
    import asyncio
    import tempfile

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(BACKEND))

    async def _probe() -> dict:
        from sentence_transformers import SentenceTransformer

        from app.db import get_db, init_db, reset_initialization_cache
        from app.services.memory import EmbeddingAdapter, memory_service
        from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts
        from app.services.memory_v11.repository import create_stable_fact

        class LocalE5(EmbeddingAdapter):
            def __init__(self) -> None:
                super().__init__(model_name="intfloat/multilingual-e5-small", dimensions=384)

            def _load(self):
                if self._model is None:
                    self._model = SentenceTransformer(
                        str(E5_SNAPSHOT),
                        local_files_only=True,
                        trust_remote_code=False,
                    )
                return self._model

        tmp = tempfile.TemporaryDirectory(prefix="b5a-e5-")
        os.environ["AMADEUS_DATA_DIR"] = tmp.name
        os.environ.pop("AMADEUS_DB_PATH", None)
        reset_initialization_cache()
        await init_db()
        embedder = LocalE5()
        memory_service.embedder = embedder
        facts = [
            ("coffee", "我每天早上喝美式咖啡，不加糖。"),
            ("cilantro", "我讨厌香菜。"),
            ("harmonica", "口琴只是偶尔吹着玩，不是正经爱好。"),
            ("cat", "我养了一只叫小白的猫。"),
        ]
        session_id = "e5-ownerA"
        from app.services.memory_v11.indexing import insert_version_embedding, prepare_passage_embedding

        for key, text in facts:
            row = await create_stable_fact(
                session_id=session_id,
                worldline="steins_gate",
                identity_mode="self",
                display_text=text,
                semantic_json={"subject": "user", "predicate": key, "object": key},
            )
            prepared = await prepare_passage_embedding(text, embedder=embedder)
            db = await get_db("steins_gate", "memory")
            try:
                await db.execute("BEGIN IMMEDIATE")
                await insert_version_embedding(
                    db,
                    fact_id=row["fact_id"],
                    version_no=int(row.get("version_no") or 1),
                    prepared=prepared,
                )
                await db.commit()
            finally:
                await db.close()
        result = await recall_stable_facts(
            scope=MemoryScope(session_id=session_id, worldline="steins_gate", identity_mode="self"),
            request=RecallRequest(needs=("usual morning drink", "foods the user avoids")),
        )
        blob = " ".join(result.records.values())
        coverage = {
            "coffee_generated": "美式咖啡" in blob,
            "cilantro_generated": "香菜" in blob,
            "harmonica_sent": "口琴" in blob,
            "status": result.status,
            "sent_n": len(result.records),
            "diagnostics": result.diagnostics,
        }
        coverage["must_sent"] = coverage["coffee_generated"] and coverage["cilantro_generated"]
        tmp.cleanup()
        return coverage

    return asyncio.run(_probe())


def _load_required_checks() -> dict[str, str]:
    sys.path.insert(0, str(BACKEND))
    from tests.test_memory_recall_b5a_r1_product_path import REQUIRED_CHECK_NODES

    return {
        check_id: f"{REQUIRED_CHECK_PREFIX}{node}"
        for check_id, node in REQUIRED_CHECK_NODES.items()
    }


def _junit_nodeid(case: ET.Element) -> str:
    classname = (case.attrib.get("classname") or "").strip()
    name = (case.attrib.get("name") or "").strip()
    if not classname or not name:
        return ""
    return f"{classname.replace('.', '/')}.py::{name}"


def _junit_case_status(case: ET.Element) -> str:
    if case.find("skipped") is not None:
        return "skipped"
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed"
    return "passed"


def evaluate_required_nodes(junit_path: Path, required: dict[str, str]) -> dict:
    """Exact module-qualified node ids only. Each required id must appear once as passed."""
    empty = {
        check_id: {"node": nodeid, "status": "absent_junit", "count": 0, "statuses": []}
        for check_id, nodeid in required.items()
    }
    if not junit_path.is_file():
        return {
            "required_checks": empty,
            "required_failures": list(required),
            "junit_ok": False,
            "gate_ok": False,
            "reason": "absent_junit",
        }
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError:
        return {
            "required_checks": {
                check_id: {"node": nodeid, "status": "malformed_junit", "count": 0, "statuses": []}
                for check_id, nodeid in required.items()
            },
            "required_failures": list(required),
            "junit_ok": False,
            "gate_ok": False,
            "reason": "malformed_junit",
        }
    by_node: dict[str, list[str]] = {}
    for case in root.iter("testcase"):
        nodeid = _junit_nodeid(case)
        if not nodeid:
            return {
                "required_checks": {
                    check_id: {"node": nodeid, "status": "malformed_junit", "count": 0, "statuses": []}
                    for check_id, nodeid in required.items()
                },
                "required_failures": list(required),
                "junit_ok": False,
                "gate_ok": False,
                "reason": "malformed_testcase",
            }
        by_node.setdefault(nodeid, []).append(_junit_case_status(case))
    report = {}
    failures: list[str] = []
    for check_id, nodeid in required.items():
        statuses = list(by_node.get(nodeid, []))
        count = len(statuses)
        if count == 0:
            status = "missing"
        elif count != 1:
            status = "duplicate"
        else:
            status = statuses[0]
        report[check_id] = {
            "node": nodeid,
            "status": status,
            "count": count,
            "statuses": statuses,
        }
        if not (count == 1 and status == "passed"):
            failures.append(check_id)
    return {
        "required_checks": report,
        "required_failures": failures,
        "junit_ok": True,
        "gate_ok": not failures,
        "reason": "" if not failures else "required_nodes",
    }


def _write_probe_suite(path: Path, required: dict[str, str], scenario: str, target_id: str) -> None:
    suite = ET.Element("testsuite")
    target_node = required[target_id]
    target_name = target_node.rsplit("::", 1)[-1]
    for check_id, node in required.items():
        if check_id == target_id and scenario in {"short-name-impostor", "one-missing"}:
            continue
        name = node.rsplit("::", 1)[-1]
        case = ET.SubElement(
            suite,
            "testcase",
            classname="tests.test_memory_recall_b5a_r1_product_path",
            name=name,
        )
        if check_id == target_id and scenario == "one-failing":
            ET.SubElement(case, "failure", message="controlled required failure")
        if check_id == target_id and scenario == "duplicate-failed-then-passed":
            ET.SubElement(case, "failure", message="controlled required failure")
    if scenario == "short-name-impostor":
        ET.SubElement(suite, "testcase", classname="tests.unrelated_module", name=target_name)
    elif scenario == "duplicate-failed-then-passed":
        ET.SubElement(
            suite,
            "testcase",
            classname="tests.test_memory_recall_b5a_r1_product_path",
            name=target_name,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def run_negative_runner_probes(required: dict[str, str], run_dir: Path) -> dict:
    probe_dir = run_dir / "negative-probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    expected_fail = True
    scenarios = [
        ("short-name-impostor", "stream_openai_compatible_fragments"),
        ("duplicate-failed-then-passed", "stream_openai_responses_call_id_item_id"),
        ("one-missing", "stream_openai_compatible_fragments"),
        ("one-failing", "stream_openai_responses_call_id_item_id"),
    ]
    for scenario, target_id in scenarios:
        path = probe_dir / f"{scenario}.xml"
        _write_probe_suite(path, required, scenario, target_id)
        evaluated = evaluate_required_nodes(path, required)
        row = {
            "scenario": scenario,
            "required_target": target_id,
            "gate_ok": evaluated["gate_ok"],
            "status": evaluated["required_checks"].get(target_id, {}).get("status"),
            "count": evaluated["required_checks"].get(target_id, {}).get("count"),
            "exit_would_be": 0 if evaluated["gate_ok"] else 1,
        }
        if evaluated["gate_ok"]:
            expected_fail = False
        rows.append(row)
    malformed_path = probe_dir / "malformed.xml"
    malformed_path.write_text("<not-junit", encoding="utf-8")
    malformed = evaluate_required_nodes(malformed_path, required)
    rows.append(
        {
            "scenario": "malformed-junit",
            "required_target": None,
            "gate_ok": malformed["gate_ok"],
            "status": malformed.get("reason"),
            "count": 0,
            "exit_would_be": 0 if malformed["gate_ok"] else 1,
        }
    )
    if malformed["gate_ok"]:
        expected_fail = False
    absent = evaluate_required_nodes(probe_dir / "missing.xml", required)
    rows.append(
        {
            "scenario": "absent-junit",
            "required_target": None,
            "gate_ok": absent["gate_ok"],
            "status": absent.get("reason"),
            "count": 0,
            "exit_would_be": 0 if absent["gate_ok"] else 1,
        }
    )
    if absent["gate_ok"]:
        expected_fail = False
    return {
        "probes": rows,
        "all_negative_exits_nonzero": expected_fail,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PYTHONUTF8"] = "1"
    run_id = datetime.now(timezone.utc).strftime("b5a-offline-%Y%m%dT%H%M%SZ")
    run_dir = SCRATCH / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pytest_env = os.environ.copy()
    pytest_env.pop("AMADEUS_MEMORY_MODE", None)
    env = {
        key: pytest_env[key]
        for key in sorted(pytest_env)
        if key.startswith(("HF_", "PYTHON", "AMADEUS"))
    }
    capture_dir = run_dir / "adapter-captures"
    capture_dir.mkdir(parents=True, exist_ok=True)
    junit_path = run_dir / "junit.xml"
    pytest_env["B5A_CAPTURE_DIR"] = str(capture_dir)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"--junitxml={junit_path}",
        *PYTEST_TARGETS,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=pytest_env,
    )
    required = _load_required_checks()
    evaluated = evaluate_required_nodes(junit_path, required)
    required_report = evaluated["required_checks"]
    required_failures: list[str] = list(evaluated["required_failures"])
    negative = run_negative_runner_probes(required, run_dir)
    if not negative["all_negative_exits_nonzero"]:
        required_failures.append("runner_negative_probes")
    (run_dir / "required-checks.json").write_text(
        json.dumps(
            {
                "required_checks": required_report,
                "required_failures": required_failures,
                "junit_ok": evaluated["junit_ok"],
                "junit_reason": evaluated.get("reason"),
                "negative_probes": negative,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "negative-probes.json").write_text(
        json.dumps(negative, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    e5_report = _run_e5_coverage() if E5_SNAPSHOT.is_dir() else {"skipped": True, "reason": "snapshot_missing"}
    (run_dir / "pytest.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "pytest.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    e5_present = E5_SNAPSHOT.is_dir()
    e5_note = "present" if e5_present else "missing; E5 coverage not claimed"
    owned = [
        "backend/app/agent_tools.py",
        "backend/app/routers/chat_ws.py",
        "backend/app/services/prompt_compiler.py",
        "backend/app/services/memory_v11/retrieval.py",
        "backend/app/services/memory_v11/jobs.py",
        "backend/app/services/memory_v11/recall.py",
        "backend/app/services/memory_v11/reconciliation_select.py",
        "backend/app/services/turn_events.py",
        "backend/app/services/turn_tool_orchestrator.py",
        "backend/app/services/provider_adapters.py",
        "desktop/src/components/SettingsModal.tsx",
    ]
    hashes = {}
    for rel in owned:
        path = WORKTREE / rel
        if path.is_file():
            hashes[rel] = sha256_file(path)
    summary = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": cmd,
        "cwd": str(BACKEND),
        "exit_code": proc.returncode,
        "offline_gate1_ok": proc.returncode == 0
        and evaluated["gate_ok"]
        and negative["all_negative_exits_nonzero"]
        and not required_failures
        and (not e5_present or bool(e5_report.get("must_sent"))),
        "acceptance_ok": False,
        "REAL_PROVIDER_NOT_RUN": True,
        "required_checks": required_report,
        "required_failures": required_failures,
        "negative_probes": negative,
        "adapter_captures": sorted(path.name for path in capture_dir.glob("*.json")),
        "e5_snapshot": str(E5_SNAPSHOT),
        "e5_snapshot_present": e5_present,
        "e5_note": e5_note,
        "e5_coverage": e5_report,
        "env": env,
        "owned_path_sha256": hashes,
        "pytest_targets": PYTEST_TARGETS,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metadata = [
        f"# B5A offline run {run_id}",
        "",
        f"exit_code: {proc.returncode}",
        f"offline_gate1_ok: {summary['offline_gate1_ok']}",
        "acceptance_ok: false",
        "REAL_PROVIDER_NOT_RUN: true",
        f"required_failures: {required_failures or '[]'}",
        f"negative_probes_nonzero: {negative['all_negative_exits_nonzero']}",
        f"adapter_captures: {summary['adapter_captures']}",
        f"e5_snapshot_present: {e5_present} ({e5_note})",
        "",
        "command:",
        " ".join(cmd),
        "",
        "pytest tail:",
        "\n".join((proc.stdout or "").splitlines()[-40:]),
    ]
    (run_dir / "RUN-METADATA.md").write_text("\n".join(metadata) + "\n", encoding="utf-8")
    (SCRATCH / "latest-run.json").write_text(
        json.dumps({"run_id": run_id, "dir": str(run_dir), "exit_code": proc.returncode}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"run_id": run_id, "exit_code": 0 if summary["offline_gate1_ok"] else 1, "offline_gate1_ok": summary["offline_gate1_ok"]}, ensure_ascii=False))
    return 0 if summary["offline_gate1_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
