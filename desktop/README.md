# Amadeus Desktop

Primary Amadeus product surface: Tauri 2 + React 19 + TypeScript + Vite.
Repository-wide rules are in `../AGENTS.md`; live implementation state is in
`../docs/amadeus-rebuild-status.md`.

## Prerequisites

- Node.js compatible with the tracked lockfile
- Rust toolchain (`rustup`)
- Windows MSVC Build Tools and WebView2 for the real Tauri shell
- Backend running at `http://127.0.0.1:8000`

## Setup and run

```powershell
Set-Location desktop
npm ci

# Browser development surface at http://localhost:1420
npm run dev

# Real Tauri/WebView2 development shell
npm run tauri -- dev
```

Vite proxies `/api` and `/ws` to the backend on port 8000. Browser evidence is
not Tauri/WebView2 evidence; see `../docs/agents/verification-ladder.md`.

## Validation

```powershell
npm run test
npm run build
```

Run focused tests first for a bounded change. Real interaction work may also
require Chrome and Tauri evidence under the owning contract.

## Product structure

```text
App: splash -> Salieri login -> transition -> Workstation
Workstation: conversations + dialogue + character presence + utilities
Settings: BASIC | CONNECTIONS | VOICE
Header MEMORY: Memory Space (3D / Compatibility / LIST / Inspector surfaces)
```

The backend owns durable conversation, worldline, identity, provider, and
Memory projection semantics. The Desktop must not silently reinterpret those
contracts.

## Assets and audio

- `public/assets/images/` — character and transition assets
- `public/assets/ui/` — product UI assets
- `public/assets/audio/` and `public/audio/` — voice references, BGM, and SFX
- `public/bg/` — login/background assets

GPT-SoVITS and voice-reference configuration belong to the backend/runtime
setup. Missing optional voice assets must not block text-capable chat.

## Packaging

`npm run tauri -- build` can produce Windows bundles after the toolchain is
installed. Packaging, publishing, and update behavior are user gates; do not run
or claim them as release evidence without explicit authorization.
