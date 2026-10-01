import { describe, expect, it, vi } from 'vitest';

import { applyIfCurrent, fetchMemoryProjection } from './fetchProjection';

describe('fetchMemoryProjection', () => {
  it('calls the live projection entry and discards stale epochs', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        projection_version: 'memory-projection-v1',
        nodes: [],
        edges: [],
        result_ids: [],
      }),
    });
    const result = await fetchMemoryProjection(
      {
        sessionId: 's1',
        worldline: 'steins_gate',
        identityMode: 'self',
        query: '咖啡',
        epoch: 4,
      },
      fetcher as unknown as typeof fetch,
    );
    expect(String(fetcher.mock.calls[0][0])).toContain('/api/memory/graph/projection?');
    expect(String(fetcher.mock.calls[0][0])).toContain('session_id=s1');
    expect(String(fetcher.mock.calls[0][0])).toContain('view=overview');
    expect(String(fetcher.mock.calls[0][0])).toContain('query=');
    expect(result.epoch).toBe(4);
    expect(applyIfCurrent(4, result.epoch, result.projection)).toBe(result.projection);
    expect(applyIfCurrent(5, result.epoch, result.projection)).toBeUndefined();
  });

  it('forwards kind, topic, pin and time criteria without rematching', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ projection_version: 'memory-projection-v1' }),
    });
    await fetchMemoryProjection(
      {
        sessionId: 's1',
        worldline: 'steins_gate',
        identityMode: 'okabe',
        query: '拿铁',
        kinds: ['fact'],
        topicId: 'topic-1',
        pinnedOnly: true,
        updatedFrom: '2026-01-01T00:00:00Z',
        updatedTo: '2026-02-01T00:00:00Z',
        epoch: 1,
      },
      fetcher as unknown as typeof fetch,
    );
    const url = String(fetcher.mock.calls[0][0]);
    expect(url).toContain('kind=fact');
    expect(url).toContain('topic_id=topic-1');
    expect(url).toContain('pinned_only=true');
    expect(url).toContain('updated_from=2026-01-01T00%3A00%3A00Z');
    expect(url).toContain('updated_to=2026-02-01T00%3A00%3A00Z');
  });

  it('classifies 503 as unavailable without leaking body text', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: { code: 'projection_unavailable', message: 'C:\\secret.db locked' } }),
    });
    await expect(fetchMemoryProjection(
      { sessionId: 's1', worldline: 'steins_gate', identityMode: 'self', epoch: 1 },
      fetcher as unknown as typeof fetch,
    )).rejects.toMatchObject({ kind: 'unavailable', code: 'projection_unavailable', message: 'projection_unavailable' });
  });
});
