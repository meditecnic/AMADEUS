// Geometry of the collapsed plate: one ribbon per group, one tick per remembered record. Pure math
// on plate points, so the SVG layer, the expand tween and the hit tests all read the same numbers.
import type { ChartCell, ChartGroup, MonthBand } from './chartCoordinates';

export type Pt = { x: number; y: number };
export type Project = (theta: number, phi: number) => Pt;
export type TickSegment = { id: string; ax: number; ay: number; bx: number; by: number };

/** Ribbon width on the plate, px. */
export const RIBBON_W = 15;
/** The pointer counts as inside a month when it is within this many theta units of it. */
export const CELL_SLACK = 0.6;

/** A month band's drawn span: the ribbon stops short of both rings so neighbouring months leave a hairline gap. */
export function bandSpan(band: Pick<MonthBand, 'theta0' | 'theta1'>): [number, number] {
  const inset = Math.min(1.1, (band.theta1 - band.theta0) * 0.2);
  return [band.theta0 + inset, band.theta1 - inset];
}

/** Ticks of one cell, side by side across the ribbon: the i-th of n sits at (i + 1/2) / n - 1/2 of its width. */
export function cellTicks(cell: ChartCell, phi: number, project: Project, width = RIBBON_W): TickSegment[] {
  const n = cell.collapsedIds.length;
  if (!n) return [];
  const [lo, hi] = bandSpan(cell.band);
  const a = project(lo, phi);
  const b = project(hi, phi);
  const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const ux = (b.x - a.x) / length;
  const uy = (b.y - a.y) / length;
  return cell.collapsedIds.map((id, i) => {
    const u = ((i + 0.5) / n - 0.5) * width;
    const ox = -uy * u;
    const oy = ux * u;
    return { id, ax: a.x + ox, ay: a.y + oy, bx: b.x + ox, by: b.y + oy };
  });
}

const fixed = (v: number) => v.toFixed(1);

export function groupTicks(group: ChartGroup, project: Project, width = RIBBON_W): TickSegment[] {
  const out: TickSegment[] = [];
  for (const cell of group.cells) out.push(...cellTicks(cell, group.phi, project, width));
  return out;
}

export const ticksPath = (ticks: readonly TickSegment[]) =>
  ticks.map((t) => `M${fixed(t.ax)} ${fixed(t.ay)}L${fixed(t.bx)} ${fixed(t.by)}`).join('');

/** The ribbon's centre line through its cells: what the pointer hits. */
export function groupSpine(group: ChartGroup, project: Project): string {
  let d = '';
  for (const cell of group.cells) {
    if (!cell.collapsedIds.length) continue;
    const [lo, hi] = bandSpan(cell.band);
    const a = project(lo, group.phi);
    const b = project(hi, group.phi);
    d += `M${fixed(a.x)} ${fixed(a.y)}L${fixed(b.x)} ${fixed(b.y)}`;
  }
  return d;
}

/** The cell of a group whose month the pointer is in, given its distance from SOUL in theta units. */
export function cellAtTheta(group: ChartGroup, theta: number): ChartCell | null {
  return group.cells.find((cell) => cell.collapsedIds.length
    && theta >= cell.band.theta0 - CELL_SLACK && theta <= cell.band.theta1 + CELL_SLACK) ?? null;
}

/** Outline of a cell as a dashed fan: its month between two rings, over the bearings its records sit at. */
export function cellFanPath(cell: ChartCell, phis: readonly number[], project: Project): string {
  const rel = phis.map((p) => ((p - cell.phi + 540) % 360) - 180);
  const lo = cell.phi + Math.min(...rel) - 2;
  const hi = cell.phi + Math.max(...rel) + 2;
  const arc = (theta: number, from: number, to: number) => {
    let d = '';
    for (let i = 0; i <= 24; i += 1) {
      const p = project(theta, from + ((to - from) * i) / 24);
      d += `${i ? 'L' : 'M'}${fixed(p.x)} ${fixed(p.y)}`;
    }
    return d;
  };
  return `${arc(cell.band.theta0, lo, hi)}${arc(cell.band.theta1, hi, lo).replace(/^M/, 'L')}Z`;
}
