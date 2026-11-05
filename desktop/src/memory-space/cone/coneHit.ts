// Picking in the cone. With 2000 records the pointer must not be tested against every star on every move
// ([187]: the slowest hover frame was 34-38 ms for exactly that): only what is drawn as a star can be hit,
// plus one screen-space segment per ribbon cell (about 500 of them).
import type { ChartModel } from '../chartCoordinates';
import { bandSpan } from '../ribbonGeometry';
import type { ConeFrame, Screen, Vec3 } from './coneFrame';

export type DrawnView = {
  /** The group that is open and fully expanded (the stars of a collapsed group exist only then). */
  openGroup: string | null;
  marks: readonly string[];
  selectedId: string | null;
};

/** Ids of the stars that are drawn as stars right now, in model order. Everything else is a tick on a ribbon. */
export function drawnStarIds(model: Pick<ChartModel, 'stars' | 'collapsed' | 'byId'>, view: DrawnView): string[] {
  if (!model.collapsed) return model.stars.map((star) => star.id);
  const keep = new Set(view.marks);
  if (view.selectedId) keep.add(view.selectedId);
  return model.stars
    .filter((star) => star.single || keep.has(star.id) || (view.openGroup !== null && star.group === view.openGroup))
    .map((star) => star.id);
}

/** `a`–`b` is the drawn ribbon (inset from the month); `e0`–`e1` is the whole month, edge to edge, on the same line. */
export type CellSegment = {
  key: string; group: string; ax: number; ay: number; bx: number; by: number; ex0: number; ey0: number; ex1: number; ey1: number;
};

/** Each cell's ribbon centre line on screen. The open group's ribbons have turned into stars and are left out. */
export function cellSegments(
  model: Pick<ChartModel, 'groups' | 'collapsed'>, frame: ConeFrame, project: (p: Vec3) => Screen, openGroup: string | null,
): CellSegment[] {
  if (!model.collapsed) return [];
  const out: CellSegment[] = [];
  for (const group of model.groups) {
    if (group.id === openGroup) continue;
    for (const cell of group.cells) {
      if (!cell.collapsedIds.length) continue;
      const [lo, hi] = bandSpan(cell.band);
      const a = project(frame.pos(lo, group.phi));
      const b = project(frame.pos(hi, group.phi));
      const e0 = project(frame.pos(cell.band.theta0, group.phi));
      const e1 = project(frame.pos(cell.band.theta1, group.phi));
      out.push({ key: cell.key, group: group.id, ax: a.x, ay: a.y, bx: b.x, by: b.y, ex0: e0.x, ey0: e0.y, ex1: e1.x, ey1: e1.y });
    }
  }
  return out;
}

export function segmentDistance(px: number, py: number, s: Pick<CellSegment, 'ax' | 'ay' | 'bx' | 'by'>): number {
  const dx = s.bx - s.ax;
  const dy = s.by - s.ay;
  const len2 = dx * dx + dy * dy || 1;
  const t = Math.max(0, Math.min(1, ((px - s.ax) * dx + (py - s.ay) * dy) / len2));
  return Math.hypot(s.ax + dx * t - px, s.ay + dy * t - py);
}

export type Hit =
  | { kind: 'star'; id: string }
  | { kind: 'cell'; key: string; group: string }
  | { kind: 'soul' };

export type PickInput = {
  stars: ReadonlyArray<{ id: string; x: number; y: number }>;
  cells: readonly CellSegment[];
  soul: { x: number; y: number } | null;
};

export const STAR_REACH = 12;
export const CELL_REACH = 9;
export const SOUL_REACH = 18;

export function pickAt(x: number, y: number, input: PickInput): Hit | null {
  let star: string | null = null;
  let best = STAR_REACH;
  for (const s of input.stars) {
    const d = Math.hypot(s.x - x, s.y - y);
    if (d < best) { best = d; star = s.id; }
  }
  if (star !== null) return { kind: 'star', id: star };
  // As on the plate: the month the pointer is in comes first; the nearest ribbon only catches a point beside every month.
  let cell: CellSegment | null = null;
  best = CELL_REACH;
  for (const seg of input.cells) {
    const dx = seg.ex1 - seg.ex0;
    const dy = seg.ey1 - seg.ey0;
    const len2 = dx * dx + dy * dy;
    if (!len2) continue;
    const along = ((x - seg.ex0) * dx + (y - seg.ey0) * dy) / len2;
    if (along < 0 || along >= 1) continue;
    const d = Math.abs((x - seg.ex0) * dy - (y - seg.ey0) * dx) / Math.sqrt(len2);
    if (d < best) { best = d; cell = seg; }
  }
  if (!cell) {
    best = CELL_REACH;
    for (const seg of input.cells) {
      const d = segmentDistance(x, y, seg);
      if (d < best) { best = d; cell = seg; }
    }
  }
  if (cell) return { kind: 'cell', key: cell.key, group: cell.group };
  if (input.soul && Math.hypot(input.soul.x - x, input.soul.y - y) < SOUL_REACH) return { kind: 'soul' };
  return null;
}
