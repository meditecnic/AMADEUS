import { Renderer, evaluateShow, hasGlyphAtlas, parseReading, type GlyphAtlas } from '../worldline-engine';
import { DIVERGENCE } from './Nixie';

/**
 * The GPU stage for worldline shifts, prepared ahead of the click.
 *
 * Building the renderer used to happen inside the click task: 13 programs compiled and linked
 * with blocking status reads, then the cathode atlas rasterized with canvas blur filters (the
 * largest cost, repeated on every shift). Nothing could paint until all of it was done. The stage
 * now warms once in idle time — atlas in a worker, compiles polled through
 * KHR_parallel_shader_compile, one small draw of each shot so the driver finishes its pipelines —
 * and a shift only leases the ready canvas. Between shifts the large targets are freed; programs,
 * buffers and the atlas stay resident.
 */

export interface ShiftStageLease {
  readonly canvas: HTMLCanvasElement;
  readonly renderer: Renderer;
  /** Detach and keep warm for the next shift. */
  release(): void;
  /** The stage misbehaved (context loss, render error, too slow): drop it. */
  discard(): void;
}

interface Stage {
  canvas: HTMLCanvasElement;
  renderer: Renderer;
  leased: boolean;
}

let stage: Stage | null = null;
let warming: Promise<boolean> | null = null;
let abort: AbortController | null = null;
let urgent = false;
let failures = 0;
let unavailable = false;
let atlasJob: Promise<GlyphAtlas | undefined> | null = null;
let worker: Worker | null = null;

const WARM_W = 192;
const WARM_H = 108;
/** One frame per shot family (success and failure branches) so every program/state combination has drawn once. */
const WARM_FRAMES: [number, 'success' | 'fail' | null][] = [
  [0.2, null], [0.8, null], [1.6, null], [2.6, null], [3.6, null], [4.6, null], [5.6, null],
  [6.15, 'success'], [6.6, 'success'], [7.2, 'success'], [8.2, 'success'], [9.4, 'success'],
  [10.1, 'success'], [10.4, 'success'], [6.6, 'fail'], [8.0, 'fail'], [6.4, null],
];

