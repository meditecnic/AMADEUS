import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ConstellationStage } from './ConstellationStage';
import { deriveOverviewPresentation } from './overviewPresentation';
import type { MemoryProjection } from './types';

const facts = [1, 2, 3, 4, 5].map((n) => ({
  kind: 'fact' as const,
  projection_id: `fact:${n}`,
  fact_id: String(n),
  label: `事实${n}`,
  is_pinned: false,
  updated_at: '2026-08-19T00:00:00Z',
  topic_id: 't1',
}));

const projection: MemoryProjection = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  view: 'overview',
  projection_version: 'memory-projection-v1',
  generated_at: '2026-08-19T00:00:00Z',
  criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
  center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
  composition: {
    active_facts: 5,
    active_experiences: 0,
    eligible_topics: 1,
    latest_memory_change_at: null,
    person_anchors_supported: false,
  },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 7, edges: 6, records: 5, results: 5 },
  shown: { nodes: 7, edges: 6, records: 5, results: 5 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: facts.map((item) => item.projection_id),
  nodes: [
    { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 5, experience_count: 0 },
    ...facts,
  ],
  edges: [
    { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
    ...facts.map((item) => ({ kind: 'has_topic' as const, from: item.projection_id, to: 'topic:t1' })),
  ],
};

describe('ConstellationStage presentation display', () => {
  it('uses the shared plan evidence set even when query would reveal every result', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    const { rerender } = render(
      <ConstellationStage
        plan={plan}
        selectedId={null}
        mode="3d"
        reducedMotion
        expandedTopicIds={['topic:t1']}
        worldline="steins_gate"
        query="事实"
        onSelect={vi.fn()}
      />,
    );
    const first = screen.getByTestId('constellation-stage').getAttribute('data-visible-evidence');
    expect(first?.split(',').filter(Boolean).sort()).toEqual(['fact:1', 'fact:2', 'fact:3']);
    expect(first).not.toContain('fact:5');
    const nextPlan = deriveOverviewPresentation(
      { ...projection, generated_at: '2026-08-19T01:00:00Z' },
      { expandedTopicIds: ['topic:t1'] },
    );
    rerender(
      <ConstellationStage
        plan={nextPlan}
        selectedId={null}
        mode="3d"
        reducedMotion
        expandedTopicIds={['topic:t1']}
        worldline="steins_gate"
        query="事实"
        onSelect={vi.fn()}
      />,
    );
    const next = screen.getByTestId('constellation-stage').getAttribute('data-visible-evidence');
    expect(next?.split(',').filter(Boolean).sort()).toEqual(['fact:1', 'fact:2', 'fact:3']);
    expect(next).not.toContain('fact:5');
  });
});
