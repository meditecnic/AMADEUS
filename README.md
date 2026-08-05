# Amadeus System

Desktop-first *STEINS;GATE* Amadeus AI companion (Chat Helper form).  
Japanese character voice + Chinese subtitles; local desktop + FastAPI backend + TTS.

| Stack | Path |
|---|---|
| Desktop UI (primary) | `desktop/` — React + TypeScript + Vite + Tauri |
| Backend | `backend/` — Python FastAPI + WebSocket |
| TTS | GPT-SoVITS / Voice Pack path (see product docs) |
| Chat model (product lean) | DeepSeek-first — see `CONTEXT.md` |

---

## Root documents (do not conflate)

| File | Audience | Answers | Does **not** answer |
|---|---|---|---|
| **`README.md`** (this file) | Humans cloning the repo | What the project is; how to run; where to read next | Agent law, persona canon, next engineering task |
| **`AGENTS.md`** | Coding agents + contributors | Repo law, single-writer, user gates, validation commands, authority list | Product-form vocabulary; live casting roster |
| **`CONTEXT.md`** | Agents + humans aligning on **product shape** | Chat Helper, Ordinary Fan, Configuration Tax, Vision presets, Three-Column Shell… | Implementation status; how to cast Codex/Grok; persona bible text |

**Multi-agent workflow (lifecycle, casting, handoff, Matt adapter):**  
→ start at [`docs/agents/README.md`](docs/agents/README.md)

**Engineering “what next”:**  
→ [`docs/amadeus-rebuild-status.md`](docs/amadeus-rebuild-status.md)

**After context compaction:**  
→ [`docs/amadeus-context-recovery.md`](docs/amadeus-context-recovery.md)

**Persona / language / memory product decisions:**  
→ [`docs/amadeus-product-qa-memory.md`](docs/amadeus-product-qa-memory.md) + Persona Bible under `backend/characters/`

**Hard product-form decisions:**  
→ [`docs/adr/`](docs/adr/)

---

## Quick start (dev)

```bash
# Backend
cd backend
# prefer project venv if present: .\.venv\Scripts\python.exe -m uvicorn ...
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Desktop UI (PC product surface)
cd desktop
npm run dev
```

Legacy/mobile tree under `frontend/` may exist for older paths; **shipping focus is `desktop/`**.

Validation commands used by agents are listed in `AGENTS.md`.

---

## Layout (high level)

```text
amadeus_web/
  README.md          ← you are here (human front door)
  AGENTS.md          ← agent/contributor law
  CONTEXT.md         ← product-form glossary
  docs/agents/       ← multi-agent ops hub
  docs/              ← status, QA, recovery, atlas, adr…
  backend/           ← API, persona, TTS queue…
  desktop/           ← primary client
  .scratch/          ← roster, tickets, templates (ephemeral)
  .agents/skills/    ← amadeus-handoff / review / verification / persona-guardian
```
