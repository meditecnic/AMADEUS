import { describe, expect, it, vi } from 'vitest';

import { EMPTY_CRITERIA } from './criteria';
import { createProjectionController } from './projectionController';
import type { MemoryProjection } from './types';

const sg = { sessionId: 's', worldline: 'steins_gate' as const, identityMode: 'okabe' as const };
const beta = { sessionId: 's', worldline: 'beta' as const, identityMode: 'okabe' as const };

function envelope(label: string): MemoryProjection {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    view: 'overview',
    projection_version: 'memory-projection-v1',
    generated_at: '2026-08-19T00:00:00Z',
    criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    composition: {
      active_facts: 1,
      active_experiences: 0,
      eligible_topics: 0,
      latest_memory_change_at: null,
      person_anchors_supported: false,
    },
    budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
    eligible: { nodes: 2, edges: 1, records: 1, results: 1 },
    shown: { nodes: 2, edges: 1, records: 1, results: 1 },
    truncated: { nodes: false, edges: false, records: false, results: false },
    empty: false,
    result_ids: [label],
    nodes: [],
    edges: [],
  };
}

describe('createProjectionController', () => {
  it('ignores a late response after a newer epoch and aborts the superseded request', async () => {
    let releaseOld: ((value: unknown) => void) | undefined;
    const signals: Array<AbortSignal | undefined> = [];
    const fetcher = vi.fn((url: string, init?: RequestInit) => {
      signals.push(init?.signal);
      if (String(url).includes('worldline=beta')) {
        return Promise.resolve({ ok: true, json: async () => envelope('beta') });
      }
      return new Promise((resolve) => {
        releaseOld = resolve;
      });
    });
    const controller = createProjectionController();
    const first = controller.load(sg, EMPTY_CRITERIA, fetcher as unknown as typeof fetch);
    const second = await controller.load(beta, EMPTY_CRITERIA, fetcher as unknown as typeof fetch);
    releaseOld?.({ ok: true, json: async () => envelope('stale-sg') });
    const firstResult = await first;
    expect(firstResult.type).toBe('stale');
    expect(second.type).toBe('ok');
    if (second.type === 'ok') expect(second.envelope.result_ids).toEqual(['beta']);
    expect(signals[0]?.aborted).toBe(true);
  });

  it('restores the previous complete scope when a scope switch fails', async () => {
    const fetcher = vi.fn((url: string) => {
      if (String(url).includes('worldline=beta')) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: async () => ({ detail: { code: 'projection_unavailable' } }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => envelope('sg') });
    });
    const controller = createProjectionController();
    const ok = await controller.load(sg, EMPTY_CRITERIA, fetcher as unknown as typeof fetch);
    expect(ok.type).toBe('ok');
    const failed = await controller.load(beta, EMPTY_CRITERIA, fetcher as unknown as typeof fetch);
    expect(failed.type).toBe('error');
    if (failed.type !== 'error') throw new Error('expected restore');
    expect(failed.restore?.scope).toEqual(sg);
    expect(failed.restore?.envelope.result_ids).toEqual(['sg']);
    expect(failed.error.kind).toBe('unavailable');
  });
});
