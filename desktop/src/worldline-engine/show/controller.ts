// Playback state machine. One run at a time, one requestAnimationFrame at a time.
// Locking to the target is only ever driven by a resolved outcome.
import type { Renderer } from '../engine/renderer';
import type { FrameState } from './frame';
import {
  T,
  evaluateShow,
  evaluateReduced,
  showEnd,
  reducedEnd,
  standbyFrame,
  successEndFrame,
  mapWallTime,
  MOCK_DELAY,
  MOCK_OUTCOME,
  formatCells,
} from './timeline';
import type { Scenario, Outcome, ShowContext } from './timeline';
import { mockSwitch } from './mock';

export type Phase = 'idle' | 'running' | 'ended' | 'harness';

export interface RunOptions {
  /** Demo fallback only; ignored when the host supplies outcome. */
  scenario: Scenario;
  reduced: boolean;
  origin: number[];
  target: number[];
  /** Host-owned request result. Rejection means failure; teardown never cancels the request. */
  outcome?: Promise<Outcome>;
}

interface Run extends Omit<RunOptions, 'outcome'> {
  id: number;
  showT: number;
  waitT: number;
  rmT: number;
  outcome: Outcome | null;
  resultAtRm: number;
  resultT: number;
  paused: boolean;
  skipRequested: boolean;
  abort: AbortController;
}

export interface ControllerEvents {
  onChange: (s: ControllerSnapshot) => void;
}

export interface ControllerSnapshot {
  phase: Phase;
  runId: number;
  scenario: Scenario | null;
  reduced: boolean;
  paused: boolean;
  skipRequested: boolean;
  outcome: Outcome | null;
  waiting: boolean;
  showT: number;
  waitT: number;
  shot: string;
  shown: string;
  origin: string;
  target: string;
}

export class Controller {
  private r: Pick<Renderer, 'render' | 'finish'>;
  private ev: ControllerEvents;
  private run: Run | null = null;
  private phase: Phase = 'idle';
  private raf = 0;
  private last = 0;
  private nextId = 1;
  private idleOrigin: number[];
  private lastFrame: FrameState | null = null;
  /** diagnostics for QA: number of rAF callbacks currently scheduled (0 or 1) */
  ticks = 0;

  constructor(r: Pick<Renderer, 'render' | 'finish'>, origin: number[], ev: ControllerEvents) {
    this.r = r;
    this.ev = ev;
    this.idleOrigin = origin;
  }

  get rafScheduled(): number {
    return this.raf !== 0 ? 1 : 0;
  }

  snapshot(): ControllerSnapshot {
    const run = this.run;
    const f = this.lastFrame;
    return {
      phase: this.phase,
      runId: run?.id ?? 0,
      scenario: run?.scenario ?? null,
      reduced: run?.reduced ?? false,
      paused: run?.paused ?? false,
      skipRequested: run?.skipRequested ?? false,
      outcome: run?.outcome ?? null,
      waiting: !!run && this.phase === 'running' && run.outcome === null && (run.reduced || run.showT >= T.GATE),
      showT: run?.showT ?? 0,
      waitT: run?.waitT ?? 0,
      shot: f?.shot ?? '',
      shown: f?.readout.shown ?? '',
      origin: formatCells(run?.origin ?? this.idleOrigin),
      target: run ? formatCells(run.target) : '',
    };
  }

  private emit(): void {
    this.ev.onChange(this.snapshot());
  }

  // ------------------------------------------------------------ commands

  start(opts: RunOptions): void {
    this.cancelRun();
    const run: Run = {
      ...opts,
      id: this.nextId++,
      showT: 0,
      waitT: 0,
      rmT: 0,
      outcome: null,
      resultAtRm: 0,
      resultT: 0,
      paused: false,
      skipRequested: false,
      abort: new AbortController(),
    };
    this.run = run;
    this.phase = 'running';
    const id = run.id;
    if (opts.outcome !== undefined) {
      opts.outcome.then(
        (outcome) => this.onResult(id, outcome),
        () => this.onResult(id, 'fail'),
      );
    } else {
      mockSwitch(run.scenario, formatCells(run.origin), formatCells(run.target), run.abort.signal).then(
        (res) => this.onResult(id, res.outcome),
        () => {
          /* aborted: a newer run or an exit superseded this one */
        },
      );
    }
    this.last = performance.now();
    this.draw();
    this.ensureLoop();
    this.emit();
  }

  private onResult(id: number, outcome: Outcome): void {
    const run = this.run;
    if (!run || run.id !== id || this.phase !== 'running') return;
    run.outcome = outcome;
    run.resultAtRm = run.rmT;
    run.resultT = run.showT;
    if (run.skipRequested) {
      this.finish();
      return;
    }
    this.last = performance.now();
    this.ensureLoop();
    this.emit();
  }

  togglePause(): void {
    const run = this.run;
    if (!run || this.phase !== 'running') return;
    run.paused = !run.paused;
    if (!run.paused) {
      this.last = performance.now();
      this.ensureLoop();
    } else {
      this.stopLoop();
      this.draw();
    }
    this.emit();
  }

