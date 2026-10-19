# Local development scenarios

`npm run dev -- --port 1420 --strictPort` starts the frontend and mock together.
No backend, provider, TTS, credentials, or extra listener is needed. The Vite
plugin has `apply: 'serve'`; its server modules and injected selector script do
not enter `npm run build` or `npm run preview`.

Open `http://localhost:1420/?scenario=streaming`. Every scenario starts at login.
Use **`Gott würfelt nicht`**. The real frontend sends
`POST /api/auth/salieri` with `{"password":"..."}`. All scenarios inherit
**HTTP 200 on success and HTTP 401 on an incorrect passphrase**;
`login-failure` forces **401 even for the correct passphrase**. The failure
message is the response's `detail` string. These statuses live in the JSON
catalog, including the forced-failure scenario.

## Catalog

Files in `scenarios/` are the source of truth. `streaming.json` is the complete
base; other files declare `extends: "streaming"` and override it. Top-level
arrays replace the base; `login` and `shift` merge individual fields.

| Name / JSON file | Contents |
| --- | --- |
| `streaming` (default) | Login, SG/β session histories, three Japanese/Chinese sentence pairs and memory facts |
| `login-success` | Explicit login success 200 / failure 401 |
| `login-failure` | Forced login failure 401 with a retry message |
| `session-list` | Seeded SG and β histories; create, rename, pin, forget and delete |
| `memory` | Three facts, one topic, v11 status, archive/details and graph |
| `empty` | Empty session list, facts, topics and graph; sending from draft still works |
| `error` | Login 200; conversation/memory GETs 503; chat sends a recoverable error |
| `offline` | Login 200; WS auth rejected for unavailable-session UI |
| `shift-failure` | Correlated `worldline.switch_error` after 500ms; source worldline retained |
| `shift-slow` | Correlated `worldline.switched` delayed 20000ms; observable confirmation gate even with shader warmup, same socket remains open |

Sentence `delayMs` values are waits **after the preceding sentence** (or after
`turn.started` for the first). `completionDelayMs` follows the final pair.
`shift.delayMs` is independent of animation timing, allowing M3 to test waiting
and rollback. Edit the JSON and restart/reload to reset the fixtures.

## Switching and isolation

URL selection wins over saved selection:

```js
localStorage.setItem('amadeus_mock_scenario', 'empty');
location.href = '/'; // remove any ?scenario= override and reload
```

`?scenario=memory` saves that choice to the same localStorage key. Unknown names
fall back to `streaming`. To reset, clear local/session storage and reload, or
restart Vite. Changing scenarios selects a separate in-memory fixture store;
returning to one restores its edits until the server restarts.

A dev-only script runs before the application entry and writes
`amadeus_mock_scenario` and `amadeus_mock_client` cookies, so unchanged fetch
and WebSocket calls use the same scenario. The client ID survives reload in
sessionStorage. Data is partitioned by client, scenario, session ID and
worldline; memory additionally partitions identity mode. Concurrent validators
should use separate browser contexts/profiles (cookies are origin-wide).
Direct REST/WS tooling can pass `?scenario=<name>` or these cookies.

## Transport and limits

- REST is intercepted at `/api/*`, plus local `/health` and
  `/health/dependencies`. Unknown endpoints return JSON 404 and never proxy.
- WS handles **`/ws/chat?session_id=...`** (the real hook URL) and `/ws` on the
  **same Vite HTTP server, port 1420**. HMR upgrades use Vite's listener.
- Protocol v2: `auth` → `session.ready`, draft materialization on `chat.send`,
  `turn.started` → timed `segment.ready` pairs → `turn.completed`.
  `turn.cancel`, history/worldline switches and socket close clear pending
  timers. Correlated request IDs and generation IDs are preserved. Heartbeat
  `ping` gets `pong`. No audio frames are generated.
- Provider/model fixtures are callable locally. Credential endpoints discard
  supplied values. Search stays unconfigured; custom provider creation is not
  simulated (JSON 404). No secrets are stored.
- The optional `/api/asset-pack/manifest.json` deliberately returns **404**
  (`optional_asset_pack_absent`), keeping the existing original-artwork-free
  fallback. Its single expected resource-load console message is benign.
  App-owned JS, CSS and fonts must load successfully. A 2xx manifest would
  incorrectly activate unavailable artwork and retired asset transitions.
- Memory experiences/observations start empty. Facts support filter/pagination,
  versioned text corrections, pin/topic presentation, deletion and topic rename.
- Request/frame payloads are capped at 64 KiB. State is disposable and resets
  when the dev server restarts. This is a verification fixture, not a backend.

For deliberate real-backend development only, set `AMADEUS_DEV_MOCK=0` before
starting Vite to retain the existing proxy configuration. The default mock
never contacts those proxy targets.

## Verification

```powershell
cd desktop
npx vitest run src/dev-mock
npm run build
npx vitest run
```

Browser checks: cold boot → incorrect login → successful login → send a draft
message and observe three separate bilingual arrivals → select a history →
open Memory. Reload with `empty`, `error`, `offline`, `shift-failure` or
`shift-slow` to reach the corresponding states. Evidence belongs in the
mission validation directory, never in git.

For the reusable SG/β × normal/reduced-motion capture matrix, screenshot
destinations, diagnostics, staged motion evidence and milestone gate handoff,
follow the [browser evidence walkthrough](../../validation/README.md).

## Shift preview

Open `http://localhost:1420/?scenario=streaming&force-fallback=1` for the static
CSS shift path. This capability override is read only in Vite development,
has no production fault control, and does not alter requests or ACKs.
`?scenario=shift-slow&force-fallback=1` makes pending click/Esc waiting easy
to inspect. `shift-failure` retains the source theme/draft; reduced-motion
also chooses CSS automatically. During WebGL playback the standard browser
`WEBGL_lose_context` extension can exercise context loss.

The menu's worldline page starts one correlated `worldline.switch` immediately.
The source UI remains visible underneath until matching target readiness and
the final digit lock, then publishes once. Both skip controls share the same
action. CSS, context loss and sustained low fps continue the same transaction.
