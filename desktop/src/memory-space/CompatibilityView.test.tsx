import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CompatibilityView } from './CompatibilityView';
import { MemoryResultOutline } from './MemoryResultOutline';
import { accessibleNameById, deriveOverviewPresentation } from './overviewPresentation';
import type { MemoryProjection } from './types';

function activateWithKey(el: HTMLElement, key: string) {
  const allowed = fireEvent.keyDown(el, { key });
  if (allowed) fireEvent.click(el);
}

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

function denseProjection(): MemoryProjection {
  const facts = [1, 2, 3, 4, 5].map((n) => ({
    kind: 'fact' as const,
    projection_id: `fact:${n}`,
    fact_id: String(n),
    label: `事实${n}`,
    is_pinned: false,
    updated_at: '2026-08-19T00:00:00Z',
    topic_id: 't1',
  }));
  return {
    ...projection,
    result_ids: facts.map((item) => item.projection_id),
    shown: { nodes: 7, edges: 6, records: 5, results: 5 },
    eligible: { nodes: 7, edges: 6, records: 5, results: 5 },
    nodes: [projection.nodes[0], projection.nodes[1], ...facts],
    edges: [
      projection.edges[0],
      ...facts.map((item) => ({ kind: 'has_topic' as const, from: item.projection_id, to: 'topic:t1' })),
    ],
  };
}

