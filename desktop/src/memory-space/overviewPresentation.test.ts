import { describe, expect, it } from 'vitest';

import {
  accessibleNameById,
  deriveOverviewPresentation,
  displayNodesFromPlan,
  REPRESENTATIVE_CHILD_LIMIT,
  ungroupedNameById,
  visibleOutlineIds,
} from './overviewPresentation';
import type { MemoryProjection, ProjectionNode } from './types';

function hub(): ProjectionNode {
  return {
    kind: 'continuity_hub',
    projection_id: 'hub',
    label_primary: 'AMADEUS',
    label_secondary: 'SOUL',
  };
}

function topic(id: string, label: string, facts = 9, experiences = 0): Extract<ProjectionNode, { kind: 'topic' }> {
  return {
    kind: 'topic',
    projection_id: `topic:${id}`,
    topic_id: id,
    label,
    fact_count: facts,
    experience_count: experiences,
  };
}

function fact(id: string, label: string, topicId: string | null = 't1'): Extract<ProjectionNode, { kind: 'fact' }> {
  return {
    kind: 'fact',
    projection_id: `fact:${id}`,
    fact_id: id,
    label,
    is_pinned: false,
    updated_at: '2026-08-19T00:00:00Z',
    topic_id: topicId,
  };
}

function experience(id: string, label: string): Extract<ProjectionNode, { kind: 'experience' }> {
  return {
    kind: 'experience',
    projection_id: `exp:${id}`,
    experience_id: id,
    label,
    is_pinned: false,
    created_at: '2026-08-19T00:00:00Z',
    updated_at: '2026-08-19T00:00:00Z',
    expires_at: null,
    is_expired: false,
    conversation_id: 'c1',
  };
}

function envelope(over: Partial<MemoryProjection> = {}): MemoryProjection {
  const coffee = topic('t1', '咖啡', 9, 0);
  const children = [1, 2, 3, 4, 5].map((n) => fact(String(n), `事实${n}`, 't1'));
  const stray = experience('u', '无主题经历');
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    view: 'overview',
    projection_version: 'memory-projection-v1',
    generated_at: '2026-08-19T00:00:00Z',
    criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    center: hub(),
    composition: {
      active_facts: 5,
      active_experiences: 1,
      eligible_topics: 1,
      latest_memory_change_at: null,
      person_anchors_supported: false,
    },
    budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
    eligible: { nodes: 8, edges: 6, records: 6, results: 5 },
    shown: { nodes: 8, edges: 6, records: 6, results: 5 },
    truncated: { nodes: false, edges: false, records: true, results: true },
    empty: false,
    result_ids: children.map((item) => item.projection_id),
    nodes: [hub(), coffee, ...children, stray],
    edges: [
      { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
      ...children.map((item) => ({ kind: 'has_topic' as const, from: item.projection_id, to: 'topic:t1' })),
    ],
    ...over,
  };
}

