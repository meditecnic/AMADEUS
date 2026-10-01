import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MemoryResultOutline } from './MemoryResultOutline';
import { deriveOverviewPresentation } from './overviewPresentation';
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

describe('MemoryResultOutline', () => {
  afterEach(() => cleanup());

  it('lists each result_id once and marks supporting rows as context', () => {
    const onSelect = vi.fn();
    const plan = deriveOverviewPresentation(projection, {});
    render(
      <MemoryResultOutline
        plan={plan}
        selectedId="fact:1"
        focusedId="fact:1"
        onSelect={onSelect}
        onRove={vi.fn()}
      />,
    );
    expect(screen.getAllByRole('option', { name: '喜欢黑咖啡' })).toHaveLength(1);
    expect(screen.getByRole('option', { name: '上下文 SOUL' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '上下文 咖啡' })).toBeInTheDocument();
    expect(screen.getByText('结果 1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: '上下文 SOUL' }));
    expect(onSelect).toHaveBeenCalledWith('hub');
  });

  it('gives dense LIST a grouped hierarchy while ranked view keeps result_ids unique and ordered', () => {
    const facts = Array.from({ length: 12 }, (_, index) => ({
      kind: 'fact' as const,
      projection_id: `fact:${index + 1}`,
      fact_id: String(index + 1),
      label: `密集事实${index + 1}`,
      is_pinned: false,
      updated_at: '2026-08-19T00:00:00Z',
      topic_id: index < 8 ? 't1' : 't2',
    }));
    const dense: MemoryProjection = {
      ...projection,
      result_ids: facts.map((item) => item.projection_id),
      shown: { nodes: 15, edges: 14, records: 12, results: 12 },
      eligible: { nodes: 15, edges: 14, records: 12, results: 12 },
      nodes: [
        projection.nodes[0],
        projection.nodes[1],
        {
          kind: 'topic',
          projection_id: 'topic:t2',
          topic_id: 't2',
          label: '研究',
          fact_count: 4,
          experience_count: 0,
        },
        ...facts,
      ],
      edges: [
        projection.edges[0],
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t2' },
        ...facts.map((item) => ({
          kind: 'has_topic' as const,
          from: item.projection_id,
          to: item.topic_id === 't1' ? 'topic:t1' : 'topic:t2',
        })),
      ],
    };
    const plan = deriveOverviewPresentation(dense, {});
    render(
      <MemoryResultOutline
        plan={plan}
        selectedId={null}
        focusedId={null}
        onSelect={vi.fn()}
        onRove={vi.fn()}
      />,
    );
    const list = screen.getByTestId('memory-list');
    expect(list).toHaveAttribute('data-browse', 'grouped');
    expect(screen.getByRole('button', { name: '按主题' })).toBeInTheDocument();
    expect(list.querySelectorAll('[data-region="group"]').length).toBeGreaterThan(1);
    fireEvent.click(screen.getByRole('button', { name: '按排序' }));
    expect(list).toHaveAttribute('data-browse', 'ranked');
    const ranked = [...list.querySelectorAll('[data-result-id]')].map((node) => node.getAttribute('data-result-id'));
    expect(ranked).toEqual(dense.result_ids);
    expect(new Set(ranked).size).toBe(12);
  });

  it('roves across open groups, skips folded groups, and reaches supporting context', () => {
    const facts = Array.from({ length: 8 }, (_, index) => ({
      kind: 'fact' as const,
      projection_id: `fact:${index + 1}`,
      fact_id: String(index + 1),
      label: `分组事实${index + 1}`,
      is_pinned: false,
      updated_at: '2026-08-19T00:00:00Z',
      topic_id: index < 4 ? 't1' : 't2',
    }));
    const dense: MemoryProjection = {
      ...projection,
      result_ids: facts.map((item) => item.projection_id),
      shown: { nodes: 11, edges: 10, records: 8, results: 8 },
      eligible: { nodes: 11, edges: 10, records: 8, results: 8 },
      nodes: [
        projection.nodes[0],
        projection.nodes[1],
        {
          kind: 'topic',
          projection_id: 'topic:t2',
          topic_id: 't2',
          label: '研究',
          fact_count: 4,
          experience_count: 0,
        },
        ...facts,
      ],
      edges: [
        projection.edges[0],
        { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t2' },
        ...facts.map((item) => ({
          kind: 'has_topic' as const,
          from: item.projection_id,
          to: item.topic_id === 't1' ? 'topic:t1' : 'topic:t2',
        })),
      ],
    };
    const plan = deriveOverviewPresentation(dense, { expandedTopicIds: ['topic:t1'] });
    const onRove = vi.fn();
    render(
      <MemoryResultOutline
        plan={plan}
        selectedId="fact:4"
        focusedId="fact:4"
        onSelect={vi.fn()}
        onRove={onRove}
      />,
    );
    const coffee = screen.getByText(/咖啡 · 4 条/).closest('details');
    const research = screen.getByText(/研究 · 4 条/).closest('details');
    expect(coffee).toBeTruthy();
    expect(research).toBeTruthy();
    if (coffee && !coffee.open) fireEvent.click(coffee.querySelector('summary') as HTMLElement);
    if (research?.open) fireEvent.click(research.querySelector('summary') as HTMLElement);
    expect(coffee?.open).toBe(true);
    expect(research?.open).toBe(false);
    const lastOpen = screen.getByRole('option', { name: '分组事实4' });
    lastOpen.focus();
    fireEvent.keyDown(lastOpen, { key: 'ArrowDown' });
    expect(onRove).toHaveBeenCalled();
    const next = onRove.mock.calls[0][0];
    expect(['fact:5', 'fact:6', 'fact:7', 'fact:8']).not.toContain(next);
    expect(next).toMatch(/^(hub|topic:)/);
    fireEvent.click(screen.getByRole('button', { name: '按排序' }));
    const ranked = [...screen.getByTestId('memory-list').querySelectorAll('[data-result-id]')]
      .map((node) => node.getAttribute('data-result-id'));
    expect(ranked).toEqual(dense.result_ids);
  });

  it('gives same-label ungrouped rows distinct accessible names', () => {
    const loose: MemoryProjection = {
      ...projection,
      result_ids: ['fact:a', 'fact:b'],
      shown: { nodes: 3, edges: 0, records: 2, results: 2 },
      eligible: { nodes: 3, edges: 0, records: 2, results: 2 },
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
      ],
      edges: [],
    };
    const plan = deriveOverviewPresentation(loose, {});
    render(
      <MemoryResultOutline
        plan={plan}
        selectedId={null}
        focusedId={null}
        onSelect={vi.fn()}
        onRove={vi.fn()}
      />,
    );
    const names = screen.getAllByRole('option')
      .map((node) => node.getAttribute('aria-label') ?? '')
      .filter((name) => name.includes('重复标签') && !name.startsWith('上下文'));
    expect(names).toHaveLength(2);
    expect(new Set(names).size).toBe(2);
    expect(names.every((name) => !name.includes('fact:a') && !name.includes('fact:b'))).toBe(true);
  });

  it('keeps same-label Facts under one Topic distinct in ranked and grouped LIST', () => {
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
    render(
      <MemoryResultOutline
        plan={plan}
        selectedId={null}
        focusedId={null}
        onSelect={vi.fn()}
        onRove={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '按排序' }));
    const ranked = screen.getAllByRole('option')
      .map((node) => node.getAttribute('aria-label') ?? '')
      .filter((name) => name.includes('重复标签') && !name.startsWith('上下文'));
    expect(ranked).toHaveLength(2);
    expect(new Set(ranked).size).toBe(2);
    expect(ranked.every((name) => !/fact:[ab]|[0-9a-f]{8}-[0-9a-f]{4}/i.test(name))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '按主题' }));
    expect(screen.getByTestId('memory-list')).toHaveAttribute('data-browse', 'grouped');
    const grouped = screen.getAllByRole('option')
      .map((node) => node.getAttribute('aria-label') ?? '')
      .filter((name) => name.includes('重复标签') && !name.startsWith('上下文'));
    expect(grouped).toHaveLength(2);
    expect(new Set(grouped).size).toBe(2);
  });

  it('gives same-label supporting evidence distinct accessible names', () => {
    const loose: MemoryProjection = {
      ...projection,
      result_ids: ['fact:1'],
      shown: { nodes: 5, edges: 2, records: 3, results: 1 },
      eligible: { nodes: 5, edges: 2, records: 3, results: 1 },
      nodes: [
        projection.nodes[0],
        projection.nodes[1],
        projection.nodes[2],
        {
          kind: 'fact',
          projection_id: 'fact:s1',
          fact_id: 's1',
          label: '重复标签',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 't1',
        },
        {
          kind: 'fact',
          projection_id: 'fact:s2',
          fact_id: 's2',
          label: '重复标签',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: 't1',
        },
      ],
      edges: [
        ...projection.edges,
        { kind: 'has_topic', from: 'fact:s1', to: 'topic:t1' },
        { kind: 'has_topic', from: 'fact:s2', to: 'topic:t1' },
      ],
    };
    const plan = deriveOverviewPresentation(loose, {});
    render(
      <MemoryResultOutline
        plan={plan}
        selectedId={null}
        focusedId={null}
        onSelect={vi.fn()}
        onRove={vi.fn()}
      />,
    );
    const names = screen.getAllByRole('option')
      .map((node) => node.getAttribute('aria-label') ?? '')
      .filter((name) => name.includes('重复标签') && name.startsWith('上下文'));
    expect(names).toHaveLength(2);
    expect(new Set(names).size).toBe(2);
    expect(names.every((name) => !name.includes('fact:s1') && !name.includes('fact:s2'))).toBe(true);
  });
});
