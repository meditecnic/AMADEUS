// Keyboard walk of a collapsed plate. The plate stays one Tab stop; the arrow keys move between
// the stars it draws and the cells (a topic in a month) it draws only as ticks.
import { norm, type ChartCell, type ChartGroup, type ChartModel } from './chartCoordinates';

export type Stop = { kind: 'star'; id: string } | { kind: 'cell'; key: string };
export const CELL_PREFIX = 'cell:';

export const stopId = (stop: Stop) => (stop.kind === 'star' ? stop.id : `${CELL_PREFIX}${stop.key}`);
export const parseStop = (id: string): Stop => (id.startsWith(CELL_PREFIX) ? { kind: 'cell', key: id.slice(CELL_PREFIX.length) } : { kind: 'star', id });

export type NavColumn = { group: ChartGroup; stops: Stop[] };

/** Clockwise angle from the top of the plate to a bearing; → on the keyboard goes this way round. */
const clockwise = (phi: number) => norm(180 - phi);

/** One column per group that has anything to stop on, in clockwise order; down a column is newer. */
export function navColumns(model: ChartModel, shown: ReadonlySet<string>, openGroup: string | null): NavColumn[] {
  const columns: NavColumn[] = [];
  for (const group of model.groups) {
    if (group.id === openGroup) continue;
    const stops: Stop[] = [];
    for (const cell of group.cells) {
      if (cell.collapsedIds.length) stops.push({ kind: 'cell', key: cell.key });
      for (const id of cell.starIds) if (shown.has(id)) stops.push({ kind: 'star', id });
    }
    if (stops.length) columns.push({ group, stops });
  }
  return columns.sort((a, b) => clockwise(a.group.phi) - clockwise(b.group.phi));
}

function monthIndexOf(model: ChartModel, stop: Stop): number {
  const key = stop.kind === 'cell' ? model.cells.get(stop.key)?.band.key : model.byId.get(stop.id)?.cellKey.split('|')[1];
  return key === undefined ? -1 : model.bands.findIndex((band) => band.key === key);
}

const same = (a: Stop, b: Stop) => stopId(a) === stopId(b);

/** The stop an arrow key leads to, or the first stop when there is no current one. Null when there is nothing to stop on. */
export function nextStop(model: ChartModel, columns: readonly NavColumn[], current: Stop | null, key: string): Stop | null {
  if (!columns.length) return null;
  const first = columns[0].stops[0];
  if (key === 'Home') return first;
  if (key === 'End') { const last = columns[columns.length - 1]; return last.stops[last.stops.length - 1]; }
  const col = current ? columns.findIndex((c) => c.stops.some((s) => same(s, current))) : -1;
  if (col < 0 || !current) return first;
  const stops = columns[col].stops;
  const at = stops.findIndex((s) => same(s, current));
  if (key === 'ArrowDown') return stops[Math.min(stops.length - 1, at + 1)];
  if (key === 'ArrowUp') return stops[Math.max(0, at - 1)];
  if (key !== 'ArrowRight' && key !== 'ArrowLeft') return null;
  const target = columns[(col + (key === 'ArrowRight' ? 1 : columns.length - 1)) % columns.length].stops;
  const month = monthIndexOf(model, current);
  let best = target[0];
  let gap = Infinity;
  for (const stop of target) {
    const d = Math.abs(monthIndexOf(model, stop) - month);
    if (d < gap) { gap = d; best = stop; }
  }
  return best;
}

/** With a group open the plate is a plain row of stars again, in the order they were written down. */
export function nextStar(stars: readonly { id: string }[], currentId: string | null, key: string): string | null {
  if (!stars.length) return null;
  const index = stars.findIndex((s) => s.id === currentId);
  if (key === 'Home') return stars[0].id;
  if (key === 'End') return stars[stars.length - 1].id;
  const step = key === 'ArrowRight' || key === 'ArrowDown' ? 1 : key === 'ArrowLeft' || key === 'ArrowUp' ? -1 : 0;
  if (!step) return null;
  return stars[Math.min(stars.length - 1, Math.max(0, (index < 0 ? 0 : index) + step))].id;
}

/** Screen-reader name of a cell. */
export function cellName(model: ChartModel, cell: ChartCell): string {
  const group = model.groups.find((g) => g.id === cell.groupId)!;
  const [year, month] = cell.band.key.split('-').map(Number);
  return `${group.label}，${year} 年 ${month} 月，${cell.starIds.length} 条，收起`;
}
