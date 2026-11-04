import { describe, expect, it } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel, norm } from './chartCoordinates';
import { cellName, navColumns, nextStar, nextStop, parseStop, stopId, type Stop } from './plateNav';
import type { MemoryChart } from './types';

const chart = handleMemory(new URL('http://mock/api/memory/chart?session_id=s&worldline=steins_gate&identity_mode=okabe'), 'GET', {},
  createMemoryStore({ facts: [], topics: [], preset: 'huge' }))!.body as MemoryChart;
const model = buildChartModel(chart, { today: Date.parse('2026-11-04T04:00:00Z'), lastOpen: null });
const shown = new Set(model.stars.filter((s) => s.single).map((s) => s.id));
const columns = navColumns(model, shown, null);

describe('collapsed keyboard walk', () => {
  it('has a column for every group with a cell or a single star, clockwise, and months in order down each', () => {
    expect(columns.length).toBe(model.groups.filter((g) => g.starIds.length).length);
    const angles = columns.map((c) => norm(180 - c.group.phi));
    expect(angles).toEqual([...angles].sort((a, b) => a - b));
    for (const { stops } of columns) {
      const months = stops.map((s) => (s.kind === 'cell' ? model.cells.get(s.key)!.band.key : model.byId.get(s.id)!.cellKey.split('|')[1]));
      expect(months).toEqual([...months].sort());
    }
    expect(columns.flatMap((c) => c.stops).filter((s) => s.kind === 'star').length).toBe(shown.size);
  });

  it('puts a cell before the single stars of its month', () => {
    const column = columns.find((c) => c.stops.some((s) => s.kind === 'star') && c.stops.some((s) => s.kind === 'cell'))!;
    const star = column.stops.findIndex((s) => s.kind === 'star');
    const month = model.byId.get((column.stops[star] as { id: string }).id)!.cellKey;
    const cell = column.stops.findIndex((s) => s.kind === 'cell' && s.key === month);
    if (cell >= 0) expect(cell).toBeLessThan(star);
  });

  it('goes down and up a column and stops at its ends', () => {
    const { stops } = columns.find((c) => c.stops.length > 3)!;
    expect(nextStop(model, columns, stops[0], 'ArrowDown')).toEqual(stops[1]);
    expect(nextStop(model, columns, stops[2], 'ArrowUp')).toEqual(stops[1]);
    expect(nextStop(model, columns, stops[0], 'ArrowUp')).toEqual(stops[0]);
    expect(nextStop(model, columns, stops[stops.length - 1], 'ArrowDown')).toEqual(stops[stops.length - 1]);
  });

  it('changes group with left and right, to the same month or the closest, wrapping round', () => {
    const start = columns[1].stops[Math.floor(columns[1].stops.length / 2)];
    const right = nextStop(model, columns, start, 'ArrowRight')!;
    expect(columns[2].stops.map(stopId)).toContain(stopId(right));
    const monthOf = (s: Stop) => (s.kind === 'cell' ? model.cells.get(s.key)!.band.key : model.byId.get(s.id)!.cellKey.split('|')[1]);
    const idx = (s: Stop) => model.bands.findIndex((b) => b.key === monthOf(s));
    const best = Math.min(...columns[2].stops.map((s) => Math.abs(idx(s) - idx(start))));
    expect(Math.abs(idx(right) - idx(start))).toBe(best);
    const left = nextStop(model, columns, start, 'ArrowLeft')!;
    expect(columns[0].stops.map(stopId)).toContain(stopId(left));
    const wrapped = nextStop(model, columns, columns[columns.length - 1].stops[0], 'ArrowRight')!;
    expect(columns[0].stops.map(stopId)).toContain(stopId(wrapped));
    const back = nextStop(model, columns, columns[0].stops[0], 'ArrowLeft')!;
    expect(columns[columns.length - 1].stops.map(stopId)).toContain(stopId(back));
  });

  it('starts at the first stop, and Home and End reach the first and the last', () => {
    expect(nextStop(model, columns, null, 'ArrowDown')).toEqual(columns[0].stops[0]);
    expect(nextStop(model, columns, { kind: 'cell', key: 'gone|2020-01' }, 'ArrowRight')).toEqual(columns[0].stops[0]);
    expect(nextStop(model, columns, columns[3].stops[1], 'Home')).toEqual(columns[0].stops[0]);
    const last = columns[columns.length - 1];
    expect(nextStop(model, columns, columns[3].stops[1], 'End')).toEqual(last.stops[last.stops.length - 1]);
    expect(nextStop(model, columns, columns[0].stops[0], 'a')).toBeNull();
    expect(nextStop(model, [], null, 'ArrowDown')).toBeNull();
  });

  it('leaves out the open group', () => {
    const open = columns[2].group.id;
    expect(navColumns(model, shown, open).some((c) => c.group.id === open)).toBe(false);
  });

  it('walks the stars of an open group in the order they were written, and stops at the ends', () => {
    const stars = model.stars.slice(0, 5);
    expect(nextStar(stars, stars[1].id, 'ArrowRight')).toBe(stars[2].id);
    expect(nextStar(stars, stars[1].id, 'ArrowUp')).toBe(stars[0].id);
    expect(nextStar(stars, stars[0].id, 'ArrowLeft')).toBe(stars[0].id);
    expect(nextStar(stars, stars[4].id, 'ArrowDown')).toBe(stars[4].id);
    expect(nextStar(stars, stars[2].id, 'End')).toBe(stars[4].id);
    expect(nextStar(stars, null, 'Home')).toBe(stars[0].id);
    expect(nextStar(stars, stars[2].id, 'x')).toBeNull();
  });

  it('names a cell: topic, year and month, how many, collapsed', () => {
    const cell = [...model.cells.values()].find((c) => c.groupId === columns[0].group.id)!;
    const [year, month] = cell.band.key.split('-').map(Number);
    expect(cellName(model, cell)).toBe(`${columns[0].group.label}，${year} 年 ${month} 月，${cell.starIds.length} 条，收起`);
    expect(parseStop(stopId({ kind: 'cell', key: cell.key }))).toEqual({ kind: 'cell', key: cell.key });
    expect(parseStop('f12')).toEqual({ kind: 'star', id: 'f12' });
  });
});
