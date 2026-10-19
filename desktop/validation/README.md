# Browser evidence walkthrough

Reusable worker/validator procedure for the local development mock. This is
documentation outside `src/`, with no package or production entry point.
The mock [catalog](../src/dev-mock/README.md) defines the available scenarios.
Mission `AGENTS.md`, `services.yaml` and `library/user-testing.md` remain the
authority for boundaries, commands, browser policy and assertion requirements.

## Start and isolate

1. Run the mission init procedure. On this Windows host use its documented
   PowerShell equivalent if `sh` is unavailable.
2. From the repository root, start the manifest's `dev` service:
   `cd desktop && npm run dev -- --port 1420 --strictPort`.
   Healthcheck with `curl.exe -sf http://localhost:1420`. Use only port 1420;
   the mock REST and WebSocket transports share that listener. Do not start
   the backend, set `AMADEUS_DEV_MOCK=0`, configure providers, or enable audio.
3. Use Playwright MCP as documented by the mission driver policy. On the
   verified Windows host agent-browser navigation/DOM reads work, but its
   screenshot path fails and ffmpeg is absent. Do not retry those capture
   paths repeatedly. If agent-browser is used for reads, always pass the
   assigned `--session` and close that session before handoff. Never use the
   default session or close sessions belonging to other workers.
4. Run one matrix row at a time. Use an isolated browser context for each
   parallel worker: fixture cookies are origin-wide. Never run the full
   Vitest suite concurrently with browser validation.

## Destinations and capture matrix

Set `missionDir`, `milestone`, `featureId` and a unique `runId` for the current
task. **All paths passed to tools must be absolute.** For example:

```powershell
$repo = 'D:\Amadeus\worktrees\frontend-kimi-20261017'
$missionDir = 'C:\Users\reause\.factory\missions\<mission-id>'
$milestone = 'm1-foundation'
$featureId = 'm1-evidence-infra'
$runId = '20261017-review-01'
$stage = Join-Path $repo ".playwright-mcp\$featureId\$runId"
$evidence = Join-Path $missionDir "validation\$milestone\worker-evidence\$featureId\$runId"
New-Item -ItemType Directory -Force -Path $stage, $evidence
```

Playwright MCP writes inside the repository. Stage screenshots and diagnostic
files under `$stage`, then copy **only this run's** files to `$evidence`.
Never commit screenshots, recordings, browser profiles or generated logs.

| Row / subdirectory | Saved app worldline | Emulated reducedMotion | Expected `.ws2[data-wl]` |
| --- | --- | --- | --- |
| `sg-normal` | `steins_gate` | `no-preference` | `sg` |
| `beta-normal` | `beta` | `no-preference` | `beta` |
| `sg-reduced` | `steins_gate` | `reduce` | `sg` |
| `beta-reduced` | `beta` | `reduce` | `beta` |

Use `browser_resize` with `{width:1920,height:1080}` for this tour.
Login/responsive features also repeat their specified checks at 1366×768
and any contract-required narrow viewport. Set media **before** loading each
row with `browser_emulate_media({reducedMotion:"reduce"})` or `"no-preference"`.
`colorScheme` does not select an Amadeus worldline.

## Repeat this journey for each row

The calls below are Playwright MCP tool arguments, not a shell script.
Use `browser_snapshot` to inspect changed screens and obtain fresh references.
The stable selectors listed here were verified at M1; refresh them if later
milestones change the markup. Do not reuse snapshot refs across navigations.

1. Navigate to `http://localhost:1420/?scenario=streaming`. In this disposable
   browser only, use `browser_evaluate` to reset client/scenario state and
   select the row's worldline:

   ```js
   () => {
     localStorage.clear();
     sessionStorage.clear();
     localStorage.setItem('amadeus_pc_worldline', 'steins_gate'); // or 'beta'
   }
   ```

   Navigate to the same URL again. Clearing storage takes effect on this
   navigation: the mock creates a fresh client, and React reads the saved
   worldline. Do not merely edit `data-wl`; that bypasses application state.
   URL scenario selection takes precedence over saved scenario selection.

2. Wait until fonts are ready and `#salieri-user` has value `Salieri`.
   M1's typewriter takes time, so submitting immediately after navigation can
   produce `无效的 USER ID`. Use a bounded readiness check, not a long sleep.
   Capture `01-login.png`. Login is worldline-neutral: the drafting sheet looks
   the same on both lines; the saved worldline only shows once the workstation
   is revealed behind the pen. A wrong passphrase must read `密码错误` in the
   `role="alert"` line and return focus to `#salieri-pwd` with its text selected.

3. `browser_fill_form` targets `#salieri-pwd` with the mock passphrase
   `Gott würfelt nicht`, then `browser_click` targets `.decrypt-btn`.
   Verify **POST `/api/auth/salieri` → 200** in network diagnostics.
   The actual route is not `/api/login`. Wait for `.ws2`, an enabled
   `textarea[aria-label='输入消息']`, ONLINE status and the entrance to
   finish: `.sl-root` is removed and `.ws2` loses `data-entrance` and
   `inert` (about 2.5 s: the pen sweeps the sheet away left to right and the
   AMADEUS title flies into the header wordmark; any key or click skips; under
   `reduce` the terminal fades out in 0.3 s with no entrance). Focus then lands
   in the composer. The entrance plays once per login — menus, Memory and
   session switches never replay it.
   Capture `02-workstation-empty.png`.

