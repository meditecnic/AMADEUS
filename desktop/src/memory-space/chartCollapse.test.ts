import { describe, expect, it } from 'vitest';
import {
  COLLAPSE_AT, DAY_MS, EXPAND_BELOW, FRAGMENT_GROUP, LOOSE_GROUP, buildChartModel, type ChartModel,
} from './chartCoordinates';
import type { MemoryChart } from './types';

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ORIGIN = Date.parse('2025-02-10T09:00:00Z');
const TODAY = Date.parse('2026-11-03T09:00:00Z');
const iso = (at: number) => new Date(at).toISOString();

/** `count` records over the whole span, 5 topics, one in eight a fragment, one in ten loose. Nothing resident unless asked. */
function library(count: number, options: { topics?: number; resident?: (i: number) => boolean; seed?: number } = {}): MemoryChart {
  const random = mulberry32(options.seed ?? count);
  const topicCount = options.topics ?? 5;
  const span = TODAY - ORIGIN;
  const topics = Array.from({ length: topicCount }, (_, i) => ({
    id: `t${i}`, label: `主题${i}`, created_at: iso(ORIGIN + (i + 1) * 1000), fact_count: 0,
  }));
  const facts: MemoryChart['facts'] = [];
  const experiences: MemoryChart['experiences'] = [];
  for (let i = 0; i < count; i += 1) {
    const at = ORIGIN + 60_000 + Math.floor(span * random()) + i;
    const id = `r${String(i).padStart(4, '0')}`;
    if (i % 8 === 7) {
      experiences.push({ id, label: id, created_at: iso(at), updated_at: iso(at), is_pinned: false, expires_at: null });
    } else {
      facts.push({
        id, label: id, topic_id: i % 10 === 3 ? null : topics[i % topicCount].id, created_at: iso(at), updated_at: iso(at),
        is_pinned: false, evidence_count: 1, version_count: 1, resident: options.resident?.(i) ?? false,
      });
    }
  }
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    generated_at: iso(TODAY), origin_at: iso(ORIGIN), topics, facts, experiences,
    counts: { facts: facts.length, experiences: experiences.length, topics: topicCount }, truncated: false,
  };
}
const build = (chart: MemoryChart, wasCollapsed?: boolean, lastOpen: number | null = null) =>
  buildChartModel(chart, { today: TODAY, lastOpen, wasCollapsed });

describe('collapse threshold', () => {
  it('collapses at 150 and goes back only below 130, remembering the last state in between', () => {
    expect(COLLAPSE_AT).toBe(150);
    expect(EXPAND_BELOW).toBe(130);
    expect(build(library(149), false).collapsed).toBe(false);
    expect(build(library(150), false).collapsed).toBe(true);
    expect(build(library(131), true).collapsed).toBe(true);
    expect(build(library(130), true).collapsed).toBe(true);
    expect(build(library(129), true).collapsed).toBe(false);
    expect(build(library(140), false).collapsed).toBe(false);
    expect(build(library(140)).collapsed).toBe(false);
    expect(build(library(400), false).collapsed).toBe(true);
  });

  it('counts cognitions and fragments together', () => {
    const chart = library(150);
    expect(chart.facts.length).toBeLessThan(150);
    expect(build(chart).stars).toHaveLength(150);
    expect(build(chart).collapsed).toBe(true);
  });
});

