import { describe, expect, it } from 'vitest';
import { createMemoryStore, handleMemory, type MemorySeed } from './memory';
import { fetchFactsPage, fetchMemoryStatus, fetchTopics } from '../memory-space/archiveApi';
import { fetchDetails } from '../memory-space/readingDetails';
import type { MemoryChart } from '../memory-space/types';

const seed: MemorySeed = {
  topics: [{ topic_id: 'lab', display_label: 'Lab' }],
  facts: [
    { fact_id: 'a', display_text: 'Coffee at the lab', topic_id: 'lab', is_pinned: true },
    { fact_id: 'b', display_text: 'Coffee at home', topic_id: null, is_pinned: false },
    { fact_id: 'c', display_text: 'Tea at the lab', topic_id: 'lab', is_pinned: true },
  ],
};
const scope = { sessionId: 'session one', worldline: 'beta', identityMode: 'self' } as const;
const params = 'session_id=session+one&worldline=beta&identity_mode=self';

function harness(input: MemorySeed = seed) {
  const store = createMemoryStore(input);
  const call = (path: string, method = 'GET', body: Record<string, unknown> = {}, query = params) =>
    handleMemory(new URL(`/api/memory/${path}${path.includes('?') ? '&' : '?'}${query}`, 'http://mock'), method, body, store)!;
  const fetcher = (async (url: string | URL | Request, init?: RequestInit) => {
    const response = handleMemory(new URL(String(url), 'http://mock'), init?.method ?? 'GET',
      init?.body ? JSON.parse(String(init.body)) : {}, store)!;
    return new Response(JSON.stringify(response.body), { status: response.status });
  }) as typeof fetch;
  return { call, fetcher, store };
}

