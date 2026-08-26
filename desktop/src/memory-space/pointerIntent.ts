/** Ordinary pointer jitter must remain a click, not a drag. Exact px is a hypothesis. */
export const CLICK_SLOP_PX = 8;

export function isPointerDrag(dx: number, dy: number, slop = CLICK_SLOP_PX): boolean {
  return Math.abs(dx) + Math.abs(dy) > slop;
}