describe('cells and bands', () => {
  const model = build(library(400));
  const inCells = (m: ChartModel) => [...m.cells.values()].flatMap((cell) => cell.starIds);

  it('puts every star in exactly one cell, of its own group and month', () => {
    const ids = inCells(model);
    expect(ids).toHaveLength(model.stars.length);
    expect(new Set(ids).size).toBe(model.stars.length);
    for (const star of model.stars) {
      const cell = model.cells.get(star.cellKey)!;
      expect(cell.starIds).toContain(star.id);
      expect(star.cellKey).toBe(`${star.group}|${cell.band.key}`);
      expect(star.createdAt).toBeGreaterThanOrEqual(cell.band.start);
      expect(star.createdAt).toBeLessThan(cell.band.end + 1);
    }
    expect(model.bands.flatMap((band) => band.starIds)).toHaveLength(model.stars.length);
  });

  it('orders groups as topics by creation, then ungrouped, then fragments, and cells by month', () => {
    expect(model.groups.map((g) => g.id)).toEqual(['t0', 't1', 't2', 't3', 't4', LOOSE_GROUP, FRAGMENT_GROUP]);
    expect(model.groups.map((g) => g.isTopic)).toEqual([true, true, true, true, true, false, false]);
    for (const group of model.groups) {
      const keys = group.cells.map((cell) => cell.band.key);
      expect(keys).toEqual([...keys].sort());
      expect(group.cells.flatMap((c) => c.starIds).sort()).toEqual([...group.starIds].sort());
    }
    expect(model.byId.get('r0007')!.group).toBe(FRAGMENT_GROUP);
    expect(model.byId.get('r0003')!.group).toBe(LOOSE_GROUP);
    const [loose, fragment] = model.groups.slice(-2);
    expect(Math.abs(loose.phi - fragment.phi)).toBeGreaterThan(1);
  });

  it('writes cell members in the order they were written down, with a middle theta and a mean bearing', () => {
    for (const cell of model.cells.values()) {
      const times = cell.starIds.map((id) => model.byId.get(id)!.no);
      expect(times).toEqual([...times].sort((a, b) => a - b));
      const mid = model.byId.get(cell.starIds[Math.floor(cell.starIds.length / 2)])!;
      expect(cell.theta).toBe(mid.theta);
      expect(cell.phi).toBeGreaterThanOrEqual(0);
      expect(cell.phi).toBeLessThan(360);
    }
  });

  it('keeps resident and new stars out of collapsedIds, and marks them single', () => {
    const chart = library(400, { resident: (i) => i % 25 === 0 });
    const m = build(chart, false, TODAY - 20 * DAY_MS);
    const singles = m.stars.filter((s) => s.single);
    expect(singles.length).toBeGreaterThan(10);
    expect(m.stars.some((s) => s.resident && !s.isNew)).toBe(true);
    expect(m.stars.some((s) => s.isNew && !s.resident)).toBe(true);
    for (const star of m.stars) expect(star.single).toBe(star.resident || star.isNew);
    for (const cell of m.cells.values()) {
      for (const id of cell.collapsedIds) expect(m.byId.get(id)!.single).toBe(false);
      expect(cell.collapsedIds.length + cell.starIds.filter((id) => m.byId.get(id)!.single).length).toBe(cell.starIds.length);
    }
    expect(m.stars.filter((s) => s.kind === 'fragment').every((s) => !s.resident)).toBe(true);
  });

  it('merges old rings into quarters and years without touching the monthly bands', () => {
    const chart = library(140);
    const open = build(chart, false);
    const shut = build(chart, true);
    expect(open.collapsed).toBe(false);
    expect(shut.collapsed).toBe(true);
    expect(shut.bands).toEqual(open.bands);
    expect(shut.bands).toHaveLength(22);
    expect(shut.bands.map((band) => band.key).slice(0, 2)).toEqual(['2025-02', '2025-03']);
    expect(shut.bands.at(-1)!.key).toBe('2026-11');
    expect(open.rings.some((r) => r.old && r.kind === 'month')).toBe(true);
    expect(shut.rings.filter((r) => r.old).every((r) => r.kind !== 'month')).toBe(true);
    expect(shut.rings.filter((r) => !r.old && r.kind === 'month').length).toBeGreaterThanOrEqual(6);
    expect(shut.rings.filter((r) => r.kind === 'year').map((r) => r.key)).toEqual(['2026-01']);
    expect(shut.rings.filter((r) => r.kind === 'quarter').map((r) => r.key)).toEqual(['2025-04', '2025-07', '2025-10', '2026-04', '2026-07', '2026-10']);
    expect(shut.rings.filter((r) => r.old).length).toBe(3);
    expect(shut.cells.size).toBe(open.cells.size);
    for (const [key, cell] of shut.cells) expect(cell.band.key).toBe(open.cells.get(key)!.band.key);
  });
});

describe('records older than the chart start', () => {
  // origin_at is the first message still there; a record can predate it once that conversation is deleted.
  function withEarlier() {
    const base = library(200);
    // Two, so that the later one shows the position rather than the start itself (the earliest record sits at SOUL).
    const row = (id: string, at: string) => ({
      id, label: id, topic_id: 't0', created_at: at, updated_at: at,
      is_pinned: false, evidence_count: 1, version_count: 1, resident: false,
    });
    const facts = [...base.facts, row('first', '2024-12-20T10:00:00Z'), row('early', '2024-12-28T10:00:00Z')];
    return { ...base, origin_at: '2025-01-05T09:00:00Z', facts, counts: { ...base.counts, facts: facts.length } };
  }

  it('dates it in its own month and puts it past SOUL, not on the first month', () => {
    const model = build(withEarlier());
    const star = model.byId.get('early')!;
    expect(star.cellKey).toBe('t0|2024-12');
    expect(model.cells.get('t0|2024-12')!.starIds).toContain('early');
    expect(star.theta).toBeGreaterThan(9);
    expect(model.bands[0].key).toBe('2024-12');
    expect(model.bands.map((b) => b.key)).toContain('2025-01');
    expect(model.byId.get('first')!.theta).toBe(9);
    expect(model.byId.get('first')!.cellKey).toBe('t0|2024-12');
    expect(model.origin).toBe(Date.parse('2024-12-20T10:00:00Z'));
    expect(model.cells.get('t0|2025-01')?.starIds ?? []).not.toContain('early');
  });

  it('changes nothing when every record is at or after origin_at', () => {
    const base = library(200);
    const same = build({ ...base, origin_at: base.origin_at });
    expect(same.origin).toBe(Date.parse(base.origin_at!));
    expect(same.bands[0].key).toBe('2025-02');
    expect(same.stars.every((s) => s.theta >= 9)).toBe(true);
  });
});

describe('modelling cost', () => {
  it('builds a 2000-record, 30-topic library in linear time', () => {
    const chart = library(2000, { topics: 30, resident: (i) => i % 60 === 0 });
    build(chart);
    const runs = Array.from({ length: 11 }, () => {
      const t0 = performance.now();
      const m = build(chart, true);
      const ms = performance.now() - t0;
      expect(m.collapsed).toBe(true);
      return ms;
    }).sort((a, b) => a - b);
    const [median, slowest] = [runs[Math.floor(runs.length / 2)], runs[runs.length - 1]];
    console.info(`[200] 2000 records: median ${median.toFixed(1)} ms, slowest ${slowest.toFixed(1)} ms`);
    expect(median).toBeLessThan(500);
  });
});
