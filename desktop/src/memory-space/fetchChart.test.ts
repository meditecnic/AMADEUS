import { describe, expect, it, vi } from 'vitest';
import { buildChartUrl, createChartController, fetchMemoryChart, parseChart, type ChartScope } from './fetchChart';
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
