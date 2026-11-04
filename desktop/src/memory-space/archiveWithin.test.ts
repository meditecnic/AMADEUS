import { describe, expect, it } from 'vitest';

import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import type { ArchiveScope } from './archiveApi';
import { PAGE_SIZE, createArchiveModule } from './archiveState';
import type { MemoryChart } from './types';

const SCOPE: ArchiveScope = { sessionId: 's', worldline: 'steins_gate', identityMode: 'okabe' };
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function served() {
  const store = createMemoryStore({ facts: [], topics: [], preset: 'huge' });
  const urls: string[] = [];
  const fetcher = (async (input: string | URL | Request) => {
    const url = new URL(String(input), 'http://mock');
    urls.push(`${url.pathname}${url.search}`);
    const result = handleMemory(url, 'GET', {}, store)!;
    return new Response(JSON.stringify(result.body), { status: result.status });
  }) as typeof fetch;
  const chart = handleMemory(new URL('http://mock/api/memory/chart?session_id=s&worldline=steins_gate&identity_mode=okabe'), 'GET', {}, store)!.body as MemoryChart;
  return { fetcher, urls, chart };
}

describe('archive: only one month or cell of the chart', () => {
  it('keeps the records in the set, across the backend pages, and pages them by 20', async () => {
    const { fetcher, urls, chart } = served();
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE);
    await flush();
    expect(module.snapshot().facts.total).toBe(chart.facts.length);

    // Spread over the whole library so the walk really crosses several 100-record pages.
    const facts = chart.facts.filter((_, i) => i % 40 === 0).slice(0, 45);
    const fragments = chart.experiences.filter((_, i) => i % 50 === 0);
    urls.length = 0;
    module.dispatch({ type: 'set-within', within: { label: '2026.09', ids: [...facts, ...fragments].map((row) => row.id) } });
    await flush();
    await flush();
    const state = module.snapshot();
    expect(state.filters.within).toMatchObject({ label: '2026.09' });
    expect(state.facts.total).toBe(facts.length);
    expect(state.facts.rows).toHaveLength(PAGE_SIZE);
    expect(state.facts.hasMore).toBe(true);
    expect(state.facts.rows.map((row) => row.factId)).toEqual(facts.slice(0, PAGE_SIZE).map((row) => row.id));
    expect(state.experiences.total).toBe(fragments.length);
    expect(state.experiences.rows.map((row) => row.experienceId).sort()).toEqual(fragments.map((row) => row.id).sort().slice(0, PAGE_SIZE));
    expect(urls.every((url) => /limit=100/.test(url))).toBe(true);
    expect(urls.filter((url) => url.startsWith('/api/memory/facts?')).length).toBeGreaterThan(1);

    module.dispatch({ type: 'page', kind: 'fact', offset: PAGE_SIZE });
    await flush();
    await flush();
    const second = module.snapshot().facts;
    expect(second.rows.map((row) => row.factId)).toEqual(facts.slice(PAGE_SIZE, 2 * PAGE_SIZE).map((row) => row.id));
    module.dispatch({ type: 'page', kind: 'fact', offset: 2 * PAGE_SIZE });
    await flush();
    await flush();
    expect(module.snapshot().facts).toMatchObject({ hasMore: false, total: facts.length });
    expect(module.snapshot().facts.rows).toHaveLength(facts.length - 2 * PAGE_SIZE);
    module.dispose();
  });

  it('asks the backend for the topic as well, stops walking once it has them all, and forgets search words', async () => {
    const { fetcher, urls, chart } = served();
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE);
    await flush();
    module.dispatch({ type: 'set-query', query: '认知' });
    await flush();
    const topic = chart.topics.find((t) => chart.facts.filter((f) => f.topic_id === t.id).length > 60)!;
    const cell = chart.facts.filter((f) => f.topic_id === topic.id).slice(0, 5).map((f) => f.id);
    urls.length = 0;
    module.dispatch({ type: 'set-within', within: { label: '实验与观测 · 2025.03', ids: cell }, topicId: topic.id });
    await flush();
    await flush();
    const state = module.snapshot();
    expect(state.filters).toMatchObject({ query: '', topicId: topic.id, pinnedOnly: false });
    expect(state.facts.rows.map((row) => row.factId).sort()).toEqual([...cell].sort());
    expect(urls.filter((url) => url.startsWith('/api/memory/facts?')).every((url) => url.includes(`topic_id=${topic.id}`))).toBe(true);
    // Fragments have no topic, and none of the cell's ids are fragments.
    expect(state.experiences.total).toBe(0);
    module.dispose();
  });

  it('clears the filter and brings every record back, keeping the topic', async () => {
    const { fetcher, chart } = served();
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE);
    await flush();
    const topic = chart.topics.find((t) => chart.facts.filter((f) => f.topic_id === t.id).length > 60)!;
    const inTopic = chart.facts.filter((f) => f.topic_id === topic.id);
    module.dispatch({ type: 'set-within', within: { label: 'x', ids: inTopic.slice(0, 3).map((f) => f.id) }, topicId: topic.id });
    await flush();
    await flush();
    expect(module.snapshot().facts.total).toBe(3);
    module.dispatch({ type: 'set-within', within: null });
    await flush();
    await flush();
    const state = module.snapshot();
    expect(state.filters.within).toBeNull();
    expect(state.filters.topicId).toBe(topic.id);
    expect(state.facts.total).toBe(inTopic.length);
    module.dispose();
  });

  it('reports a failed page as failed, not as an empty cell', async () => {
    const { chart } = served();
    const module = createArchiveModule({ fetcher: (async () => new Response('{}', { status: 503 })) as typeof fetch });
    module.open(SCOPE);
    await flush();
    module.dispatch({ type: 'set-within', within: { label: 'x', ids: [chart.facts[0].id] } });
    await flush();
    await flush();
    expect(module.snapshot().facts.status).toBe('failed');
    module.dispose();
  });

  it('clearing the filter leaves the search words and "仅置顶" as the user set them', async () => {
    const { fetcher, chart } = served();
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE);
    await flush();
    const ids = chart.facts.slice(0, 40).map((row) => row.id);
    module.dispatch({ type: 'set-within', within: { label: '2026.09', ids } });
    await flush();
    await flush();
    module.dispatch({ type: 'set-query', query: '认知' });
    module.dispatch({ type: 'set-pinned-only', pinnedOnly: true });
    await flush();
    await flush();
    module.dispatch({ type: 'set-within', within: null });
    await flush();
    await flush();
    const state = module.snapshot();
    expect(state.filters).toMatchObject({ query: '认知', pinnedOnly: true, within: null });
    expect(state.facts.rows.every((row) => row.isPinned)).toBe(true);
    expect(state.facts.total).toBe(chart.facts.filter((row) => row.is_pinned).length);
    module.dispose();
  });

  it('putting a filter on still starts from no search words and not "仅置顶"', async () => {
    const { fetcher, chart } = served();
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE);
    await flush();
    module.dispatch({ type: 'set-query', query: '认知' });
    module.dispatch({ type: 'set-pinned-only', pinnedOnly: true });
    await flush();
    module.dispatch({ type: 'set-within', within: { label: 'x', ids: [chart.facts[0].id] } });
    await flush();
    await flush();
    expect(module.snapshot().filters).toMatchObject({ query: '', pinnedOnly: false });
    module.dispose();
  });
});
