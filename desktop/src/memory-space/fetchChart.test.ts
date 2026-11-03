import { describe, expect, it, vi } from 'vitest';
import { buildChartUrl, createChartController, fetchMemoryChart, parseChart, type ChartScope } from './fetchChart';
import { parseMicros, parseMs } from './chartTime';
import type { MemoryChart } from './types';

const scope: ChartScope = { sessionId: 's 1', worldline: 'beta', identityMode: 'okabe' };
const chart: MemoryChart = {
  scope: { session_id: 's 1', worldline: 'beta', identity_mode: 'okabe' },
  generated_at: '2026-11-02T10:00:00Z', origin_at: '2026-08-20T12:00:00Z',
  topics: [{ id: 't1', label: '猫', created_at: '2026-09-01T00:00:00Z', fact_count: 1 }],
  facts: [{ id: 'f1', label: '你养猫。', topic_id: 't1', created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    is_pinned: false, evidence_count: 2, version_count: 1, resident: true }],
  experiences: [{ id: 'e1', label: '你看了展。', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    is_pinned: false, expires_at: null }],
  counts: { facts: 1, experiences: 1, topics: 1 }, truncated: false,
};
const respond = (status: number, body: unknown) => vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe('memory chart read', () => {
  it('asks for the exact scope', () => {
    expect(buildChartUrl(scope)).toBe('/api/memory/chart?session_id=s+1&worldline=beta&identity_mode=okabe');
  });

  it('accepts a well-formed chart and rejects another scope or malformed rows', () => {
    expect(parseChart(chart, scope)).toEqual(chart);
    expect(parseChart({ ...chart, scope: { ...chart.scope, identity_mode: 'self' } }, scope)).toBeNull();
    expect(parseChart({ ...chart, facts: [{ ...chart.facts[0], evidence_count: -1 }] }, scope)).toBeNull();
    expect(parseChart({ ...chart, experiences: [{ ...chart.experiences[0], created_at: 'yesterday' }] }, scope)).toBeNull();
    expect(parseChart({ ...chart, counts: undefined }, scope)).toBeNull();
  });

  it('maps 503 to unavailable and other failures to a generic error', async () => {
    expect(await fetchMemoryChart(scope, { fetcher: respond(503, { detail: { code: 'memory_unavailable' } }) }))
      .toEqual({ type: 'error', error: { kind: 'unavailable', code: 'memory_unavailable' } });
    expect(await fetchMemoryChart(scope, { fetcher: respond(500, {}) }))
      .toEqual({ type: 'error', error: { kind: 'generic', code: 'chart_failed' } });
    expect(await fetchMemoryChart(scope, { fetcher: respond(200, { ...chart, facts: 'x' }) }))
      .toEqual({ type: 'error', error: { kind: 'generic', code: 'invalid_chart_payload' } });
  });

  it('aborts the older request when the scope changes and never reports it', async () => {
    const signals: AbortSignal[] = [];
    const releases: Array<() => void> = [];
    const controller = createChartController((_scope, options) => new Promise((resolve) => {
      signals.push(options!.signal!);
      releases.push(() => resolve({ type: 'ok', chart }));
    }));
    const first = controller.load(scope);
    const second = controller.load({ ...scope, worldline: 'steins_gate' });
    expect(signals[0].aborted).toBe(true);
    releases.forEach((release) => release());
    expect(await first).toEqual({ type: 'stale' });
    expect(await second).toEqual({ type: 'ok', chart });
  });
});