const idle = (timeout: number) => new Promise<void>(resolve => {
  const ric = (window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
  if (urgent || !ric) window.setTimeout(resolve, 0);
  else ric(() => resolve(), { timeout });
});

/** Cathode atlas bytes from a worker; undefined lets the renderer rasterize them itself. */
function atlasFromWorker(): Promise<GlyphAtlas | undefined> {
  if (hasGlyphAtlas()) return Promise.resolve(undefined);
  if (atlasJob) return atlasJob;
  if (typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined') return Promise.resolve(undefined);
  atlasJob = new Promise<GlyphAtlas | undefined>(resolve => {
    let settled = false;
    const finish = (atlas?: GlyphAtlas) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(guard);
      worker?.terminate();
      worker = null;
      if (!atlas) atlasJob = null;
      resolve(atlas);
    };
    const guard = window.setTimeout(() => finish(), 6000);
    try {
      worker = new Worker(new URL('./glyphAtlas.worker.ts', import.meta.url), { type: 'module' });
      worker.onmessage = (event: MessageEvent<GlyphAtlas | { error: string }>) => {
        const msg = event.data;
        finish('data' in msg && msg.data instanceof Uint8Array && msg.data.length === msg.width * msg.height * 4 ? msg : undefined);
      };
      worker.onerror = () => finish();
      worker.postMessage(null);
    } catch {
      finish();
    }
  });
  return atlasJob;
}

function webgl2Supported(): boolean {
  return typeof WebGL2RenderingContext !== 'undefined';
}

function destroy(s: Stage, loseContext: boolean): void {
  s.renderer.onLost = null;
  s.renderer.onRestored = null;
  s.renderer.dispose();
  s.canvas.remove();
  if (loseContext) {
    // Releasing the context waits for queued GPU work in some drivers; never inside a transition frame.
    window.setTimeout(() => { s.canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')?.loseContext(); }, 0);
  }
  if (stage === s) stage = null;
}

async function warm(): Promise<boolean> {
  const ctl = new AbortController();
  abort = ctl;
  const canvas = document.createElement('canvas');
  canvas.className = 'shift-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.width = WARM_W;
  canvas.height = WARM_H;
  let renderer: Renderer | null = null;
  try {
    const atlas = atlasFromWorker();
    // Shader compiles run in parallel with the atlas raster.
    renderer = await Renderer.create(canvas, { signal: ctl.signal, atlasPending: atlas });
    const s: Stage = { canvas, renderer, leased: false };
    renderer.onLost = () => { if (!s.leased) destroy(s, false); };
    stage = s;
    const origin = parseReading(DIVERGENCE.beta)!;
    const target = parseReading(DIVERGENCE.steins_gate)!;
    renderer.resize(WARM_W, WARM_H);
    for (const [t, outcome] of WARM_FRAMES) {
      if (urgent || s.leased || ctl.signal.aborted || renderer.isLost) break;
      await idle(500);
      if (urgent || s.leased || ctl.signal.aborted || renderer.isLost) break;
      renderer.render(evaluateShow(t, { origin, target, outcome, waitT: 6.2, resultT: outcome ? 0 : undefined }));
      renderer.flush();
    }
    if (!s.leased && !renderer.isLost) renderer.trim();
    if (renderer.isLost) { destroy(s, false); return false; }
    failures = 0;
    return true;
  } catch (error) {
    if (renderer) renderer.dispose();
    else if (String(error).includes('WebGL2')) unavailable = true;
    if (!ctl.signal.aborted) failures++;
    canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')?.loseContext();
    return false;
  } finally {
    if (abort === ctl) abort = null;
  }
}

/** Prepare the stage once. `urgent`: a shift is waiting, skip remaining idle work. */
export function prewarmShiftStage(opts: { urgent?: boolean } = {}): Promise<boolean> {
  if (opts.urgent) urgent = true;
  if (stage) return Promise.resolve(true);
  if (warming) return warming;
  if (unavailable || failures >= 2) return Promise.resolve(false);
  if (!opts.urgent && !webgl2Supported()) return Promise.resolve(false);
  warming = warm().finally(() => { warming = null; urgent = false; });
  return warming;
}

/** Idle-time prewarm, skipped where WebGL2 does not exist or motion is reduced. */
export function scheduleShiftPrewarm(delay: number): () => void {
  if (!webgl2Supported()) return () => {};
  const timer = window.setTimeout(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    if (import.meta.env.DEV && new URLSearchParams(window.location.search).get('force-fallback') === '1') return;
    void prewarmShiftStage();
  }, delay);
  return () => window.clearTimeout(timer);
}

export function shiftStageReady(): boolean {
  return !!stage && !stage.leased && !stage.renderer.isLost;
}

export function shiftStageAvailable(): boolean {
  return !unavailable && failures < 2;
}

/** Hand the warm stage to one shift. Null when it is not ready or already leased. */
export function acquireShiftStage(): ShiftStageLease | null {
  const s = stage;
  if (!s || s.leased || s.renderer.isLost) return null;
  s.leased = true;
  let done = false;
  return {
    canvas: s.canvas,
    renderer: s.renderer,
    release() {
      if (done) return;
      done = true;
      s.leased = false;
      s.canvas.remove();
      s.renderer.onRestored = null;
      if (s.renderer.isLost) { destroy(s, false); return; }
      s.renderer.trim();
      s.renderer.onLost = () => { if (!s.leased) destroy(s, false); };
    },
    discard() {
      if (done) return;
      done = true;
      s.leased = false;
      destroy(s, !s.renderer.isLost);
    },
  };
}

export function resetShiftStageForTests(): void {
  abort?.abort();
  if (stage) destroy(stage, false);
  stage = null;
  warming = null;
  abort = null;
  urgent = false;
  failures = 0;
  unavailable = false;
  atlasJob = null;
  worker?.terminate();
  worker = null;
}
