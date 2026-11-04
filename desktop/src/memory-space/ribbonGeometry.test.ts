import { describe, expect, it } from 'vitest';
import { DAY_MS, buildChartModel } from './chartCoordinates';
import { CELL_SLACK, cellAtTheta } from './ribbonGeometry';
import type { MemoryChart } from './types';

// The case from the review: the chart starts 2023-01-10 and today is 2026-11-04, so the month of November
// is three days long and its slack zone lies entirely inside October.
const ORIGIN = Date.parse('2023-01-10T09:00:00Z');
const TODAY = Date.parse('2026-11-04T09:00:00Z');
const iso = (at: number) => new Date(at).toISOString();
function chart(): MemoryChart {
  const dates = ['2023-02-15', '2024-06-20', '2025-12-05', '2026-09-12', '2026-10-05', '2026-10-27', '2026-11-01', '2026-11-03'];
  const facts = dates.map((d, i) => ({
    id: `r${i}`, label: `r${i}`, topic_id: 't', created_at: iso(Date.parse(`${d}T12:00:00Z`)), updated_at: iso(Date.parse(`${d}T12:00:00Z`)),
    is_pinned: false, evidence_count: 1, version_count: 1, resident: false,
  }));
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    generated_at: iso(TODAY), origin_at: iso(ORIGIN),
    topics: [{ id: 't', label: '主题', created_at: iso(ORIGIN + DAY_MS), fact_count: facts.length }],
    facts, experiences: [], counts: { facts: facts.length, experiences: 0, topics: 1 }, truncated: false,
  };
}
const model = buildChartModel(chart(), { today: TODAY, lastOpen: null });
const group = model.groups[0];
const cellOf = (month: string) => group.cells.find((c) => c.band.key === month)!;

describe('cellAtTheta', () => {
  it('gives the middle of a three-day month to that month, not to the month before', () => {
    const nov = cellOf('2026-11');
    expect(nov.band.theta1 - nov.band.theta0).toBeLessThan(CELL_SLACK);
    const mid = (nov.band.theta0 + nov.band.theta1) / 2;
    expect(cellAtTheta(group, mid)?.key).toBe(nov.key);
    expect(cellAtTheta(group, nov.band.theta0)?.key).toBe(nov.key);
    expect(cellAtTheta(group, nov.band.theta0 - 1e-6)?.key).toBe(cellOf('2026-10').key);
  });

  it('puts every point inside a month in that month', () => {
    for (const cell of group.cells) {
      for (const f of [0.01, 0.5, 0.99]) {
        const theta = cell.band.theta0 + (cell.band.theta1 - cell.band.theta0) * f;
        expect(cellAtTheta(group, theta)?.key, `${cell.key} at ${f}`).toBe(cell.key);
      }
    }
  });

  it('still catches a point just off either end of the ribbon, within the slack', () => {
    const first = group.cells[0];
    const last = group.cells[group.cells.length - 1];
    expect(cellAtTheta(group, first.band.theta0 - CELL_SLACK / 2)?.key).toBe(first.key);
    expect(cellAtTheta(group, last.band.theta1 + CELL_SLACK / 2)?.key).toBe(last.key);
    expect(cellAtTheta(group, last.band.theta1 + CELL_SLACK * 2)).toBeNull();
  });

  it('picks the nearest month when a point falls in the gap between two months that have ticks', () => {
    const a = cellOf('2023-02');
    const b = cellOf('2024-06');
    expect(a.band.theta1).toBeLessThan(b.band.theta0);
    const near = (theta: number) => cellAtTheta(group, theta);
    expect(near(a.band.theta1 + 0.1)?.key).toBe(a.key);
    expect(near(b.band.theta0 - 0.1)?.key).toBe(b.key);
  });
});
