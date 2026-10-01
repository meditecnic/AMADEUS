import { describe, expect, it } from 'vitest';

import { EMPTY_CRITERIA } from './criteria';
import { classifyOverviewState } from './overviewState';
import type { MemoryProjection } from './types';

function envelope(over: Partial<MemoryProjection> = {}): MemoryProjection {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    view: 'overview',
    projection_version: 'memory-projection-v1',
    generated_at: '2026-08-19T00:00:00Z',
    criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    center: {
      kind: 'continuity_hub',
      projection_id: 'hub',
      label_primary: 'AMADEUS',
      label_secondary: 'SOUL',
    },
    composition: {
      active_facts: 0,
      active_experiences: 0,
      eligible_topics: 0,
      latest_memory_change_at: null,
      person_anchors_supported: false,
    },
    budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
    eligible: { nodes: 1, edges: 0, records: 0, results: 0 },
    shown: { nodes: 1, edges: 0, records: 0, results: 0 },
    truncated: { nodes: false, edges: false, records: false, results: false },
    empty: true,
    result_ids: [],
    nodes: [],
    edges: [],
    ...over,
  };
}

describe('classifyOverviewState', () => {
  it('does not invent records while loading', () => {
    const state = classifyOverviewState({
      loading: true,
      envelope: null,
      error: null,
      requestedCriteria: EMPTY_CRITERIA,
    });
    expect(state.phase).toBe('loading');
    expect(state.envelope).toBeNull();
    expect(state.copy).toContain('读取');
  });

  it('separates true-empty from criteria-zero using composition and criteria', () => {
    const vacant = classifyOverviewState({
      loading: false,
      envelope: envelope(),
      error: null,
    });
    expect(vacant.phase).toBe('true-empty');
    expect(vacant.copy).toContain('长期记忆');

    const filtered = classifyOverviewState({
      loading: false,
      envelope: envelope({
        empty: true,
        composition: {
          active_facts: 4,
          active_experiences: 0,
          eligible_topics: 2,
          latest_memory_change_at: null,
          person_anchors_supported: false,
        },
        criteria: { query: '拿铁', kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
      }),
      error: null,
    });
    expect(filtered.phase).toBe('criteria-empty');
    expect(filtered.copy).toContain('没有匹配');
    expect(filtered.copy).not.toContain('长期记忆');

    const leftover = classifyOverviewState({
      loading: false,
      envelope: envelope({
        empty: true,
        composition: {
          active_facts: 4,
          active_experiences: 0,
          eligible_topics: 2,
          latest_memory_change_at: null,
          person_anchors_supported: false,
        },
      }),
      error: null,
    });
    expect(leftover.phase).toBe('true-empty');
    expect(leftover.copy).not.toContain('没有匹配');
  });

  it('keeps a trusted snapshot labeled when unavailable', () => {
    const prior = envelope({ empty: false, result_ids: ['fact:1'] });
    const state = classifyOverviewState({
      loading: false,
      envelope: prior,
      previousTruth: prior,
      error: { kind: 'unavailable', code: 'projection_unavailable' },
    });
    expect(state.phase).toBe('unavailable');
    expect(state.stale).toBe(true);
    expect(state.envelope?.result_ids).toEqual(['fact:1']);
    expect(state.copy).toContain('暂时不可用');
    expect(state.showRetry).toBe(true);
  });
});
