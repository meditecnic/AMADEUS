import { describe, expect, it } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import {
  DAY_MS, MIN_SEPARATION, MIN_SPAN_DAYS, buildChartModel, platePoint, thetaAt, zoneOf, type ChartModel,
} from './chartCoordinates';
import type { MemoryChart } from './types';

const T0 = Date.parse('2026-08-20T12:00:00Z');
const day = (n: number) => new Date(T0 + n * DAY_MS).toISOString();
const TODAY = T0 + 74 * DAY_MS;

function chart(overrides: Partial<MemoryChart> = {}): MemoryChart {
  const fact = (id: string, d: number, topic: string | null, evidence = 1) => ({
    id, label: id, topic_id: topic, created_at: day(d), updated_at: day(d), is_pinned: false,
    evidence_count: evidence, version_count: 1, resident: true,
  });
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    generated_at: day(74), origin_at: day(0),
    topics: [
      { id: 'lab', label: '实验', created_at: day(1), fact_count: 3 },
      { id: 'cat', label: '猫', created_at: day(20), fact_count: 2 },
    ],
    facts: [fact('a', 1, 'lab', 3), fact('b', 5, 'lab'), fact('c', 40, 'lab'), fact('d', 20, 'cat', 4),
      fact('e', 21, 'cat'), fact('f', 30, null), fact('g', 30, null)],
    experiences: [{ id: 'x', label: 'x', created_at: day(60), updated_at: day(60), is_pinned: false, expires_at: day(90) }],
    counts: { facts: 7, experiences: 1, topics: 2 }, truncated: false, ...overrides,
  };
}
const positions = (model: ChartModel) => new Map(model.stars.map((s) => [s.id, [s.theta, s.phi]]));