describe('in-memory memory REST handler', () => {
  it('echoes exact scopes in frontend-compatible browse and status payloads', async () => {
    const { fetcher } = harness();
    expect(await fetchMemoryStatus(scope, { fetcher })).toEqual({
      type: 'ok', payload: { pendingCount: 0, processingCount: 0, failedCount: 0, failedJobs: [] },
    });
    const facts = await fetchFactsPage(scope, { limit: 20, offset: 0 }, { fetcher });
    expect(facts).toMatchObject({ type: 'ok', payload: { total: 3, hasMore: false, rows: [
      { factId: 'a', versionNo: 1, activeVersion: 1, updatedAt: expect.any(String) },
      { factId: 'b' }, { factId: 'c' },
    ] } });
    expect(await fetchTopics(scope, { fetcher })).toEqual({
      type: 'ok', payload: [{ topicId: 'lab', displayLabel: 'Lab', activeFactCount: 2 }],
    });
  });

  it('isolates mutations by session, worldline and identity and snapshots seed data', () => {
    const localSeed = structuredClone(seed);
    const { call } = harness(localSeed);
    localSeed.facts[0].display_text = 'Changed outside store';
    expect(call('facts/a', 'PATCH', { display_text: 'Edited', expected_version: 1 }).status).toBe(200);
    for (const other of [
      'session_id=other&worldline=beta&identity_mode=self',
      'session_id=session+one&worldline=steins_gate&identity_mode=self',
      'session_id=session+one&worldline=beta&identity_mode=okabe',
    ]) {
      expect(call('facts/a/details', 'GET', {}, other).body).toMatchObject({
        fact: { display_text: 'Coffee at the lab', active_version: 1 },
      });
    }
    expect(call('facts/a/details').body).toMatchObject({ fact: { display_text: 'Edited' } });
  });

  it('combines query, topic and pin filters before pagination', () => {
    const { call } = harness();
    expect(call('facts?query=LAB&topic_id=lab&pinned_only=true&limit=1&offset=1').body).toMatchObject({
      facts: [{ fact_id: 'c' }], pagination: { total: 2, limit: 1, offset: 1, has_more: false },
    });
    expect(call('facts?query=coffee&limit=1').body).toMatchObject({
      facts: [{ fact_id: 'a' }], pagination: { total: 2, has_more: true },
    });
  });

  it('tracks versions, rejects stale corrections/deletions, and persists presentation and topics', async () => {
    const { call, fetcher } = harness();
    expect(call('facts/a', 'PATCH', { display_text: 'Updated', expected_version: 99 }).status).toBe(409);
    expect(call('facts/a', 'PATCH', { display_text: '  ', expected_version: 1 }).status).toBe(422);
    expect(call('facts/a', 'PATCH', { display_text: 'Updated', expected_version: 1 }).status).toBe(200);
    expect(call('facts/a/presentation', 'PATCH', { is_pinned: false, topic_id: null }).status).toBe(200);
    expect(call('facts/a/details').body).toMatchObject({
      fact: { display_text: 'Updated', active_version: 2, is_pinned: false, topic_id: null },
      versions: [
        { version_no: 1, display_text: 'Coffee at the lab', is_active: false },
        { version_no: 2, display_text: 'Updated', previous_version: 1, is_active: true },
      ],
    });
    expect((await fetchDetails({ scope, kind: 'fact', recordId: 'a' }, { fetcher })).type).toBe('ok');
    expect(call('topics/lab', 'PATCH', { display_label: 'Laboratory' }).status).toBe(200);
    expect(call('topics').body).toMatchObject({ topics: [{ display_label: 'Laboratory', fact_count: 1 }] });
    expect(call('facts/a?expected_version=1', 'DELETE').status).toBe(409);
    expect(call('facts/a?expected_version=2', 'DELETE').status).toBe(200);
    expect(call('facts/a/details').status).toBe(404);
    expect(call('facts').body).toMatchObject({ pagination: { total: 2 } });
  });

  it('provides empty browse, JSON actions and unknown route errors', () => {
    const { call, store } = harness({ facts: [], topics: [] });
    expect(call('experiences').body).toMatchObject({ experiences: [], pagination: { total: 0, has_more: false } });
    expect(call('observations').body).toMatchObject({ observations: [] });
    expect(call('topics').body).toMatchObject({ topics: [] });
    expect(call('graph/projection')).toMatchObject({ status: 404 });
    expect(call('observations/o/ignore', 'POST')).toEqual({ status: 404, body: { detail: { code: 'observation_not_found' } } });
    expect(call('jobs/j/retry', 'POST').body).toMatchObject({ ok: true });
    expect(call('unknown')).toMatchObject({ status: 404, body: { detail: { code: expect.any(String) } } });
    expect(handleMemory(new URL('http://mock/api/other'), 'GET', {}, store)).toBeUndefined();
  });

  it('serves the dated chart for presets, consistent with archive rows and details', () => {
    const { call } = harness({ facts: [], topics: [], preset: 'normal' });
    const chart = call('chart').body as MemoryChart;
    expect(chart.scope).toEqual({ session_id: 'session one', worldline: 'beta', identity_mode: 'self' });
    expect(chart.facts.length + chart.experiences.length).toBeGreaterThanOrEqual(20);
    expect(chart.counts).toEqual({ facts: chart.facts.length, experiences: chart.experiences.length, topics: chart.topics.length });
    expect(chart.origin_at).toBeTruthy();
    const created = chart.facts.map(fact => fact.created_at);
    expect(created).toEqual([...created].sort());
    const revised = chart.facts.find(fact => fact.version_count > 1)!;
    const details = call(`facts/${revised.id}/details`).body as { versions: unknown[]; provenance: Array<{ excerpt: string }> };
    expect(details.versions).toHaveLength(revised.version_count);
    expect(details.provenance).toHaveLength(revised.evidence_count);
    expect((call('experiences').body as { experiences: unknown[] }).experiences).toHaveLength(chart.experiences.length);
    const sg = call('chart', 'GET', {}, 'session_id=session+one&worldline=steins_gate&identity_mode=self').body as MemoryChart;
    expect(sg.facts.map(fact => fact.label)).not.toEqual(chart.facts.map(fact => fact.label));
  });

  it('builds the 2000-record library: 30 topics over two years, pinned, resident and fragment records', () => {
    const huge = harness({ facts: [], topics: [], preset: 'huge' }).call('chart').body as MemoryChart;
    expect(huge.facts.length + huge.experiences.length).toBe(2000);
    expect(huge.truncated).toBe(false);
    expect(huge.topics).toHaveLength(30);
    expect(huge.experiences).toHaveLength(250);
    expect(huge.facts.filter(fact => fact.resident)).toHaveLength(32);
    expect(huge.facts.filter(fact => fact.is_pinned).length).toBeGreaterThan(60);
    expect(huge.facts.filter(fact => fact.topic_id === null).length).toBeGreaterThan(100);
    const years = (Date.parse(huge.facts[huge.facts.length - 1].created_at) - Date.parse(huge.origin_at!)) / 31_557_600_000;
    expect(years).toBeGreaterThan(1.8);
    const again = harness({ facts: [], topics: [], preset: 'huge' }).call('chart').body as MemoryChart;
    expect(again.facts.map(fact => fact.id)).toEqual(huge.facts.map(fact => fact.id));
    expect(again.facts.map(fact => fact.created_at)).toEqual(huge.facts.map(fact => fact.created_at));
  });

  it('builds a 140-record library, under the collapse line', () => {
    const mid = harness({ facts: [], topics: [], preset: 'mid' }).call('chart').body as MemoryChart;
    expect(mid.facts.length + mid.experiences.length).toBe(140);
    expect(mid.topics).toHaveLength(8);
    expect(mid.facts.filter(fact => fact.resident)).toHaveLength(32);
  });

  it('builds the large and new-user libraries and an empty chart', () => {
    const large = harness({ facts: [], topics: [], preset: 'large' }).call('chart').body as MemoryChart;
    expect(large.facts.length + large.experiences.length).toBe(300);
    expect(large.topics).toHaveLength(12);
    const months = (Date.parse(large.facts[large.facts.length - 1].created_at) - Date.parse(large.origin_at!)) / 2_629_800_000;
    expect(months).toBeGreaterThan(13);
    expect(large.facts.filter(fact => fact.resident)).toHaveLength(32);
    const fresh = harness({ facts: [], topics: [], preset: 'new' }).call('chart').body as MemoryChart;
    expect(fresh).toMatchObject({ topics: [], experiences: [], counts: { facts: 3 } });
    expect(harness({ facts: [], topics: [] }).call('chart').body).toMatchObject({ origin_at: null, facts: [], counts: { facts: 0 } });
  });

  it('tracks seeded observation confirm/ignore transitions and creates a fact on confirm', () => {
    const { call } = harness({
      facts: [],
      topics: [],
      observations: [
        { observation_id: 'obs-a', display_text: '用户习惯先列提纲再讨论。' },
        { observation_id: 'obs-b', display_text: '用户偶尔在午夜整理数据。' },
      ],
    });
    expect(call('observations?limit=1&offset=0').body).toMatchObject({
      observations: [{ observation_id: 'obs-a', status: 'candidate' }],
      pagination: { total: 2, limit: 1, offset: 0, has_more: true },
    });

    expect(call('observations/obs-a/confirm', 'POST').status).toBe(200);
    expect(call('observations').body).toMatchObject({
      observations: [
        { observation_id: 'obs-a', status: 'attached' },
        { observation_id: 'obs-b', status: 'candidate' },
      ],
    });
    expect(call('facts').body).toMatchObject({
      facts: [{ fact_id: 'fact-obs-a', display_text: '用户习惯先列提纲再讨论。', version_no: 1 }],
    });
    expect(call('facts/fact-obs-a/details').body).toMatchObject({
      provenance: [{ observation_id: 'obs-a', excerpt: '用户习惯先列提纲再讨论。' }],
    });
    expect(call('observations/obs-a/confirm', 'POST')).toEqual({
      status: 409,
      body: { detail: { code: 'observation_not_confirmable' } },
    });
    expect(call('observations/obs-a/ignore', 'POST')).toEqual({
      status: 409,
      body: { detail: { code: 'observation_state_conflict' } },
    });

    expect(call('observations/obs-b/ignore', 'POST').status).toBe(200);
    expect(call('observations/obs-b/confirm', 'POST')).toEqual({
      status: 409,
      body: { detail: { code: 'observation_not_confirmable' } },
    });
  });

  it('answers 404 observation_not_found for confirm and ignore of an unknown observation', () => {
    const { call } = harness({
      ...seed,
      observations: [{ observation_id: 'obs-a', display_text: '用户习惯先列提纲再讨论。' }],
    });
    for (const action of ['confirm', 'ignore']) {
      expect(call(`observations/obs-missing/${action}`, 'POST')).toEqual({
        status: 404,
        body: { detail: { code: 'observation_not_found' } },
      });
    }
    expect(call('observations').body).toMatchObject({ observations: [{ observation_id: 'obs-a', status: 'candidate' }] });
    expect(call('facts').body).toMatchObject({ pagination: { total: 3 } });
  });
});
