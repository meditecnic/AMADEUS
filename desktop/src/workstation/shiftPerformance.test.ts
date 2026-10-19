import { describe, expect, it } from 'vitest';
import { ShiftPerformance } from './shiftPerformance';

describe('shift performance fallback', () => {
  it('ignores warmup and isolated slow frames, degrades on sustained low fps', () => {
    const monitor = new ShiftPerformance();
    for (let t = 0; t <= 3000; t += 20) expect(monitor.sample(t, true)).toBe(false);
    expect(monitor.sample(3400, true)).toBe(false);
    let degraded = false;
    for (let t = 3500; t <= 5500; t += 100) degraded ||= monitor.sample(t, true);
    expect(degraded).toBe(true);
  });
  it('does not count hidden-tab throttling as a GPU failure', () => {
    const monitor = new ShiftPerformance();
    monitor.sample(0, true);
    monitor.sample(1000, true);
    monitor.sample(10_000, false);
    for (let t = 10_020; t <= 12_000; t += 20) expect(monitor.sample(t, true)).toBe(false);
  });
});