describe('deriveOverviewPresentation', () => {
  it('keeps primaryResults identical to result_ids in identity, uniqueness, and order', () => {
    const projection = envelope();
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    expect(plan.primaryResults.map((row) => row.id)).toEqual(projection.result_ids);
    expect(new Set(plan.primaryResults.map((row) => row.id)).size).toBe(projection.result_ids.length);
    expect(plan.primaryResults.map((row) => row.rank)).toEqual([1, 2, 3, 4, 5]);
    expect(plan.envelope).toBe(projection);
  });

  it('keeps ungrouped evidence in LIST identity without stacking it on rest Overview', () => {
    const projection = envelope();
    const rest = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    expect(rest.ungroupedIds).toContain('exp:u');
    expect(rest.visualPlan.visibleEvidenceIds).not.toContain('exp:u');
    expect(rest.primaryResults.some((row) => row.id === 'exp:u' || rest.ungroupedIds.includes('exp:u'))).toBe(true);
    const focused = deriveOverviewPresentation(projection, { expandedTopicIds: [], selectedId: 'exp:u' });
    expect(focused.visualPlan.visibleEvidenceIds).toContain('exp:u');
  });

  it('separates supporting context from primary results and does not count them as results', () => {
    const projection = envelope();
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const supportIds = plan.supportingContext.map((row) => row.id);
    expect(supportIds).toContain('hub');
    expect(supportIds).toContain('topic:t1');
    expect(supportIds).toContain('exp:u');
    expect(supportIds.some((id) => projection.result_ids.includes(id))).toBe(false);
    expect(plan.primaryResults).toHaveLength(projection.shown.results);
    expect(plan.supportingContext.every((row) => row.context)).toBe(true);
  });

  it('assigns Topic children only from has_topic edges, not topic_id or composition count', () => {
    const projection = envelope({
      nodes: [
        hub(),
        topic('t1', '咖啡', 99, 0),
        fact('1', '有边', 't1'),
        fact('2', '无边却带 topic_id', 't1'),
      ],
      result_ids: ['fact:1', 'fact:2'],
      edges: [
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
      ],
      shown: { nodes: 4, edges: 2, records: 2, results: 2 },
      eligible: { nodes: 4, edges: 2, records: 2, results: 2 },
    });
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    const group = plan.topicGroups.find((item) => item.topicProjectionId === 'topic:t1');
    expect(group?.admittedChildIds).toEqual(['fact:1']);
    expect(group?.childResultIds).toEqual(['fact:1']);
    expect(plan.ungroupedIds).toContain('fact:2');
    expect(group?.localRemainder).toBeLessThan(99);
  });

  it('keeps nodes without has_topic ungrouped and contextual', () => {
    const plan = deriveOverviewPresentation(envelope(), { expandedTopicIds: [] });
    expect(plan.ungroupedIds).toEqual(['exp:u']);
    expect(plan.supportingContext.some((row) => row.id === 'exp:u')).toBe(true);
  });

  it('does not treat local remainder as backend truncation', () => {
    const projection = envelope();
    const collapsed = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const group = collapsed.topicGroups[0];
    expect(group.localRemainder).toBe(group.admittedChildIds.length);
    expect(collapsed.backendTruncation.truncated).toEqual(projection.truncated);
    expect(collapsed.backendTruncation.shown).toEqual(projection.shown);
    expect(collapsed.backendTruncation.eligible).toEqual(projection.eligible);
    expect(group.localRemainder).not.toBe(projection.eligible.results - projection.shown.results);
  });

  it('limits representative children without mutating the envelope', () => {
    const projection = envelope();
    const before = JSON.stringify(projection);
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    const group = plan.topicGroups[0];
    expect(group.representativeIds.length).toBe(REPRESENTATIVE_CHILD_LIMIT);
    expect(group.localRemainder).toBe(group.admittedChildIds.length - group.representativeIds.length);
    expect(JSON.stringify(projection)).toBe(before);
    expect(plan.primaryResults.map((row) => row.id)).toEqual(projection.result_ids);
  });

  it('keeps topicGroups as references that do not overwrite canonical rank', () => {
    const projection = envelope({
      result_ids: ['fact:5', 'fact:1', 'fact:3'],
      shown: { nodes: 8, edges: 6, records: 6, results: 3 },
      eligible: { nodes: 8, edges: 6, records: 6, results: 3 },
    });
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    expect(plan.primaryResults.map((row) => row.id)).toEqual(['fact:5', 'fact:1', 'fact:3']);
    expect(plan.topicGroups[0].childResultIds).toEqual(['fact:5', 'fact:1', 'fact:3']);
    expect(plan.primaryResults.map((row) => row.rank)).toEqual([1, 2, 3]);
  });

  it('fails closed when any result_id lacks a canonical node', () => {
    const projection = envelope({
      result_ids: ['fact:1', 'fact:missing', 'fact:2'],
      shown: { nodes: 8, edges: 6, records: 6, results: 3 },
      eligible: { nodes: 8, edges: 6, records: 6, results: 3 },
    });
    const plan = deriveOverviewPresentation(projection, {});
    expect(plan.valid).toBe(false);
    expect(plan.malformedResultIds).toEqual(['fact:missing']);
    expect(plan.primaryResults).toEqual([]);
    expect(plan.envelope).toBe(projection);
    expect(plan.envelope.result_ids).toEqual(['fact:1', 'fact:missing', 'fact:2']);
    expect(plan.backendTruncation.shown.results).toBe(3);
    expect(plan.primaryResults.map((row) => row.id)).not.toEqual(['fact:1', 'fact:2']);
    expect(plan.primaryResults.every((row) => row.kind !== 'unknown')).toBe(true);
  });

  it('fails closed when a result_id is the continuity hub', () => {
    const projection = envelope({
      result_ids: ['hub', 'fact:1'],
      shown: { nodes: 8, edges: 6, records: 6, results: 2 },
      eligible: { nodes: 8, edges: 6, records: 6, results: 2 },
    });
    const plan = deriveOverviewPresentation(projection, {});
    expect(plan.valid).toBe(false);
    expect(plan.malformedResultIds).toEqual(['hub']);
    expect(plan.primaryResults).toEqual([]);
  });

  it('fails closed when result_ids repeat a Fact id', () => {
    const projection = envelope({
      result_ids: ['fact:1', 'fact:1', 'fact:2'],
      shown: { nodes: 8, edges: 6, records: 6, results: 3 },
      eligible: { nodes: 8, edges: 6, records: 6, results: 3 },
    });
    const before = JSON.stringify(projection);
    const plan = deriveOverviewPresentation(projection, {});
    expect(plan.valid).toBe(false);
    expect(plan.primaryResults).toEqual([]);
    expect(plan.malformedResultIds).toEqual(['fact:1', 'fact:1']);
    expect(plan.envelope).toBe(projection);
    expect(plan.envelope.result_ids).toEqual(['fact:1', 'fact:1', 'fact:2']);
    expect(plan.backendTruncation.shown.results).toBe(3);
    expect(JSON.stringify(projection)).toBe(before);
  });

  it('fails closed when result_ids repeat a Topic id', () => {
    const projection = envelope({
      result_ids: ['topic:t1', 'fact:1', 'topic:t1'],
      shown: { nodes: 8, edges: 6, records: 6, results: 3 },
      eligible: { nodes: 8, edges: 6, records: 6, results: 3 },
    });
    const plan = deriveOverviewPresentation(projection, {});
    expect(plan.valid).toBe(false);
    expect(plan.primaryResults).toEqual([]);
    expect(plan.malformedResultIds).toEqual(['topic:t1', 'topic:t1']);
    expect(plan.envelope.result_ids).toEqual(['topic:t1', 'fact:1', 'topic:t1']);
  });

  it('exposes no rematch or fabricate path: adapters only receive envelope ids', () => {
    const projection = envelope();
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    const known = new Set(projection.nodes.map((node) => node.projection_id));
    for (const id of [
      ...plan.primaryResults.map((row) => row.id),
      ...plan.supportingContext.map((row) => row.id),
      ...plan.ungroupedIds,
      ...plan.topicGroups.flatMap((group) => group.admittedChildIds),
    ]) {
      expect(known.has(id)).toBe(true);
    }
  });

  it('does not let a nonempty query add every result_id to the visual plan', () => {
    const projection = envelope();
    const plan = deriveOverviewPresentation(projection, {
      expandedTopicIds: ['topic:t1'],
      query: '事实',
    });
    expect(plan.primaryResults.map((row) => row.id)).toEqual(projection.result_ids);
    expect(plan.visualPlan.visibleEvidenceIds).toEqual(['fact:1', 'fact:2', 'fact:3', 'exp:u']);
    expect(plan.visualPlan.visibleEvidenceIds).not.toContain('fact:5');
    expect(plan.envelope.result_ids).toEqual(projection.result_ids);
    expect(plan.backendTruncation.shown).toEqual(projection.shown);
    const located = deriveOverviewPresentation(projection, {
      expandedTopicIds: ['topic:t1'],
      query: '事实',
      selectedId: 'fact:5',
      focusedId: 'fact:4',
    });
    expect(located.visualPlan.visibleEvidenceIds).toEqual(
      expect.arrayContaining(['fact:1', 'fact:2', 'fact:3', 'fact:4', 'fact:5']),
    );
    expect(located.primaryResults.map((row) => row.id)).toEqual(projection.result_ids);
  });

  it('gives same-label supporting evidence distinct honest names', () => {
    const projection = envelope({
      nodes: [
        hub(),
        topic('t1', '咖啡', 9, 0),
        fact('1', '事实1', 't1'),
        fact('s1', '重复标签', 't1'),
        fact('s2', '重复标签', 't1'),
      ],
      result_ids: ['fact:1'],
      edges: [
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:s1', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:s2', to: 'topic:t1' },
      ],
      shown: { nodes: 5, edges: 4, records: 3, results: 1 },
      eligible: { nodes: 5, edges: 4, records: 3, results: 1 },
    });
    const plan = deriveOverviewPresentation(projection, {});
    expect(plan.primaryResults.map((row) => row.id)).toEqual(['fact:1']);
    expect(plan.supportingContext.map((row) => row.id)).toEqual(expect.arrayContaining(['fact:s1', 'fact:s2']));
    const names = ungroupedNameById(plan);
    expect(names.get('fact:s1')).not.toBe(names.get('fact:s2'));
    expect(names.get('fact:s1')).toMatch(/上下文/);
    expect(names.get('fact:s2')).toMatch(/上下文/);
    expect(names.get('fact:s1')).toContain('事实');
    expect(names.get('fact:s1')).not.toMatch(/fact:s/);
    expect(names.get('fact:s2')).not.toMatch(/fact:s/);
  });

  it('distinguishes same-label Facts under one Topic without UUID names', () => {
    const projection = envelope({
      nodes: [
        hub(),
        topic('t1', '咖啡', 2, 0),
        fact('a', '重复标签', 't1'),
        fact('b', '重复标签', 't1'),
      ],
      result_ids: ['fact:a', 'fact:b'],
      edges: [
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:a', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:b', to: 'topic:t1' },
      ],
      shown: { nodes: 4, edges: 3, records: 2, results: 2 },
      eligible: { nodes: 4, edges: 3, records: 2, results: 2 },
    });
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    const names = accessibleNameById(plan);
    expect(names.get('fact:a')).not.toBe(names.get('fact:b'));
    expect(names.get('fact:a')).toContain('重复标签');
    expect(names.get('fact:b')).toContain('重复标签');
    expect(names.get('fact:a')).toContain('事实');
    expect(names.get('fact:a')).not.toMatch(/fact:[ab]|[0-9a-f]{8}-[0-9a-f]{4}/i);
    expect(names.get('fact:b')).not.toMatch(/fact:[ab]|[0-9a-f]{8}-[0-9a-f]{4}/i);
  });

  it('derives 3D display membership from the plan instead of query hits', () => {
    const plan = deriveOverviewPresentation(envelope(), { expandedTopicIds: ['topic:t1'] });
    const evidence = displayNodesFromPlan(plan)
      .filter((node) => node.kind === 'fact' || node.kind === 'experience')
      .map((node) => node.id)
      .sort();
    expect(evidence).toEqual([...plan.visualPlan.visibleEvidenceIds].sort());
    expect(evidence).toContain('fact:1');
    expect(evidence).not.toContain('fact:5');
  });

  it('lists only open grouped options before supporting context', () => {
    const plan = deriveOverviewPresentation(envelope({
      result_ids: ['fact:1', 'fact:2', 'fact:3', 'fact:4', 'fact:5'],
    }), { expandedTopicIds: ['topic:t1'] });
    const visible = visibleOutlineIds(plan, 'grouped', ['topic:t1']);
    expect(visible.slice(0, 5)).toEqual(['fact:1', 'fact:2', 'fact:3', 'fact:4', 'fact:5']);
    expect(visible).toContain('hub');
    expect(visible.indexOf('hub')).toBeGreaterThan(visible.indexOf('fact:5'));
    const folded = visibleOutlineIds(plan, 'grouped', []);
    expect(folded.filter((id) => id.startsWith('fact:'))).toEqual([]);
    expect(folded[0]).toBe('hub');
  });
});
