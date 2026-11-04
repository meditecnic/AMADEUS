import { describe, expect, it } from 'vitest';

import {
  FramePerf, MIN_FRAMES, NOTICES, SLOW_FRAME_MS, WARMUP_MS, WINDOW_MS,
  applyContextLoss, applyCreateFailure, applyRetry3d, applySlow, applyUserChoice, decideSurface, isSoftwareRenderer, median, perfSampleVisible,
  type DecisionInput, type FrameSample, type Verdict,
} from './renderCapability';

const GOOD = { webgl2: true, renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)' };
const input = (over: Partial<DecisionInput> = {}): DecisionInput => ({
  choice: null, probe: GOOD, reducedMotion: false, slowUntil: null, now: 1_000_000, ...over,
});

describe('which surface (DIRECTION 16.1)', () => {
  it('uses 3D on a machine with a real GPU and nothing against it', () => {
    const d = decideSurface(input());
    expect(d.surface).toBe('3d');
    expect(d.reason).toBe('healthy');
    expect(d.notice).toBeNull();
  });

  it('1. follows the user\'s own pick, whatever the machine says, and says nothing about it', () => {
    const everythingAgainst = input({
      probe: { webgl2: false, renderer: 'SwiftShader' }, reducedMotion: true, slowUntil: 2_000_000,
    });
    expect(decideSurface({ ...everythingAgainst, choice: '3d' })).toMatchObject({ surface: '3d', reason: 'user', notice: null });
    expect(decideSurface({ ...input(), choice: 'plate' })).toMatchObject({ surface: 'plate', reason: 'user', notice: null });
  });

  it('2. falls back to the plate when WebGL2 cannot be made, and offers another try', () => {
    const d = decideSurface(input({ probe: { webgl2: false, renderer: null } }));
    expect(d).toMatchObject({ surface: 'plate', reason: 'create-fail' });
    expect(d.notice).toMatchObject({ text: '这台电脑现在打不开立体星图，先用图版。', action: '再试一次' });
  });

  it('3. follows "reduce motion" with the plate', () => {
    const d = decideSurface(input({ reducedMotion: true }));
    expect(d).toMatchObject({ surface: 'plate', reason: 'reduced-motion' });
    expect(d.notice).toMatchObject({ text: '系统开了「减少动态」，先用图版。', action: '切到立体' });
  });

  it.each([
    'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)',
    'llvmpipe (LLVM 15.0.7, 256 bits)',
    'Gallium 0.4 on softpipe',
    'ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11)',
  ])('4. falls back to the plate on software rendering: %s', (renderer) => {
    expect(isSoftwareRenderer(renderer)).toBe(true);
    const d = decideSurface(input({ probe: { webgl2: true, renderer } }));
    expect(d).toMatchObject({ surface: 'plate', reason: 'software' });
    expect(d.notice).toMatchObject({ text: '这台电脑现在没有显卡加速，先用图版。', action: '仍然用立体' });
  });

  it('does not take a real GPU for software', () => {
    expect(isSoftwareRenderer(GOOD.renderer)).toBe(false);
    expect(isSoftwareRenderer('ANGLE (AMD, AMD Radeon(TM) 780M Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)')).toBe(false);
    expect(isSoftwareRenderer(null)).toBe(false);
  });

  it('5. goes straight to the plate for a machine found slow within the week, and not after it', () => {
    const d = decideSurface(input({ slowUntil: 1_000_001 }));
    expect(d).toMatchObject({ surface: 'plate', reason: 'slow' });
    expect(d.notice).toMatchObject({ text: '这台电脑跑立体星图偏慢，已换成图版。', action: '切回立体' });
    expect(decideSurface(input({ slowUntil: 1_000_000 })).surface).toBe('3d');
    expect(decideSurface(input({ slowUntil: 999_999 })).surface).toBe('3d');
  });

  it('applies the rules in order', () => {
    const software = { webgl2: true, renderer: 'llvmpipe' };
    expect(decideSurface(input({ probe: { webgl2: false, renderer: null }, reducedMotion: true })).reason).toBe('create-fail');
    expect(decideSurface(input({ probe: software, reducedMotion: true })).reason).toBe('reduced-motion');
    expect(decideSurface(input({ probe: software, slowUntil: 2_000_000 })).reason).toBe('software');
  });
});

describe('failures while it runs', () => {
  const healthy = decideSurface(input());

  it('rebuilds a lost context once and gives up on the second loss', () => {
    const first = applyContextLoss(healthy);
    expect(first).toMatchObject({ surface: '3d', retrying: true, contextLosses: 1 });
    const second = applyContextLoss({ ...first, retrying: false });
    expect(second).toMatchObject({ surface: 'plate', reason: 'context-loss', retrying: false, contextLosses: 2 });
    expect(second.notice?.action).toBe('再试一次');
  });

  it('says it still cannot when a retry fails again', () => {
    const failed = applyCreateFailure(healthy);
    expect(failed.notice?.text).toBe(NOTICES['create-fail'].text);
    const again = applyCreateFailure(applyRetry3d());
    expect(again.surface).toBe('plate');
    expect(again.notice).toMatchObject({ text: '还是打不开，继续用图版。', action: null });
  });

  it('switches to the plate with the slow notice', () => {
    expect(applySlow(healthy)).toMatchObject({ surface: 'plate', reason: 'slow' });
  });

  it('a pick of their own clears the notice and the loss count', () => {
    const lost = applyContextLoss(applyContextLoss(healthy));
    expect(applyUserChoice('3d')).toMatchObject({ surface: '3d', reason: 'user', notice: null, contextLosses: 0 });
    expect(lost.surface).toBe('plate');
  });
});

