"""Small offline fixture smoke; the full benchmark is deliberately outside CI."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np


def test_scale_fixture(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    root = tmp_path / "scale"
    command = [sys.executable, "-m", "scripts.scale_fixture", "--data-dir", str(root),
               "--conversations", "3", "--messages", "600", "--facts", "50",
               "--experiences", "200", "--usage", "600", "--long-history", "20"]
    result = subprocess.run(command, cwd=backend, env={**os.environ, "AMADEUS_DATA_DIR": str(root)},
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((root / "scale-fixture.json").read_text(encoding="utf-8"))
    assert manifest["messages"] == 600
    for wl in ("sg", "beta"):
        with closing(sqlite3.connect(root / "worldlines" / wl / "history.sqlite3")) as conn:
            assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 3
            assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 300
            assert conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id LIKE '%-0'").fetchone()[0] == 20
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        with closing(sqlite3.connect(root / "worldlines" / wl / "memory.sqlite3")) as conn:
            assert conn.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0] == 25
            assert conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0] == 100
            blobs = conn.execute("SELECT vector FROM stable_fact_version_embeddings WHERE dimensions=768").fetchall()
            assert len(blobs) == 25
            assert all(abs(np.linalg.norm(np.frombuffer(b[0], dtype="<f4")) - 1) < 1e-6 for b in blobs)
            assert conn.execute("SELECT COUNT(*) FROM memory_observation_sources").fetchone()[0] > 125
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    with closing(sqlite3.connect(root / "control.sqlite3")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 600
    assert (root / "scale-experience-vectors.f32").stat().st_size == 200 * 768 * 4
    # Both determinism and refusing an existing directory protect local rehearsal data.
    second = tmp_path / "repeat"
    repeat = command.copy()
    repeat[repeat.index(str(root))] = str(second)
    subprocess.run(repeat, cwd=backend, check=True, capture_output=True, timeout=60)
    assert (root / "scale-experience-vectors.f32").read_bytes() == (second / "scale-experience-vectors.f32").read_bytes()
    refused = subprocess.run(command, cwd=backend, capture_output=True, timeout=60)
    assert refused.returncode != 0