4. Fill `textarea[aria-label='输入消息']` with `记录本次视觉走查`, then click
   `button[aria-label='发送']`. Capture `03-stream-frame-01.png`,
   `03-stream-frame-02.png`, `03-stream-frame-03.png` while sentence pairs
   arrive, followed by `03-stream-complete.png`. The default fixture emits
   three Japanese/Chinese pairs, the final Chinese text being
   `别急着下结论，等下一次结果。`. Verify each Japanese node has `lang="ja"`.
   Record timestamps and message counts with each capture. A few staged
   frames prove state progression, not exact animation duration or frame rate.

5. Click `button[aria-label='系统设置']`. The four `.ws2-sys-item` buttons
   appear in this order: 世界线 / WORLDLINE, 连接 / CONNECTIONS,
   声音 / SOUND, 角色与对话 / CHARACTER. Click each and capture:
   `04-menu-worldline.png`, `04-menu-connection.png`,
   `04-menu-sound.png`, `04-menu-character.png`.
   Inspect `.ws2-sys-item[aria-current='page']` to confirm the selected page.
   Press Escape and verify `.ws2-sysmenu.open` is absent.
   For this surface sweep choose the saved worldline before login;
   testing the real shift/commit/isolation contract is a separate M3 journey.

6. Click `button[aria-label='MEMORY']`; wait for populated content and capture
   `05-memory-overview.png`. Click `[data-testid='memory-mode-archive']`
   and capture `06-memory-archive.png` with the fixture's three facts.
   Return with `[data-testid='memory-mode-constellation']`, click
   `.memory-space-find-toggle` and capture `07-memory-search.png`.
   Fill `input[aria-label='搜索记忆']` with a word in the fixture, e.g. `实验`,
   wait for results, and capture `08-memory-filtered.png`.
   Clear it and capture `09-memory-search-cleared.png`. Record the result
   text/count before, during and after filtering; the full list must return.
   Close search with `.memory-space-find-toggle`, then click
   `[data-testid='memory-back']` and capture `10-workstation-return.png`.
   Memory's back control unwinds nested state first, so close search before
   using it. Confirm `.memory-space` is absent and the conversation remains.

Use `browser_take_screenshot` with
`{filename:"<absolute-stage>/<row>/<name>.png",scale:"css"}`.
Create the row directory first. Capture viewport images at the same size for
comparison. For a visual refactor, collect this same population into
`before/` and `after/` before changing the UI.

## Diagnostics and motion evidence

At checkpoints save `browser_snapshot`, `browser_console_messages` (include
debug messages), and `browser_network_requests({static:true})` to the stage
directory. Keep raw diagnostics and a short classification; do not hide errors.
Expected on this mock host:

- Optional `/api/asset-pack/manifest.json` → 404, documented by the mock catalog.
- A StrictMode WebSocket handshake cancellation followed by a working socket.
- Aborted superseded Memory fetches during unmount/filtering.
- The motion library's reduced-motion notice when that preference is emulated.
- Intentional 401/503 responses only when testing the corresponding scenario.

App JS/CSS/fonts must load successfully. Unexplained errors, failed requests,
exceptions or an unusable screen are findings. Preserve enough URL/status
information to distinguish the expected cases; never print real credentials.
The mock emits no audio and never calls a real Provider/TTS.

Record this metadata through `browser_evaluate` with every named state:

```js
() => ({
  capturedAt: new Date().toISOString(),
  viewport: { width: innerWidth, height: innerHeight },
  reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
  storedWorldline: localStorage.getItem('amadeus_pc_worldline'),
  worldline: document.querySelector('.ws2')?.dataset.wl,
  memoryWorldline: document.querySelector('.memory-space')?.dataset.worldline,
  japanese: [...document.querySelectorAll('[lang="ja"]')].map(e => e.textContent),
  menuPage: document.querySelector('.ws2-sys-item[aria-current="page"] .zh')?.textContent,
  results: document.querySelector('[aria-label="记忆结果"]')?.innerText
})
```

For motion assertions collect before/during/after frames or a recording that
actually resolves the asserted stages. Save frame timestamps/action timing;
if the transition is too quick to capture, use the mission-approved slow
capture/recording tooling and report the limitation until captured.
On this host use staged Playwright screenshots, since agent-browser capture
and ffmpeg recording are unavailable. Still frames alone cannot establish
smoothness, precise durations or ≤2 simultaneous animations.
Reduced-motion screenshots establish the tested preference and usability;
asserting fade-only/static behavior also requires frame or code evidence.

## Gates, archive and handoff

Close the Playwright browser with `browser_close`, then stop only the dev
service you started using the exact manifest `stop` command. Close any named
agent-browser sessions you opened. Run manifest commands `build`
(`cd desktop && npm run build`) and `test` (`cd desktop && npx vitest run`).
Save each complete terminal output and actual exit code under `gates/`;
never pipe through `head`/`tail` or infer success from the last output line.
The M1 baseline is 524 passed + 2 expected-fail; added tests increase that
count. Vitest's aggregate summary may include the expected-fail cases in its
green total: inspect their `.fails` declarations and report both conventions.

```powershell
Get-ChildItem -LiteralPath $stage | Copy-Item -Destination $evidence -Recurse
```

Copy into a fresh destination so earlier evidence is not overwritten.
Review the copied filenames, image dimensions and diagnostics. Add a report
with source commit, browser/viewport, matrix rows, assertion mapping, findings,
gate exit codes/test counts, and relative screenshot/log paths. Link existing
workers' before/after evidence where the milestone requires comparison.
Check `git status`: only the intended source/manual changes may be committed.

A milestone-closing handoff sets `returnToOrchestrator: true` and includes the
evidence report, gate logs and local run guide for U's review. Passing gates
and browser checks do not constitute U's acceptance of the milestone.
