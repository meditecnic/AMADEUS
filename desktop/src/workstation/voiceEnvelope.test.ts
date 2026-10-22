import { describe, expect, it } from 'vitest';
import { mouthFrame } from './voiceEnvelope';

describe('mouthFrame', () => {
  it('opens at the same levels as before (0.05 half, 0.25 open)', () => {
    expect(mouthFrame(0.04, 1)).toBe(1);
    expect(mouthFrame(0.06, 1)).toBe(2);
    expect(mouthFrame(0.26, 1)).toBe(3);
    expect(mouthFrame(0.26, 2)).toBe(3);
  });

  it('holds a shape until the level has clearly left it, so it does not chatter at a threshold', () => {
    // Hovering just under an opening threshold keeps the current frame.
    expect(mouthFrame(0.22, 3)).toBe(3);
    expect(mouthFrame(0.045, 2)).toBe(2);
    // Falling well below closes in steps.
    expect(mouthFrame(0.15, 3)).toBe(2);
    expect(mouthFrame(0.03, 2)).toBe(1);
    expect(mouthFrame(0.02, 3)).toBe(1);
    // A level between the two thresholds that has not opened yet stays closed.
    expect(mouthFrame(0.045, 1)).toBe(1);
  });
});