describe('memory chart field checks', () => {
  const drop = <T extends object>(row: T, key: string) => {
    const copy = { ...row } as Record<string, unknown>;
    delete copy[key];
    return copy;
  };
  const withFact = (fact: unknown) => ({ ...chart, facts: [fact] });
  const withTopic = (topic: unknown) => ({ ...chart, topics: [topic] });
  const withExperience = (experience: unknown) => ({ ...chart, experiences: [experience] });

  it('rejects any missing field', () => {
    for (const key of Object.keys(chart)) expect(parseChart(drop(chart, key), scope), key).toBeNull();
    for (const key of Object.keys(chart.facts[0])) expect(parseChart(withFact(drop(chart.facts[0], key)), scope), `fact.${key}`).toBeNull();
    for (const key of Object.keys(chart.topics[0])) expect(parseChart(withTopic(drop(chart.topics[0], key)), scope), `topic.${key}`).toBeNull();
    for (const key of Object.keys(chart.experiences[0])) {
      expect(parseChart(withExperience(drop(chart.experiences[0], key)), scope), `experience.${key}`).toBeNull();
    }
    for (const key of Object.keys(chart.counts)) expect(parseChart({ ...chart, counts: drop(chart.counts, key) }, scope), `counts.${key}`).toBeNull();
  });

  it('rejects a field of the wrong type', () => {
    expect(parseChart({ ...chart, truncated: 'yes' }, scope)).toBeNull();
    expect(parseChart({ ...chart, truncated: 0 }, scope)).toBeNull();
    expect(parseChart(withTopic({ ...chart.topics[0], fact_count: 1.5 }), scope)).toBeNull();
    expect(parseChart(withTopic({ ...chart.topics[0], fact_count: '1' }), scope)).toBeNull();
    expect(parseChart(withFact({ ...chart.facts[0], resident: 1 }), scope)).toBeNull();
    expect(parseChart(withFact({ ...chart.facts[0], topic_id: 7 }), scope)).toBeNull();
    expect(parseChart(withFact({ ...chart.facts[0], version_count: -1 }), scope)).toBeNull();
    expect(parseChart(withExperience({ ...chart.experiences[0], is_pinned: 'false' }), scope)).toBeNull();
    expect(parseChart({ ...chart, counts: { ...chart.counts, topics: null } }, scope)).toBeNull();
    expect(parseChart({ ...chart, scope: { ...chart.scope, worldline: 'alpha' } }, scope)).toBeNull();
    expect(parseChart({ ...chart, facts: {} }, scope)).toBeNull();
  });

  it('rejects a broken time anywhere', () => {
    expect(parseChart(withFact({ ...chart.facts[0], updated_at: 'bad' }), scope)).toBeNull();
    expect(parseChart(withFact({ ...chart.facts[0], created_at: '2026-02-30T00:00:00Z' }), scope)).toBeNull();
    expect(parseChart({ ...chart, generated_at: 'now' }, scope)).toBeNull();
    expect(parseChart({ ...chart, origin_at: '' }, scope)).toBeNull();
    expect(parseChart(withTopic({ ...chart.topics[0], created_at: null }), scope)).toBeNull();
    expect(parseChart(withExperience({ ...chart.experiences[0], expires_at: 'soon' }), scope)).toBeNull();
    expect(parseChart(withExperience({ ...chart.experiences[0], updated_at: '2026-10-01T25:00:00Z' }), scope)).toBeNull();
  });

  it('accepts Z and +00:00 times, microseconds, and null only where the backend allows it', () => {
    const mixed = {
      ...chart,
      generated_at: '2026-11-02T10:00:00+00:00',
      origin_at: null,
      facts: [{ ...chart.facts[0], created_at: '2026-09-01T12:00:00.000100+00:00', updated_at: '2026-09-02T00:00:00Z', topic_id: null }],
      experiences: [{ ...chart.experiences[0], expires_at: '2999-01-01T00:00:00.5Z' }],
    };
    expect(parseChart(mixed, scope)).toEqual(mixed);
  });
});

describe('chart times', () => {
  it('keeps microseconds and reads Z, +00:00, offsets and naive times as the backend does', () => {
    expect(parseMicros('2026-09-01T12:00:00.000900+00:00')! - parseMicros('2026-09-01T12:00:00.000100Z')!).toBe(800);
    expect(parseMicros('2026-09-01T20:00:00+08:00')).toBe(parseMicros('2026-09-01T12:00:00Z'));
    expect(parseMicros('2026-09-01 12:00:00')).toBe(parseMicros('2026-09-01T12:00:00Z'));
    expect(parseMs('2026-09-01T12:00:00.123456Z')).toBe(Date.parse('2026-09-01T12:00:00.123Z'));
    expect(parseMicros('2026-09-01')).toBeNull();
    expect(Number.isNaN(parseMs('bad'))).toBe(true);
  });
});