describe('CompatibilityView', () => {
  afterEach(() => cleanup());

  it('shows SOUL to Topic structure without a permanent evidence caption wall', () => {
    const onSelect = vi.fn();
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    render(
      <CompatibilityView plan={plan} selectedId={null} onSelect={onSelect} />,
    );
    expect(screen.getByTestId('memory-compatibility')).toHaveAttribute('data-webgl', 'false');
    expect(screen.getByText('兼容视图 / COMPATIBILITY VIEW')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '记忆结构' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SOUL' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上下文 咖啡' })).toBeInTheDocument();
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
  });

  it('owns a vertical scroll region for dense structure on wide and narrow hosts', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const { rerender } = render(
      <div style={{ width: 1280, height: 720 }}>
        <CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />
      </div>,
    );
    const scroll = screen.getByTestId('memory-compatibility-scroll');
    expect(scroll).toHaveAttribute('data-overflow', 'vertical');
    expect(scroll).toHaveStyle({ overflowY: 'auto', minHeight: '0px', maxHeight: '100%' });
    rerender(
      <div style={{ width: 504, height: 900 }}>
        <CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />
      </div>,
    );
    expect(screen.getByTestId('memory-compatibility-scroll')).toHaveAttribute('data-overflow', 'vertical');
    expect(screen.getByTestId('memory-compatibility-scroll')).toHaveStyle({ overflowY: 'auto' });
  });

  it('locates a selected child that sits outside the representative subset', () => {
    const dense = denseProjection();
    const plan = deriveOverviewPresentation(dense, { expandedTopicIds: ['topic:t1'] });
    expect(plan.topicGroups[0].representativeIds).not.toContain('fact:5');
    expect(plan.topicGroups[0].representativeIds).toHaveLength(3);
    expect(plan.topicGroups[0].admittedChildIds).toContain('fact:5');
    render(
      <CompatibilityView
        plan={plan}
        selectedId="fact:5"
        focusedId="fact:5"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '事实5' })).toBeInTheDocument();
    expect(plan.topicGroups[0].representativeIds).toEqual(['fact:1', 'fact:2', 'fact:3']);
  });

  it('does not invent a SOUL to Topic link without hub_to_anchor', () => {
    const orphan: MemoryProjection = {
      ...projection,
      nodes: [
        projection.nodes[0],
        {
          kind: 'topic',
          projection_id: 'topic:orphan',
          topic_id: 'orphan',
          label: '孤儿主题',
          fact_count: 1,
          experience_count: 0,
        },
        {
          kind: 'fact',
          projection_id: 'fact:1',
          fact_id: '1',
          label: '无锚点事实',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 'orphan',
        },
      ],
      edges: [{ kind: 'has_topic', from: 'fact:1', to: 'topic:orphan' }],
    };
    const plan = deriveOverviewPresentation(orphan, { expandedTopicIds: ['topic:orphan'] });
    const { container } = render(<CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />);
    const soulTopics = container.querySelector('[data-region="soul-topics"]');
    expect(soulTopics?.textContent ?? '').not.toContain('孤儿主题');
    expect(soulTopics?.textContent ?? '').not.toContain('无锚点事实');
    expect(container.querySelector('.memory-compatibility-hub')).toHaveTextContent('SOUL');
    expect(screen.getByRole('button', { name: /孤儿主题/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /无锚点事实/ })).toBeInTheDocument();
    expect(container.querySelector('[data-region="contextual"]')?.textContent ?? '').toContain('孤儿主题');
  });

  it('keeps distinct accessible names for multiple ungrouped items', () => {
    const loose: MemoryProjection = {
      ...projection,
      result_ids: ['exp:a', 'exp:b'],
      shown: { nodes: 3, edges: 0, records: 2, results: 2 },
      eligible: { nodes: 3, edges: 0, records: 2, results: 2 },
      nodes: [
        projection.nodes[0],
        {
          kind: 'experience',
          projection_id: 'exp:a',
          experience_id: 'a',
          label: '散落经历甲',
          is_pinned: false,
          created_at: '2026-08-19T00:00:00Z',
          updated_at: '2026-08-19T00:00:00Z',
          expires_at: null,
          is_expired: false,
          conversation_id: 'c1',
        },
        {
          kind: 'experience',
          projection_id: 'exp:b',
          experience_id: 'b',
          label: '散落经历乙',
          is_pinned: false,
          created_at: '2026-08-19T00:00:00Z',
          updated_at: '2026-08-19T00:00:00Z',
          expires_at: null,
          is_expired: false,
          conversation_id: 'c2',
        },
      ],
      edges: [],
    };
    const plan = deriveOverviewPresentation(loose, {});
    render(<CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByRole('button', { name: '未归组 经历 散落经历甲 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '未归组 经历 散落经历乙 1' })).toBeInTheDocument();
  });

  it('gives same-label ungrouped facts and experiences distinct honest names', () => {
    const loose: MemoryProjection = {
      ...projection,
      result_ids: ['fact:a', 'fact:b', 'exp:a', 'exp:b'],
      shown: { nodes: 5, edges: 0, records: 4, results: 4 },
      eligible: { nodes: 5, edges: 0, records: 4, results: 4 },
      nodes: [
        projection.nodes[0],
        {
          kind: 'fact',
          projection_id: 'fact:a',
          fact_id: 'a',
          label: '重复标签',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: null,
        },
        {
          kind: 'fact',
          projection_id: 'fact:b',
          fact_id: 'b',
          label: '重复标签',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: null,
        },
        {
          kind: 'experience',
          projection_id: 'exp:a',
          experience_id: 'a',
          label: '重复标签',
          is_pinned: false,
          created_at: '2026-08-19T00:00:00Z',
          updated_at: '2026-08-19T00:00:00Z',
          expires_at: null,
          is_expired: false,
          conversation_id: 'c1',
        },
        {
          kind: 'experience',
          projection_id: 'exp:b',
          experience_id: 'b',
          label: '重复标签',
          is_pinned: false,
          created_at: '2026-08-19T00:00:00Z',
          updated_at: '2026-08-19T00:00:00Z',
          expires_at: null,
          is_expired: false,
          conversation_id: 'c2',
        },
      ],
      edges: [],
    };
    const plan = deriveOverviewPresentation(loose, {});
    render(<CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />);
    const names = screen.getAllByRole('button')
      .map((node) => node.getAttribute('aria-label') ?? '')
      .filter((name) => name.includes('重复标签'));
    expect(names).toHaveLength(4);
    expect(new Set(names).size).toBe(4);
    expect(names.every((name) => name.startsWith('未归组'))).toBe(true);
    expect(names.some((name) => name.includes('事实'))).toBe(true);
    expect(names.some((name) => name.includes('经历'))).toBe(true);
    expect(names.every((name) => !/fact:[ab]|exp:[ab]/.test(name))).toBe(true);
    expect(names).not.toContain('未归组 重复标签');
  });

  it('distinguishes same-label Facts under one Topic without UUID names', () => {
    const twins: MemoryProjection = {
      ...projection,
      result_ids: ['fact:a', 'fact:b'],
      shown: { nodes: 4, edges: 3, records: 2, results: 2 },
      eligible: { nodes: 4, edges: 3, records: 2, results: 2 },
      nodes: [
        projection.nodes[0],
        projection.nodes[1],
        {
          kind: 'fact',
          projection_id: 'fact:a',
          fact_id: 'a',
          label: '重复标签',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 't1',
        },
        {
          kind: 'fact',
          projection_id: 'fact:b',
          fact_id: 'b',
          label: '重复标签',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 't1',
        },
      ],
      edges: [
        projection.edges[0],
        { kind: 'has_topic', from: 'fact:a', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:b', to: 'topic:t1' },
      ],
    };
    const plan = deriveOverviewPresentation(twins, { expandedTopicIds: ['topic:t1'] });
    render(<CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />);
    const names = screen.getAllByRole('button')
      .map((node) => node.getAttribute('aria-label') || (node.textContent ?? '').trim())
      .filter((name) => name.includes('重复标签'));
    expect(names).toHaveLength(2);
    expect(new Set(names).size).toBe(2);
    expect(names.every((name) => !/fact:[ab]|[0-9a-f]{8}-[0-9a-f]{4}/i.test(name))).toBe(true);
  });

  it('does not advertise an unimplemented ARIA tree keyboard contract', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    render(<CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.queryByRole('tree')).toBeNull();
    expect(screen.queryByRole('treeitem')).toBeNull();
    expect(screen.getByRole('region', { name: '记忆结构' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SOUL' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上下文 咖啡' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '喜欢黑咖啡' })).toBeInTheDocument();
  });

  it('lets native Tab, Enter, and Space focus and select Topic and evidence', () => {
    const onSelect = vi.fn();
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    render(<CompatibilityView plan={plan} selectedId={null} onSelect={onSelect} />);
    const soul = screen.getByRole('button', { name: 'SOUL' });
    const topic = screen.getByRole('button', { name: '上下文 咖啡' });
    const evidence = screen.getByRole('button', { name: '喜欢黑咖啡' });
    expect(soul.tabIndex).toBeGreaterThanOrEqual(0);
    expect(topic.tabIndex).toBeGreaterThanOrEqual(0);
    expect(evidence.tabIndex).toBeGreaterThanOrEqual(0);
    topic.focus();
    expect(document.activeElement).toBe(topic);
    activateWithKey(topic, 'Enter');
    expect(onSelect).toHaveBeenCalledWith('topic:t1');
    evidence.focus();
    expect(document.activeElement).toBe(evidence);
    activateWithKey(evidence, ' ');
    expect(onSelect).toHaveBeenCalledWith('fact:1');
  });

  it('exposes the current Compatibility selection with a valid native-button state', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    render(<CompatibilityView plan={plan} selectedId="topic:t1" onSelect={vi.fn()} />);

    const selected = screen.getByRole('button', { name: '上下文 咖啡' });
    expect(selected).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: 'SOUL' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('button', { name: '喜欢黑咖啡' })).not.toHaveAttribute('aria-current');
    expect(screen.getAllByRole('button').every((button) => !button.hasAttribute('aria-selected'))).toBe(true);
  });

  it('reports Topic expansion from the child list actually rendered', () => {
    const collapsed = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const view = render(
      <CompatibilityView plan={collapsed} selectedId={null} focusedId={null} onSelect={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '上下文 咖啡' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();

    view.rerender(
      <CompatibilityView plan={collapsed} selectedId="fact:1" focusedId="fact:1" onSelect={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '上下文 咖啡' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '喜欢黑咖啡' })).toBeInTheDocument();

    const orphanProjection: MemoryProjection = {
      ...projection,
      edges: [{ kind: 'has_topic', from: 'fact:1', to: 'topic:t1' }],
    };
    const orphanPlan = deriveOverviewPresentation(orphanProjection, { expandedTopicIds: [] });
    view.rerender(
      <CompatibilityView plan={orphanPlan} selectedId="fact:1" focusedId="fact:1" onSelect={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '上下文 咖啡' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '喜欢黑咖啡' })).toBeInTheDocument();
  });

  it('gives same-label anchored Topics distinct shared names without leaking ids', () => {
    const twins: MemoryProjection = {
      ...projection,
      result_ids: ['fact:1', 'fact:2'],
      shown: { nodes: 5, edges: 4, records: 2, results: 2 },
      eligible: { nodes: 5, edges: 4, records: 2, results: 2 },
      nodes: [
        projection.nodes[0],
        {
          kind: 'topic',
          projection_id: 'topic:t1',
          topic_id: 't1',
          label: '实验',
          fact_count: 1,
          experience_count: 0,
        },
        {
          kind: 'topic',
          projection_id: 'topic:t2',
          topic_id: 't2',
          label: '实验',
          fact_count: 1,
          experience_count: 0,
        },
        {
          kind: 'fact',
          projection_id: 'fact:1',
          fact_id: '1',
          label: '第一条实验记录',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 't1',
        },
        {
          kind: 'fact',
          projection_id: 'fact:2',
          fact_id: '2',
          label: '第二条实验记录',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 't2',
        },
      ],
      edges: [
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t2' },
        { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:2', to: 'topic:t2' },
      ],
    };
    const plan = deriveOverviewPresentation(twins, {});
    const shared = accessibleNameById(plan);
    expect(shared.get('topic:t1')).not.toBe(shared.get('topic:t2'));
    render(
      <>
        <CompatibilityView plan={plan} selectedId={null} onSelect={vi.fn()} />
        <MemoryResultOutline
          plan={plan}
          selectedId={null}
          focusedId={null}
          onSelect={vi.fn()}
          onRove={vi.fn()}
        />
      </>,
    );
    const soulTopics = document.querySelector('[data-region="soul-topics"]');
    expect(soulTopics).toBeTruthy();
    const topicNames = within(soulTopics as HTMLElement)
      .getAllByRole('button')
      .filter((node) => node.hasAttribute('aria-expanded'))
      .map((node) => node.getAttribute('aria-label') || (node.textContent ?? '').trim());
    expect(topicNames).toHaveLength(2);
    expect(new Set(topicNames).size).toBe(2);
    expect(topicNames.every((name) => name.includes('实验'))).toBe(true);
    expect(topicNames.every((name) => name.includes('主题'))).toBe(true);
    expect(topicNames.every((name) => !/topic:t[12]|[0-9a-f]{8}-[0-9a-f]{4}/i.test(name))).toBe(true);
    expect(topicNames).toEqual([shared.get('topic:t1'), shared.get('topic:t2')]);
    expect(screen.getByRole('option', { name: shared.get('topic:t1') })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: shared.get('topic:t2') })).toBeInTheDocument();
  });
});
