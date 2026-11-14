import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useWorldlineLedger } from './worldlineLedger';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

/** The summary answers with `summary`; per-line reads see a healthy Steins;Gate and an unreadable β. */
function stubBackend(summary: () => Response) {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    if (url.startsWith('/api/worldlines/summary')) return summary();
    const beta = url.includes('worldline=beta');
    if (url.startsWith('/api/conversations?')) {
      return beta ? json(503, { detail: { code: 'chart_unavailable' } })
        : json(200, [{ last_active_at: '2026-11-09T08:00:00Z' }, { last_active_at: '2026-11-01T08:00:00Z' }]);
    }
    if (url.startsWith('/api/memory/chart?')) {
      return beta ? json(503, { detail: { code: 'chart_unavailable' } }) : json(200, { counts: { facts: 3, experiences: 1 } });
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));
  return { fallbackUrls: () => urls.filter((url) => !url.startsWith('/api/worldlines/summary')), urls };
}

afterEach(() => vi.unstubAllGlobals());

describe('useWorldlineLedger summary 503 handling', () => {
  it('skips the per-line fallback only while data is relocating', async () => {
    const backend = stubBackend(() => json(503, { detail: { code: 'data_relocating' } }));
    const { result } = renderHook(() => useWorldlineLedger('session-one', true));
    await waitFor(() => expect(backend.urls).toHaveLength(1));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    expect(backend.fallbackUrls()).toHaveLength(0);
    expect(result.current).toEqual({});
  });

  it.each([
    ['chart_unavailable', () => json(503, { detail: { code: 'chart_unavailable' } })],
    ['a non-JSON body', () => new Response('Service Unavailable', { status: 503 })],
  ])('falls back line by line on a 503 with %s and shows the healthy line', async (_label, summary) => {
    const backend = stubBackend(summary);
    const { result } = renderHook(() => useWorldlineLedger('session-one', true));
    await waitFor(() => expect(result.current.steins_gate).toBeDefined());
    expect(backend.fallbackUrls()).toHaveLength(6);
    expect(result.current).toEqual({
      steins_gate: { conversations: 2, memories: 8, last: '2026-11-09T08:00:00Z' },
      beta: { conversations: null, memories: null, last: null },
    });
  });
});
