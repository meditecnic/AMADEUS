import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CompatibilityView } from './CompatibilityView';
import type { MemoryProjection } from './types';

const projection: MemoryProjection = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  view: 'overview',
  projection_version: 'memory-projection-v1',
  generated_at: '2026-08-19T00:00:00Z',
  criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
  center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
  composition: {
    active_facts: 1,
    active_experiences: 0,
    eligible_topics: 1,
    latest_memory_change_at: null,
    person_anchors_supported: false,
  },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 3, edges: 2, records: 1, results: 1 },
  shown: { nodes: 3, edges: 2, records: 1, results: 1 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: ['fact:1'],
  nodes: [
    { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 1, experience_count: 0 },
    {
      kind: 'fact',
      projection_id: 'fact:1',
      fact_id: '1',
      label: '喜欢黑咖啡',
      is_pinned: false,
      updated_at: '2026-08-14T00:00:00Z',
      topic_id: 't1',
    },
  ],
  edges: [
    { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
    { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
  ],
};

describe('CompatibilityView', () => {
  it('renders the same nodes, edges and result without WebGL', () => {
    const onSelect = vi.fn();
    render(
      <CompatibilityView projection={projection} selectedId="fact:1" onSelect={onSelect} />,
    );
    expect(screen.getByTestId('memory-compatibility')).toHaveAttribute('data-webgl', 'false');
    expect(screen.getByText('兼容视图 / COMPATIBILITY VIEW')).toBeInTheDocument();
    expect(screen.getByText('喜欢黑咖啡')).toBeInTheDocument();
    expect(screen.getByText('SOUL')).toBeInTheDocument();
    expect(document.querySelectorAll('line').length).toBe(2);
  });
});
