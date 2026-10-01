import { describe, expect, it } from 'vitest';

import { readingCardFromNode } from './readingContent';
import type { MemoryProjection } from './types';

const factNode = {
  kind: 'fact' as const,
  projection_id: 'fact:1',
  fact_id: '1',
  label: '喜欢黑咖啡',
  is_pinned: false,
  updated_at: '2026-08-14T00:00:00Z',
  topic_id: 't1' as string | null,
};

const envelope: MemoryProjection = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  view: 'overview',
  projection_version: 'memory-projection-v1',
  generated_at: '2026-08-14T00:00:00Z',
  criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
  center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
  composition: { active_facts: 1, active_experiences: 0, eligible_topics: 1, latest_memory_change_at: null, person_anchors_supported: false },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 3, edges: 2, records: 1, results: 1 },
  shown: { nodes: 3, edges: 2, records: 1, results: 1 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: ['fact:1'],
  nodes: [
    { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 1, experience_count: 0 },
    factNode,
  ],
  edges: [
    { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
    { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
  ],
};

describe('readingCardFromNode', () => {
  it('does not duplicate the title and humanizes time for evidence kinds', () => {
    const card = readingCardFromNode(factNode, envelope);
    expect(card?.summary).toBeNull();
    expect(card?.provenance.updatedAtHuman).toBeTruthy();
    expect(card?.provenance.updatedAtHuman).not.toContain('T');
    expect(card?.provenance.updatedAtRaw).toBe('2026-08-14T00:00:00Z');
  });

  it('takes the Topic label from canonical envelope data only', () => {
    expect(readingCardFromNode(factNode, envelope)?.provenance.topicLabel).toBe('咖啡');
    expect(
      readingCardFromNode({ ...factNode, topic_id: null }, envelope)?.provenance.topicLabel,
    ).toBe('未归组');
    expect(
      readingCardFromNode({ ...factNode, topic_id: 't-missing' }, envelope)?.provenance.topicLabel,
    ).toBe('当前投影未提供可显示的归属主题');
    expect(
      readingCardFromNode({ ...factNode, projection_id: 'fact:other' }, envelope)?.provenance.topicLabel,
    ).toBe('当前投影未提供可显示的归属主题');
    expect(readingCardFromNode(factNode)?.provenance.topicLabel).toBeNull();
  });

  it('keeps the topic composition summary and never invents evidence fields', () => {
    const card = readingCardFromNode(envelope.nodes[1], envelope);
    expect(card?.summary).toBe('1 条事实 · 0 条经历');
    expect(card?.provenance.pinned).toBeNull();
  });

  it('keeps experience conversation id out of the default slots', () => {
    const card = readingCardFromNode({
      kind: 'experience' as const,
      projection_id: 'experience:e1',
      experience_id: 'e1',
      label: '一起看了星空',
      is_pinned: false,
      created_at: '2026-08-12T00:00:00Z',
      updated_at: '2026-08-12T00:00:00Z',
      expires_at: null,
      is_expired: false,
      conversation_id: 'c1',
    }, envelope);
    expect(card?.summary).toBeNull();
    expect(card?.provenance.conversationIdRaw).toBe('c1');
    expect(card?.provenance.topicLabel).toBeNull();
  });
});
