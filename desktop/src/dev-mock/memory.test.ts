import { describe, expect, it } from 'vitest';
import { createMemoryStore, handleMemory, type MemorySeed } from './memory';
import { fetchFactsPage, fetchLegacyFacts, fetchMemoryStatus, fetchTopics } from '../memory-space/archiveApi';
import { fetchDetails } from '../memory-space/readingDetails';
import type { MemoryProjection } from '../memory-space/types';

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
      type: 'ok', payload: { runtimeMode: 'v11', pendingCount: 0, processingCount: 0, failedCount: 0, failedJobs: [] },
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

  it('returns a complete projection with valid graph references and exact criteria', () => {
    const { call } = harness();
    const p = call('graph/projection?query=lab&kind=fact&kind=topic&topic_id=lab&pinned_only=true&updated_from=2020-01-01&updated_to=2099-01-01').body as MemoryProjection;
    expect(p.scope).toEqual({ session_id: 'session one', worldline: 'beta', identity_mode: 'self' });
    expect(p.criteria).toEqual({ query: 'lab', kinds: ['fact', 'topic'], topic_id: 'lab', pinned_only: true, updated_from: '2020-01-01', updated_to: '2099-01-01' });
    expect(p).toMatchObject({
      view: 'overview', projection_version: expect.any(String), generated_at: expect.any(String),
      center: { kind: 'continuity_hub', label_primary: expect.any(String), label_secondary: expect.any(String) },
      composition: { active_facts: 3, active_experiences: 0, eligible_topics: 1, latest_memory_change_at: expect.any(String), person_anchors_supported: false },
      budgets: { nodes: expect.any(Number), edges: expect.any(Number), hard_max_nodes: expect.any(Number), hard_max_edges: expect.any(Number) },
      eligible: { nodes: 4, edges: 3, records: 2, results: 3 },
      shown: { nodes: 4, edges: 3, records: 2, results: 3 },
      truncated: { nodes: false, edges: false, records: false, results: false }, empty: false,
    });
    expect(p.result_ids).toEqual(['topic:lab', 'fact:a', 'fact:c']);
    expect(p.nodes).toContainEqual({ kind: 'fact', projection_id: 'fact:a', fact_id: 'a', label: 'Coffee at the lab', is_pinned: true, updated_at: expect.any(String), topic_id: 'lab' });
    const ids = new Set(p.nodes.map(n => n.projection_id));
    expect(ids.has(p.center.projection_id)).toBe(true);
    for (const edge of p.edges) {
      expect(ids.has(edge.from) && ids.has(edge.to)).toBe(true);
      expect(['hub_to_anchor', 'has_topic', 'hub_to_evidence']).toContain(edge.kind);
    }
    const expired = call('graph/projection?updated_to=2000-01-01').body as MemoryProjection;
    expect(expired.result_ids).toEqual([]);
    expect(expired.empty).toBe(true);
    expect((call('graph/projection?kind=experience').body as MemoryProjection).result_ids).toEqual([]);
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

  it('provides empty browse, legacy groups, JSON actions and unknown route errors', async () => {
    const { call, store, fetcher } = harness({ facts: [], topics: [] });
    expect(call('experiences').body).toMatchObject({ experiences: [], pagination: { total: 0, has_more: false } });
    expect(call('observations').body).toMatchObject({ observations: [] });
    expect(call('topics').body).toMatchObject({ topics: [] });
    expect(call('graph/projection').body).toMatchObject({ empty: true, result_ids: [], nodes: [{ kind: 'continuity_hub' }] });
    expect(call('facts', 'GET', {}, 'session_id=session+one&worldline=beta').body).toMatchObject({ local: [], shared: [], okabe: [] });
    expect(await fetchLegacyFacts(scope, { fetcher })).toEqual({ type: 'ok', payload: [] });
    expect(call('observations/o/ignore', 'POST').body).toMatchObject({ ok: true });
    expect(call('jobs/j/retry', 'POST').body).toMatchObject({ ok: true });
    expect(call('unknown')).toMatchObject({ status: 404, body: { detail: { code: expect.any(String) } } });
    expect(handleMemory(new URL('http://mock/api/other'), 'GET', {}, store)).toBeUndefined();
    const populated = harness();
    expect(await fetchLegacyFacts(scope, { fetcher: populated.fetcher })).toMatchObject({
      type: 'ok', payload: [{ text: 'Coffee at the lab' }, { text: 'Coffee at home' }, { text: 'Tea at the lab' }],
    });
  });
});
