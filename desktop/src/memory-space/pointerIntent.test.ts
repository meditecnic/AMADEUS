import { describe, expect, it } from 'vitest';

import { CLICK_SLOP_PX, isPointerDrag } from './pointerIntent';

describe('pointer click vs drag', () => {
  it('treats slight jitter inside the slop as a click', () => {
    expect(CLICK_SLOP_PX).toBeGreaterThan(2);
    expect(isPointerDrag(3, 2)).toBe(false);
    expect(isPointerDrag(1, 0)).toBe(false);
  });

  it('treats a real stroke beyond the slop as a drag', () => {
    expect(isPointerDrag(20, 4)).toBe(true);
  });
});
