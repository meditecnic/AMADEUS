import { describe, expect, it } from 'vitest';

import {
  HEALTHY_CAPABILITY,
  ambientMotionEnabled,
  applyContextLoss,
  applyCreateFailure,
  applyFrameSample,
  applyPerformanceSample,
  applyRetry3d,
  averageFps,
  perfSampleVisible,
  reducedMotionChangesSurface,
  type CapabilityState,
  type FrameAccumulation,
  type FrameSample,
} from './renderCapability';

describe('renderCapability', () => {
  it('routes unavailable 3D to the semantic LIST instead of a second graph', () => {
    const fallback = applyCreateFailure(HEALTHY_CAPABILITY);
    expect(fallback.surface).toBe('list');
    expect(fallback.reason).toBe('create-fail');
    expect(applyRetry3d(fallback, true).surface).toBe('3d');
    expect(applyRetry3d(fallback, false).surface).toBe('list');
  });

  it('28. context-loss and create-fail still follow renderCapability', () => {
    const created = applyCreateFailure(HEALTHY_CAPABILITY);
    expect(created.surface).toBe('list');
    expect(created.sticky).toBe(true);
    const firstLoss = applyContextLoss(HEALTHY_CAPABILITY);
    expect(firstLoss.surface).toBe('3d');
    expect(firstLoss.retrying).toBe(true);
    const secondLoss = applyContextLoss(firstLoss);
    expect(secondLoss.surface).toBe('list');
    expect(secondLoss.sticky).toBe(true);
  });

  it('retries once on first context loss and sticks on the second', () => {
    const first = applyContextLoss(HEALTHY_CAPABILITY);
    expect(first.surface).toBe('3d');
    expect(first.retrying).toBe(true);
    const second = applyContextLoss(first);
    expect(second.surface).toBe('list');
    expect(second.sticky).toBe(true);
    const failedRetry = applyRetry3d(second, false);
    expect(failedRetry.surface).toBe('list');
  });

  it('reduces decoration before falling under a 5s visible window', () => {
    const now = 20_000;
    const low = Array.from({ length: 21 }, (_, index) => ({
      at: now - 5000 + index * 250,
      visible: true,
      warmup: false,
    }));
    const reduced = applyPerformanceSample(HEALTHY_CAPABILITY, low, now);
    expect(reduced.decoration).toBe('reduced');
    expect(reduced.surface).toBe('3d');
    const stillMeasuring = applyPerformanceSample(reduced, low, now);
    expect(stillMeasuring.surface).toBe('3d');
    const later = now + 5000;
    const laterWindow = Array.from({ length: 21 }, (_, index) => ({
      at: later - 5000 + index * 250,
      visible: true,
      warmup: false,
    }));
    const fallback = applyPerformanceSample(reduced, laterWindow, later);
    expect(fallback.surface).toBe('list');
    expect(averageFps(low, now)).toBeLessThan(40);
    expect(reducedMotionChangesSurface()).toBe(false);
    expect(ambientMotionEnabled(true, '3d')).toBe(false);
    expect(ambientMotionEnabled(false, '3d')).toBe(true);
  });
});

function samples(spec: Array<[at: number, visible: boolean, warmup?: boolean]>): FrameSample[] {
  return spec.map(([at, visible, warmup]) => ({ at, visible, warmup: warmup ?? false }));
}

describe('perfSampleVisible (P1-B seam)', () => {
  it('counts only visible AND focused time', () => {
    expect(perfSampleVisible('visible', true)).toBe(true);
    expect(perfSampleVisible('visible', false)).toBe(false);
    expect(perfSampleVisible('hidden', true)).toBe(false);
    expect(perfSampleVisible('hidden', false)).toBe(false);
  });
});

describe('averageFps under throttling (P1-B)', () => {
  it('ignores hidden/unfocused samples entirely', () => {
    const window = samples([
      [0, true],
      [16, true],
      [32, true],
      // Throttled region: ~1fps and marked invisible.
      [1032, false],
      [2032, false],
      [3032, false],
    ]);
    // At t=3032 the only in-window visible samples are the first three;
    // elapsed 32ms over 2 intervals -> healthy 62.5fps, never 1fps.
    expect(averageFps(window, 3032)).toBeCloseTo(62.5, 1);
  });

  it('returns null when the window holds fewer than two visible samples', () => {
    const window = samples([
      [0, true],
      [1000, false],
      [2000, false],
    ]);
    expect(averageFps(window, 2000)).toBeNull();
  });
});

describe('applyPerformanceSample honesty (P1-B)', () => {
  // decorationReducedAt sits beyond the 5s grace so the fps branch decides.
  const reduced: CapabilityState = { ...HEALTHY_CAPABILITY, decoration: 'reduced', decorationReducedAt: -6000 };

  it('does not demote when only throttled samples exist in the window', () => {
    const window = samples([
      [0, true],
      [16, true],
      [1016, false],
      [2016, false],
    ]);
    const next = applyPerformanceSample(reduced, window, 2016);
    expect(next).toBe(reduced);
    expect(next.surface).toBe('3d');
  });

  it('still demotes on genuine foreground low fps', () => {
    const window = samples([
      [0, true],
      [50, true],
      [100, true],
      [150, true],
    ]);
    const next = applyPerformanceSample(reduced, window, 150);
    expect(next.surface).toBe('list');
    expect(next.reason).toBe('low-fps');
    expect(next.sticky).toBe(true);
  });

  it('keeps warmup samples out of the baseline after a visibility reset', () => {
    const window = samples([
      [0, true],
      [16, true],
      [32, true],
      [1032, false],
      // After refocus the stage restarts warmup; these must not judge yet.
      [2000, true, true],
      [2016, true, true],
    ]);
    const next = applyPerformanceSample(reduced, window, 2016);
    expect(next).toBe(reduced);
  });
});

describe('visibility recovery boundary (P1 round 5)', () => {
  it('clearing the full sampling window prevents stale low-fps demotion', () => {
    const base: CapabilityState = {
      ...HEALTHY_CAPABILITY,
      decoration: 'reduced',
      decorationReducedAt: -6000,
    };
    // Pre-recovery: genuine foreground low-fps visible samples (~20fps).
    const stale = samples(
      Array.from({ length: 12 }, (_, index) => [index * 50, true] as [number, boolean]),
    );
    // Hazard control: judging on the stale window alone demotes immediately.
    expect(applyPerformanceSample(base, stale, stale[stale.length - 1].at).surface).toBe('list');
    // Recovery boundary: the full sampling window is cleared; only warmup
    // samples exist afterwards. They must not demote.
    let acc: FrameAccumulation = { state: base, buffer: [] };
    acc = applyFrameSample(acc.state, acc.buffer, { at: 5000, visible: true, warmup: true });
    acc = applyFrameSample(acc.state, acc.buffer, { at: 5016, visible: true, warmup: true });
    expect(acc.state.surface).toBe('3d');
    expect(acc.state).toBe(base);
  });

  it('retains the rolling buffer across ordinary frames', () => {
    let acc: FrameAccumulation = { state: HEALTHY_CAPABILITY, buffer: [] };
    for (let index = 0; index < 8; index += 1) {
      acc = applyFrameSample(acc.state, acc.buffer, { at: index * 16, visible: true, warmup: false });
    }
    expect(acc.buffer).toHaveLength(8);
    expect(acc.state.surface).toBe('3d');
  });
});
