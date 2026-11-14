import { describe, expect, it } from 'vitest';
import { fitFactor } from './windowFit';

describe('fitFactor', () => {
  it('keeps the designed sizes up to about 1440 × 860, then zooms in 5 % steps up to 200 %', () => {
    expect(fitFactor(1080, 640)).toBe(1);
    expect(fitFactor(1280, 720)).toBe(1);
    expect(fitFactor(1600, 900)).toBe(1);
    expect(fitFactor(1920, 1080)).toBe(1.25);
    expect(fitFactor(1920, 1000)).toBe(1.15); // maximised under a taskbar
    expect(fitFactor(2560, 1440)).toBe(1.65);
    expect(fitFactor(3840, 2160)).toBe(2);
  });
});
