// When the star chart is drawn as the 3D light cone and when as the SVG plate (DIRECTION 16.1). Pure rules:
// the probe, the stored preferences and the frame timings come in as arguments, so each step is testable.

export type Surface = '3d' | 'plate';
/** What the user picked with the "立体 | 图版" switch; `null` if they never did. */
export type DisplayChoice = Surface | null;

export type FallbackReason = 'create-fail' | 'software' | 'reduced-motion' | 'slow' | 'context-loss';

/** What the browser can do, found once per page: a WebGL2 context and the renderer name behind it. */
export type GpuProbe = { webgl2: boolean; renderer: string | null };

export type NoticeText = {
  text: string;
  /** The button beside it; `null` when there is nothing to try. */
  action: string | null;
};

export const NOTICES: Record<FallbackReason | 'create-fail-again', NoticeText> = {
  'create-fail': { text: '这台电脑现在打不开立体星图，先用图版。', action: '再试一次' },
  'create-fail-again': { text: '还是打不开，继续用图版。', action: null },
  'context-loss': { text: '这台电脑现在打不开立体星图，先用图版。', action: '再试一次' },
  software: { text: '这台电脑现在没有显卡加速，先用图版。', action: '仍然用立体' },
  slow: { text: '这台电脑跑立体星图偏慢，已换成图版。', action: '切回立体' },
  'reduced-motion': { text: '系统开了「减少动态」，先用图版。', action: '切到立体' },
};

export type CapabilityState = {
  surface: Surface;
  /** `user`: the user's own pick, never explained. `healthy`: 3D, being timed. */
  reason: FallbackReason | 'user' | 'healthy';
  /** What to tell the user about a fallback; `null` for a pick of their own. */
  notice: (NoticeText & { kind: keyof typeof NOTICES }) | null;
  /** WebGL contexts lost so far in this visit. */
  contextLosses: number;
  /** A 3D rebuild is under way after a lost context. */
  retrying: boolean;
  /** The 3D failed to build once and the user asked again. */
  retried: boolean;
};

const noticeOf = (kind: keyof typeof NOTICES): CapabilityState['notice'] => ({ kind, ...NOTICES[kind] });

export const SOFTWARE_RENDERERS = /swiftshader|llvmpipe|softpipe|microsoft basic render/i;
export const isSoftwareRenderer = (name: string | null) => Boolean(name && SOFTWARE_RENDERERS.test(name));

export type DecisionInput = {
  choice: DisplayChoice;
  probe: GpuProbe;
  reducedMotion: boolean;
  /** Expiry of the stored "this machine is slow" note, or `null`. */
  slowUntil: number | null;
  now: number;
};

/** The order of DIRECTION 16.1; the first rule that applies wins. */
export function decideSurface(input: DecisionInput): CapabilityState {
  const base = { contextLosses: 0, retrying: false, retried: false };
  // 1. The user's own pick, whatever the machine says.
  if (input.choice) return { ...base, surface: input.choice, reason: 'user', notice: null };
  // 2. No WebGL2.
  if (!input.probe.webgl2) return { ...base, surface: 'plate', reason: 'create-fail', notice: noticeOf('create-fail') };
  // 3. The system asks for less motion.
  if (input.reducedMotion) return { ...base, surface: 'plate', reason: 'reduced-motion', notice: noticeOf('reduced-motion') };
  // 4. Software rendering: failIfMajorPerformanceCaveat lets SwiftShader through, so the renderer's name decides.
  if (isSoftwareRenderer(input.probe.renderer)) return { ...base, surface: 'plate', reason: 'software', notice: noticeOf('software') };
  // 5a. This machine was found slow within the last week.
  if (input.slowUntil !== null && input.slowUntil > input.now) return { ...base, surface: 'plate', reason: 'slow', notice: noticeOf('slow') };
  // 5b. Otherwise 3D, and the frames are timed (FramePerf).
  return { ...base, surface: '3d', reason: 'healthy', notice: null };
}