  /** Skip the montage. Never fabricates a result: without one it waits at the gate. */
  skip(): void {
    const run = this.run;
    if (!run || this.phase !== 'running') return;
    run.skipRequested = true;
    if (run.outcome !== null) {
      this.finish();
      return;
    }
    if (!run.reduced && run.showT < T.GATE) run.showT = T.GATE;
    run.paused = false;
    this.last = performance.now();
    this.draw();
    this.ensureLoop();
    this.emit();
  }

  /** Esc: leave the show entirely and return to standby. */
  exit(): void {
    if (this.phase === 'idle') return;
    this.cancelRun();
    this.phase = 'idle';
    this.draw();
    this.emit();
  }

  setIdleOrigin(origin: number[]): void {
    this.idleOrigin = origin;
    if (this.phase === 'idle') this.draw();
  }

  private cancelRun(): void {
    if (this.run) {
      this.run.abort.abort();
      this.run = null;
    }
    this.stopLoop();
  }

  private finish(): void {
    this.phase = 'ended';
    this.stopLoop();
    this.draw();
    this.emit();
  }

  // ------------------------------------------------------------ loop

  private ensureLoop(): void {
    if (this.raf === 0 && this.phase === 'running' && !this.run?.paused) {
      this.raf = requestAnimationFrame(this.tick);
    }
  }

  private stopLoop(): void {
    if (this.raf !== 0) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private tick = (now: number): void => {
    this.raf = 0;
    this.ticks++;
    const run = this.run;
    if (!run || this.phase !== 'running' || run.paused) return;
    const dt = Math.min(Math.max((now - this.last) / 1000, 0), 0.1);
    this.last = now;
    const wasWaiting = run.outcome === null && run.showT >= T.GATE;
    if (run.reduced) {
      run.rmT += dt;
      if (run.outcome && run.rmT >= reducedEnd(run.outcome, run.resultAtRm)) {
        this.finish();
        return;
      }
    } else {
      let next = run.showT + dt;
      if (run.outcome === null && next >= T.GATE) {
        run.waitT += next - Math.max(run.showT, T.GATE);
        next = T.GATE;
      }
      run.showT = next;
      if (run.outcome && run.showT >= showEnd(run.outcome)) {
        this.finish();
        return;
      }
    }
    this.draw();
    const isWaiting = run.outcome === null && run.showT >= T.GATE;
    if (isWaiting !== wasWaiting) this.emit();
    this.ensureLoop();
  };

  /** Re-render the current state (after resize, or once when static). */
  draw(): void {
    const f = this.currentFrame();
    this.lastFrame = f;
    this.r.render(f);
  }

  private ctx(run: Run): ShowContext {
    return { origin: run.origin, target: run.target, outcome: run.outcome, waitT: run.waitT, resultT: run.resultT };
  }

  private currentFrame(): FrameState {
    const run = this.run;
    if (this.phase === 'harness' && this.lastFrame) return this.lastFrame;
    if (!run || this.phase === 'idle') return standbyFrame(this.idleOrigin, false);
    if (this.phase === 'ended') {
      return run.outcome === 'success' ? successEndFrame(run.target) : standbyFrame(run.origin, true);
    }
    if (run.reduced) return evaluateReduced(run.rmT, run.outcome, run.resultAtRm, this.ctx(run));
    return evaluateShow(run.showT, this.ctx(run));
  }

  // ------------------------------------------------------------ deterministic test hook

  /** Render the frame a scenario shows `wall` seconds after start (no pauses). Test-only. */
  harnessSeek(wall: number, scenario: Scenario, opts: { reduced?: boolean; origin: number[]; target: number[] }): FrameState {
    this.cancelRun();
    this.phase = 'harness';
    const ctx: ShowContext = { origin: opts.origin, target: opts.target, outcome: null, waitT: 0 };
    let f: FrameState;
    if (wall < 0) {
      f = standbyFrame(opts.origin, false);
    } else if (opts.reduced) {
      const at = MOCK_DELAY[scenario];
      const out = MOCK_OUTCOME[scenario];
      const known = wall >= at;
      if (known && wall >= reducedEnd(out, at)) f = out === 'success' ? successEndFrame(opts.target) : standbyFrame(opts.origin, true);
      else f = evaluateReduced(wall, known ? out : null, at, ctx);
    } else {
      const m = mapWallTime(wall, scenario);
      ctx.outcome = m.outcome;
      ctx.waitT = m.waitT;
      ctx.resultT = m.resultT;
      if (m.ended && m.outcome) f = m.outcome === 'success' ? successEndFrame(opts.target) : standbyFrame(opts.origin, true);
      else f = evaluateShow(m.showT, ctx);
    }
    this.lastFrame = f;
    this.r.render(f);
    this.r.finish();
    this.emit();
    return f;
  }

  dispose(): void {
    this.cancelRun();
  }
}