/** Frames `ms` apart from `from` for `seconds`, all drawn. */
function run(perf: FramePerf, from: number, seconds: number, ms: number, over: Partial<FrameSample> = {}): { verdicts: Verdict[]; end: number } {
  const verdicts: Verdict[] = [];
  let at = from;
  const stop = from + seconds * 1000;
  while (at < stop) {
    at += ms;
    verdicts.push(perf.push({ at, ms, visible: true, focused: true, ...over }));
  }
  return { verdicts, end: at };
}
const firstVerdict = (verdicts: Verdict[]) => verdicts.find((v) => v !== null) ?? null;

describe('timing the frames (DIRECTION 16.1, step 5)', () => {
  it('fixes the numbers of the spec', () => {
    expect([WARMUP_MS, WINDOW_MS, MIN_FRAMES, SLOW_FRAME_MS]).toEqual([1500, 5000, 20, 25]);
  });

  it('a steady 60 fps machine is fine after one window, and then stops being timed', () => {
    const perf = new FramePerf(0);
    const { verdicts, end } = run(perf, 0, 8, 16.7);
    expect(firstVerdict(verdicts)).toBe('ok');
    expect(perf.finished).toBe(true);
    expect(firstVerdict(run(perf, end, 12, 60).verdicts)).toBeNull();
  });

  it('a slow machine lowers the pixel ratio first, then goes to the plate on the next slow window', () => {
    const perf = new FramePerf(0);
    const out = run(perf, 0, 14, 40).verdicts.filter((v) => v !== null);
    expect(out).toEqual(['lower-tier', 'to-plate']);
    expect(perf.finished).toBe(true);
  });

  it('lowering the pixel ratio can be enough: a fast second window ends the timing', () => {
    const perf = new FramePerf(0);
    const first = run(perf, 0, 7.5, 40);
    expect(firstVerdict(first.verdicts)).toBe('lower-tier');
    const second = run(perf, first.end, 6, 16);
    expect(second.verdicts.filter((v) => v !== null)).toEqual(['ok']);
  });

  it('does not count the first 1.5 seconds', () => {
    const perf = new FramePerf(0);
    // 1.4 s of terrible frames, then nothing for a while: no window has even started.
    const early = run(perf, 0, 1.4, 100);
    expect(early.verdicts.every((v) => v === null)).toBe(true);
    // and they do not leak into the window that follows
    const later = run(perf, 1500, 6, 16);
    expect(firstVerdict(later.verdicts)).toBe('ok');
  });

  it('does not conclude from a window with too few frames', () => {
    const perf = new FramePerf(0);
    // 15 frames, 400 ms apart, over 6 s: slow, but not enough of them.
    const sparse = run(perf, 1500, 6, 400);
    expect(sparse.verdicts.filter((v) => v !== null)).toEqual([]);
    expect(perf.finished).toBe(false);
  });

  it('does not conclude from a window that covers less than 3.5 s of frames', () => {
    const perf = new FramePerf(0);
    // 40 frames of 1 ms each fill the 20-frame bar but only 40 ms of counted time ... spread across a 5 s wall window
    let at = 1500;
    const verdicts: Verdict[] = [];
    for (let i = 0; i < 40; i += 1) { at += 130; verdicts.push(perf.push({ at, ms: 1, visible: true, focused: true })); }
    expect(verdicts.filter((v) => v !== null)).toEqual([]);
    expect(perf.finished).toBe(false);
  });

  it('judges by the median: a few stalls in a smooth run do not demote it', () => {
    const perf = new FramePerf(0);
    let at = 1500;
    const verdicts: Verdict[] = [];
    // 190 frames, every 31st a 600 ms stall: the mean is about 28 ms (over the line), the median 16 ms
    const gaps: number[] = [];
    for (let i = 0; i < 150; i += 1) gaps.push(i % 31 === 30 ? 600 : 16);
    for (let i = 0; i < 40; i += 1) gaps.push(16);
    for (const ms of gaps) {
      at += ms;
      verdicts.push(perf.push({ at, ms, visible: true, focused: true }));
    }
    expect(gaps.reduce((a, b) => a + b, 0) / gaps.length).toBeGreaterThan(SLOW_FRAME_MS);
    expect(median(gaps)).toBe(16);
    expect(firstVerdict(verdicts)).toBe('ok');
  });

  it('does not count frames while the page is hidden or has lost focus', () => {
    const hidden = new FramePerf(0);
    expect(run(hidden, 1500, 12, 60, { visible: false }).verdicts.every((v) => v === null)).toBe(true);
    const blurred = new FramePerf(0);
    expect(run(blurred, 1500, 12, 60, { focused: false }).verdicts.every((v) => v === null)).toBe(true);
    // and they cannot hide in a window that is otherwise fine
    const perf = new FramePerf(0);
    run(perf, 1500, 3, 60, { visible: false });
    expect(firstVerdict(run(perf, 4500, 6, 16).verdicts)).toBe('ok');
  });

  it('restarts the window after a pause (a gap is not a frame time)', () => {
    const perf = new FramePerf(0);
    const a = run(perf, 1500, 3, 16);
    expect(a.verdicts.every((v) => v === null)).toBe(true);
    expect(perf.push({ at: a.end + 30_000, ms: 30_000, visible: true, focused: true })).toBeNull();
    // the earlier 3 s are gone: this window needs its own 5 s
    const b = run(perf, a.end + 30_000, 3, 16);
    expect(b.verdicts.every((v) => v === null)).toBe(true);
    expect(firstVerdict(run(perf, b.end, 3, 16).verdicts)).toBe('ok');
  });

  it('reads visibility and focus together', () => {
    expect(perfSampleVisible('visible', true)).toBe(true);
    expect(perfSampleVisible('visible', false)).toBe(false);
    expect(perfSampleVisible('hidden', true)).toBe(false);
  });
});