/** The 3D context could not be made at all. A second failure after the user asked again says so. */
export function applyCreateFailure(state: CapabilityState): CapabilityState {
  return {
    ...state, surface: 'plate', retrying: false, reason: 'create-fail',
    notice: noticeOf(state.retried ? 'create-fail-again' : 'create-fail'),
  };
}

/** A lost context is rebuilt once; the second loss falls back to the plate. */
export function applyContextLoss(state: CapabilityState): CapabilityState {
  const losses = state.contextLosses + 1;
  if (losses === 1) return { ...state, contextLosses: losses, retrying: true, surface: '3d' };
  return { ...state, contextLosses: losses, retrying: false, surface: 'plate', reason: 'context-loss', notice: noticeOf('context-loss') };
}

/** The user pressed the button on a notice or the switch: 3D again, with a clean slate. */
export function applyUserChoice(choice: Surface): CapabilityState {
  return { surface: choice, reason: 'user', notice: null, contextLosses: 0, retrying: false, retried: false };
}

/** "再试一次" after a failure to build. */
export function applyRetry3d(): CapabilityState {
  return { ...applyUserChoice('3d'), retried: true };
}

/** Measured slow in two windows in a row. */
export function applySlow(state: CapabilityState): CapabilityState {
  return { ...state, surface: 'plate', reason: 'slow', retrying: false, notice: noticeOf('slow') };
}

export const WARMUP_MS = 1500;
export const WINDOW_MS = 5000;
export const MIN_SPAN_MS = 3500;
export const MIN_FRAMES = 20;
export const SLOW_FRAME_MS = 25;
/** A longer gap between two drawn frames is a pause (hidden tab, a stall), not a frame time. */
export const MAX_FRAME_MS = 1000;

export type FrameSample = {
  /** rAF timestamp of the frame. */
  at: number;
  /** Time since the previous frame, which was also drawn. */
  ms: number;
  visible: boolean;
  focused: boolean;
};

export type Verdict = 'ok' | 'lower-tier' | 'to-plate' | null;

export function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
}

/**
 * Times the frames that were really drawn (a still scene draws none). The first 1.5 s are shader and font warm-up;
 * a hidden or unfocused page does not count (rAF is throttled there). A window is 5 s; it concludes only with at
 * least 3.5 s of counted frames and 20 of them, and it looks at the median so one stall does not decide. A slow
 * window first lowers the pixel ratio; a second slow window switches to the plate. After that it stops timing.
 */
export class FramePerf {
  private windowStart: number | null = null;
  private frames: number[] = [];
  private span = 0;
  private stage: 'first' | 'lowered' | 'done' = 'first';

  constructor(private readonly startedAt: number) {}

  get finished() { return this.stage === 'done'; }

  private reset() { this.windowStart = null; this.frames = []; this.span = 0; }

  push(sample: FrameSample): Verdict {
    if (this.stage === 'done') return null;
    if (!sample.visible || !sample.focused || sample.ms > MAX_FRAME_MS) { this.reset(); return null; }
    if (sample.at - this.startedAt < WARMUP_MS) return null;
    if (this.windowStart === null) this.windowStart = sample.at;
    this.frames.push(sample.ms);
    this.span += sample.ms;
    if (sample.at - this.windowStart < WINDOW_MS) return null;
    const enough = this.span >= MIN_SPAN_MS && this.frames.length >= MIN_FRAMES;
    const slow = enough && median(this.frames) > SLOW_FRAME_MS;
    this.reset();
    if (!enough) return null;
    if (!slow) { this.stage = 'done'; return 'ok'; }
    if (this.stage === 'first') { this.stage = 'lowered'; return 'lower-tier'; }
    this.stage = 'done';
    return 'to-plate';
  }
}

/** rAF is throttled when the page is hidden or unfocused; neither state counts toward the timing. */
export function perfSampleVisible(visibilityState: string, hasFocus: boolean): boolean {
  return visibilityState === 'visible' && hasFocus;
}

/** The star chart's ambient turn needs the 3D surface and a system that does not ask for less motion. */
export function ambientMotionEnabled(reducedMotion: boolean, surface: Surface): boolean {
  return surface === '3d' && !reducedMotion;
}
