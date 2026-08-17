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
});