describe('chart coordinates', () => {
  it('places the same data at the same spots, whatever the input order', () => {
    const base = chart();
    const a = buildChartModel(base, { today: TODAY, lastOpen: null });
    const b = buildChartModel({ ...base, facts: [...base.facts].reverse(), topics: [...base.topics].reverse() }, { today: TODAY, lastOpen: null });
    expect(positions(b)).toEqual(positions(a));
    expect(b.topics.map((t) => t.phi)).toEqual(a.topics.map((t) => t.phi));
  });

  it('keeps every earlier star and topic in place when a topic and a record are added', () => {
    const base = chart();
    const before = buildChartModel(base, { today: TODAY, lastOpen: null });
    const after = buildChartModel({
      ...base,
      topics: [...base.topics, { id: 'work', label: '工作', created_at: day(65), fact_count: 1 }],
      facts: [...base.facts, { ...base.facts[0], id: 'w', topic_id: 'work', created_at: day(66) },
        { ...base.facts[5], id: 'h', created_at: day(70) }],
    }, { today: TODAY, lastOpen: null });
    for (const [id, spot] of positions(before)) expect(positions(after).get(id)).toEqual(spot);
    expect(after.topics.slice(0, 2).map((t) => t.phi)).toEqual(before.topics.map((t) => t.phi));
  });

  it('orders records written in the same millisecond by their real time, so the earlier star never moves', () => {
    const base = chart();
    const old = { ...base.facts[0], id: 'z-existing', created_at: '2026-09-01T12:00:00.000100+00:00', updated_at: '2026-09-01T12:00:00.000100+00:00' };
    const before = buildChartModel({ ...base, facts: [...base.facts, old] }, { today: TODAY, lastOpen: null });
    const after = buildChartModel({
      ...base,
      facts: [...base.facts, old, { ...old, id: 'a-new-0', created_at: '2026-09-01T12:00:00.000900+00:00' }],
    }, { today: TODAY, lastOpen: null });
    expect(positions(after).get('z-existing')).toEqual(positions(before).get('z-existing'));
    expect(after.byId.get('z-existing')!.no).toBeLessThan(after.byId.get('a-new-0')!.no);
    const zulu = buildChartModel({ ...base, facts: [...base.facts, { ...old, created_at: '2026-09-01T12:00:00.000100Z' }] }, { today: TODAY, lastOpen: null });
    expect(positions(zulu)).toEqual(positions(before));
  });
  it('puts later records further out, with SOUL at the first conversation', () => {
    const model = buildChartModel(chart(), { today: TODAY, lastOpen: null });
    const ordered = [...model.stars].sort((p, q) => p.createdAt - q.createdAt);
    ordered.slice(1).forEach((star, i) => expect(star.theta).toBeGreaterThanOrEqual(ordered[i].theta));
    expect(thetaAt(T0, T0)).toBe(9);
    expect(model.thetaToday).toBeCloseTo(9 + 6.2 * Math.sqrt(74));
    expect(model.stars.map((s) => s.no)).toEqual(model.stars.map((_, i) => i + 1));
  });

  it('pushes crowded neighbours apart around the ring only, never in time', () => {
    const base = chart();
    const crowd = Array.from({ length: 6 }, (_, i) => ({ ...base.facts[0], id: `p${i}`, created_at: day(30) }));
    const model = buildChartModel({ ...base, facts: [...base.facts, ...crowd] }, { today: TODAY, lastOpen: null });
    const theta30 = thetaAt(T0, Date.parse(day(30)));
    const pushed = model.stars.filter((s) => s.id.startsWith('p'));
    pushed.forEach((s) => expect(s.theta).toBeCloseTo(theta30));
    const rad = (d: number) => (d * Math.PI) / 180;
    for (let i = 0; i < pushed.length; i += 1) for (let j = i + 1; j < pushed.length; j += 1) {
      const [p, q] = [pushed[i], pushed[j]];
      const gap = Math.hypot(Math.sin(rad(p.phi)) * p.theta - Math.sin(rad(q.phi)) * q.theta,
        Math.cos(rad(p.phi)) * p.theta - Math.cos(rad(q.phi)) * q.theta);
      expect(gap).toBeGreaterThanOrEqual(MIN_SEPARATION - 1e-9);
    }
  });

  it('marks what was written after the last visit and sizes stars by mentions', () => {
    const model = buildChartModel(chart(), { today: TODAY, lastOpen: Date.parse(day(35)) });
    expect(model.stars.filter((s) => s.isNew).map((s) => s.id)).toEqual(['c', 'x']);
    expect(model.byId.get('d')!.radius).toBeCloseTo(1.9 + 0.55 * 4);
    expect(model.byId.get('f')!.kind).toBe('loose');
    expect(model.byId.get('x')!.kind).toBe('fragment');
  });

  it('draws a young chart at six weeks of scale and month rings from the first month', () => {
    const young = buildChartModel(chart({ origin_at: day(70) }), { today: TODAY, lastOpen: null });
    expect(young.thetaFit).toBeCloseTo(9 + 6.2 * Math.sqrt(MIN_SPAN_DAYS));
    const model = buildChartModel(chart(), { today: TODAY, lastOpen: null });
    expect(model.rings.map((r) => r.key)).toEqual(['2026-09', '2026-10', '2026-11']);
    expect(model.rings.at(-1)!.nearToday).toBe(true);
    const empty = buildChartModel(chart({ origin_at: null, facts: [], experiences: [], topics: [] }), { today: TODAY, lastOpen: null });
    expect(empty).toMatchObject({ origin: null, rings: [], stars: [] });
  });

  it('projects onto the plate and names the frame cell', () => {
    const p = platePoint(50, 90, 300, 50);
    expect(p.x).toBeCloseTo(288);
    expect(p.y).toBeCloseTo(0);
    expect(zoneOf(20, 20, 1440, 840)).toBe('A1');
    expect(zoneOf(1430, 830, 1440, 840)).toBe('F8');
  });

  it('lays out a 300-record, 12-topic, 14-month library quickly', () => {
    const store = createMemoryStore({ facts: [], topics: [], preset: 'large' });
    const large = handleMemory(new URL('http://mock/api/memory/chart?session_id=s&worldline=steins_gate&identity_mode=okabe'), 'GET', {}, store)!.body as MemoryChart;
    const today = Date.parse('2026-11-02T14:00:00Z');
    const started = performance.now();
    const model = buildChartModel(large, { today, lastOpen: null });
    const elapsed = performance.now() - started;
    console.info(`[chart-coordinates] 300 records: ${elapsed.toFixed(1)} ms`);
    expect(model.stars).toHaveLength(300);
    expect(model.topics).toHaveLength(12);
    expect(model.rings.length).toBeGreaterThanOrEqual(13);
    expect(elapsed).toBeLessThan(500);
  });
});
