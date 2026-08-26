import { describe, expect, it } from 'vitest';

import {
  HEALTHY_CAPABILITY,
  ambientMotionEnabled,
  applyContextLoss,
  applyCreateFailure,
  applyPerformanceSample,
  applyRetry3d,
  averageFps,
  reducedMotionChangesSurface,
} from './renderCapability';

describe('renderCapability', () => {
  it('28. context-loss and create-fail still follow renderCapability', () => {
    const created = applyCreateFailure(HEALTHY_CAPABILITY);
    expect(created.surface).toBe('compatibility');
    expect(created.sticky).toBe(true);
    const firstLoss = applyContextLoss(HEALTHY_CAPABILITY);
    expect(firstLoss.surface).toBe('3d');
    expect(firstLoss.retrying).toBe(true);
    const secondLoss = applyContextLoss(firstLoss);
    expect(secondLoss.surface).toBe('compatibility');
    expect(secondLoss.sticky).toBe(true);
  });

  it('retries once on first context loss and sticks on the second', () => {
    const first = applyContextLoss(HEALTHY_CAPABILITY);
    expect(first.surface).toBe('3d');
    expect(first.retrying).toBe(true);
    const second = applyContextLoss(first);
    expect(second.surface).toBe('compatibility');
    expect(second.sticky).toBe(true);
    const failedRetry = applyRetry3d(second, false);
    expect(failedRetry.surface).toBe('compatibility');
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
    expect(fallback.surface).toBe('compatibility');
    expect(averageFps(low, now)).toBeLessThan(40);
    expect(reducedMotionChangesSurface()).toBe(false);
    expect(ambientMotionEnabled(true, '3d')).toBe(false);
    expect(ambientMotionEnabled(false, '3d')).toBe(true);
  });
});
