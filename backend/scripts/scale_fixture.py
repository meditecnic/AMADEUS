"""Offline two-year fixture. Run from backend: python -m scripts.scale_fixture.

Uses production schema initialization, then equivalent transactional SQL for
messages, v11 observations/sources/facts/experiences and content-free usage.
Experiences have no production embedding column: their synthetic 768-D vectors
are an ordered fixture-only .f32 sidecar, never a new application schema/table.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import numpy as np

SESSION = "scale-user"
WORLDLINES = ("steins_gate", "beta")
QUERY = "周末登山旅行，紅茶の好みを覚えていますか"


def vector(rng):
    values = rng.standard_normal(768)
    return (values / np.linalg.norm(values)).astype("<f4").tobytes()


def create_fixture(root: Path, *, conversations=300, messages=60000, facts=5000,
                   experiences=20000, usage=60000, long_history=2000, seed=217):
    if conversations < 2 or min(facts, experiences, usage) < 1:
        raise ValueError("need >=2 conversations per worldline and positive memory/usage counts")
    if long_history < 2 or messages // 2 < long_history + 2 * (conversations - 1):
        raise ValueError("messages must cover the long conversation and >=2 per other conversation")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)  # Never overwrite an existing data directory.
    os.environ["AMADEUS_DATA_DIR"] = str(root)
    os.environ.pop("AMADEUS_DB_PATH", None)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    from app import db

    db.reset_initialization_cache()
    asyncio.run(db.init_db())
    rng = np.random.default_rng(seed)
    start = datetime(2024, 11, 8, tzinfo=timezone.utc)
    lengths = []
    with (root / "scale-experience-vectors.f32").open("wb") as sidecar:
        for wi, wl in enumerate(WORLDLINES):
            count = messages // 2 + (wi < messages % 2)
            base, extra = divmod(count - long_history, conversations - 1)
            counts = [long_history] + [base + (i < extra) for i in range(conversations - 1)]
            sources = []
            with closing(sqlite3.connect(db._resolve_path(wl))) as conn, conn:
                conn.execute("PRAGMA foreign_keys=ON")
                for ci, size in enumerate(counts):
                    cid = f"scale-{wi}-{ci}"
                    stamp = (start + timedelta(days=ci * 730 / conversations)).isoformat()
                    conn.execute("""INSERT INTO conversations
                        (id,session_id,title,title_source,identity_mode,provider_id,model_id,
                         created_at,updated_at,last_active_at) VALUES(?,?,?,'manual','self',
                         'deepseek','deepseek-v4-flash',?,?,?)""",
                                 (cid, SESSION, f"旅行记录・会話 {ci}", stamp, stamp, stamp))
                    for mi in range(size):
                        text = (f"{ci}/{mi} 周末登山旅行，紅茶が好きです。 "
                                + "今日の出来事と研究记录。" * (1 if mi % 4 else 100))
                        created = (start + timedelta(seconds=(len(sources) * 2 + mi % 2) * 730 * 86400 / count)).isoformat()
                        cur = conn.execute("""INSERT INTO messages
                            (session_id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)""",
                                           (SESSION, cid, "user" if mi % 2 == 0 else "assistant", text, created))
                        if mi % 2 == 0:
                            sources.append((cur.lastrowid, cid, created, hashlib.sha256(text.encode("utf-8")).hexdigest()))
            lengths.append(counts)
            with closing(sqlite3.connect(db._resolve_path(wl, "memory"))) as conn, conn:
                conn.execute("PRAGMA foreign_keys=ON")
                for kind, total in (("fact", facts), ("experience", experiences)):
                    for i in range(total // 2 + (wi < total % 2)):
                        oid, item = f"obs-{kind}-{wi}-{i}", f"{kind}-{wi}-{i:06d}"
                        text = f"周末登山旅行，紅茶はダージリンが好きです。記録 {i}"
                        semantic = json.dumps({"subject": "user", "predicate": "preference", "value": text}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        fingerprint = hashlib.sha256(semantic.encode("utf-8")).hexdigest()
                        source = sources[(i * 37 + (0 if kind == "fact" else 11)) % len(sources)]
                        mid, cid, created, source_hash = source
                        conn.execute("""INSERT INTO memory_observations
                            (observation_id,session_id,identity_mode,display_text,semantic_json,
                             semantic_fingerprint,evidence_kind,memory_class,confidence,status,created_at,updated_at)
                            VALUES(?,?,'self',?,?,?,'direct_user',?,0.95,'attached',?,?)""",
                                     (oid, SESSION, text, semantic, fingerprint,
                                      "stable_candidate" if kind == "fact" else "episodic", created, created))
                        provenance = [source]
                        other = sources[-1] if sources[0][1] == cid else sources[0]
                        if i % 19 == 0:
                            provenance.append(other)  # Shared evidence must survive exclusive forgetting.
                        conn.executemany("""INSERT INTO memory_observation_sources
                            VALUES(?,?,?,'user',?,?,'present',?)""",
                                         [(oid, s[0], s[1], s[3], s[2], text) for s in provenance])
                        embedding = vector(rng)
                        if kind == "fact":
                            conn.execute("""INSERT INTO stable_facts
                                (fact_id,session_id,identity_mode,state,active_version,is_pinned,created_at,updated_at)
                                VALUES(?,?,'self','active',1,?,?,?)""", (item, SESSION, int(i < 4), created, created))
                            conn.execute("""INSERT INTO stable_fact_versions
                                (fact_id,version_no,display_text,semantic_json,semantic_fingerprint,
                                 confidence,change_kind,valid_from) VALUES(?,1,?,?,?,0.95,'create',?)""",
                                         (item, text, semantic, fingerprint, created))
                            conn.execute("INSERT INTO stable_fact_evidence VALUES(?,1,?,'primary')", (item, oid))
                            conn.execute("INSERT INTO stable_fact_version_embeddings VALUES(?,1,?,768,?)", (item, db.EMBEDDING_MODEL, embedding))
                        else:
                            conn.execute("""INSERT INTO experiences
                                (experience_id,session_id,conversation_id,identity_mode,observation_id,
                                 display_text,semantic_json,semantic_fingerprint,confidence,status,created_at,updated_at)
                                VALUES(?,?,?,'self',?,?,?,?,0.95,'active',?,?)""",
                                         (item, SESSION, cid, oid, text, semantic, fingerprint, created, created))
                            sidecar.write(embedding)
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    with closing(sqlite3.connect(db._resolve_path(kind="control"))) as conn, conn:
        conn.executemany("""INSERT INTO model_usage
            (time_utc,worldline,conversation_id,category,source,provider_id,model_id,input_tokens,output_tokens,reported)
            VALUES(?,?,?,'chat','chat','deepseek','deepseek-v4-flash',400,100,1)""",
                         [((start + timedelta(seconds=i * 1000)).isoformat(), WORLDLINES[i % 2],
                           f"scale-{i % 2}-{i % conversations}") for i in range(usage)])
    manifest = dict(conversations_per_worldline=conversations, messages=messages, facts=facts,
                    experiences=experiences, usage=usage, long_history=long_history, seed=seed,
                    history_lengths=lengths, experience_vectors="worldline order, ascending experience id, <f4[768]; fixture-only")
    (root / "scale-fixture.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="must not already exist; default is a new temporary directory")
    for name, default in (("conversations", 300), ("messages", 60000), ("facts", 5000),
                          ("experiences", 20000), ("usage", 60000), ("long-history", 2000), ("seed", 217)):
        parser.add_argument("--" + name, type=int, default=default)
    args = vars(parser.parse_args())
    root = args.pop("data_dir") or Path(tempfile.mkdtemp(prefix="amadeus-scale-")) / "data"
    create_fixture(root, **args)
    print(root.resolve())
