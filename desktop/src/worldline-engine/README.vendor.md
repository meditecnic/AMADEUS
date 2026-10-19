# Vendored worldline runtime

Source: [meditecnic/worldline-transition](https://github.com/meditecnic/worldline-transition),
MIT, copyright (c) 2026 meditecnic. The original MIT text is retained in
`LICENSE`. Imported on 2026-10-19 from the clean local checkout
`D:/Amadeus/_local-artifacts/worldline-video-20261004/src/`, commit
`5d490a2a3448346f6a29ae1d2d87f872148725ec`.

This is the published descendant of the R6-E frozen prototype (freeze
`6f065ca`, code `68ee141`; local recording receipt also references prototype
`371060b`). Its procedural geometry, shaders, shot timing and digit lock
sequence are retained. The source README describes it as the standalone
transition intended for AMADEUS, with procedural visuals and no original
video, screenshots or audio.

## Retained and trimmed

- `engine/`: WebGL2 renderer/helpers and a cathode atlas drawn with Canvas2D paths.
- `show/`: frame data, procedural geometry, timeline and playback controller.
  `mock.ts` and deterministic seek helpers remain for tests/demo fallback.
- `shaders/`: GLSL embedded in TypeScript, resolved by desktop's existing
  TypeScript bundler mode and Vite pipeline. No shader loader is needed.
- `assets/OFL.txt`: upstream font copyright notices and SIL OFL 1.1 text.
  The upstream Noto Sans SC and IBM Plex Mono WOFF2 files are demo UI assets,
  unused by the renderer. They are omitted along with `fonts.css`; no font
  binaries or new font families are introduced. A future host uses desktop's
  existing fonts.
- `index.ts`: side-effect-free runtime exports for the desktop host.

Excluded: upstream `main.ts`, HTML, all global/demo CSS, preview images,
package/build configuration, dependencies, generated distributions, Git
metadata, and all capture/recording scripts. Recorded MP4/WAV files and
frame images are excluded. The runtime never loads original artwork,
image/video textures, fonts or audio. GPU textures contain generated glyph
bytes or render targets only. There are no audio elements or audio contexts.
No runtime dependencies were added.

## Host result interface

```ts
import { Controller, parseReading, type Outcome } from './worldline-engine';

// The host owns one real request and resolves only after its matching
// acknowledgement and target session are ready.
const outcome: Promise<Outcome> = hostRequest.then(() => 'success');
controller.start({
  origin: parseReading('1.048596')!,
  target: parseReading('1.129848')!,
  reduced: false,
  scenario: 'success', // used only if outcome is omitted
  outcome,
});
```

Supplying `outcome` bypasses `mockSwitch` entirely. Rejection is a failure;
an unresolved promise holds at `T.GATE` without locking. A run ID prevents
replaced, exited or disposed runs from consuming late success or rejection.
Disposal stops playback and aborts only the local demo timer. The host still
owns and reconciles an already-sent network request.

Without `outcome`, `scenario` selects the original local mock, useful for
tests. `harnessSeek` remains a deterministic test hook. Neither is a source
of production acknowledgements.

The React host is `workstation/WorldlineShift.tsx`, with request ownership in
`ShiftTransaction` and Workstation. A structural render/finish adapter allows
one controller to continue through GPU loss without replacing its outcome.
CSS/reduced-motion use a static final lock once the outcome is known.
The controller's
original `exit()` returns to standby; production click/Esc should share the
host's skip action, which calls `skip()` and never invents an outcome.

## Timing and resource ownership

Keep the renderer's requestAnimationFrame playback and narrative seconds.
`T.GATE` is 6.20, while locks begin at 6.13 after a successful result. Digits
lock left to right, with the last at 7.63 (`T.HOLD0`) for an early result.
`resultT` can delay individual locks when a result arrives late; inspect the
rendered `FrameState.readout.locked` values rather than deriving publication
from the gate, the displayed string, or `phase === 'ended'`. The final digit
lock precedes the remaining hold/exit narrative and success end at 10.55.

The renderer retains `onLost`/`onRestored`, resize, and idempotent `dispose()`.
The host owns canvas sizing/listeners and fallback policy. On teardown, dispose
the controller first, then the renderer; renderer disposal removes its context
listeners and frees programs, buffers, textures, targets and vertex arrays.

Desktop's existing strict `tsc` includes these sources. The focused bundling
test builds this entry with Vite in memory, including every shader, without
mounting the demo or creating a graphics context on import. The application
consumes this entry in Step 2; importing it creates no graphics context.
