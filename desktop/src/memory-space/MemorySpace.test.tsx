import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MemorySpace } from './MemorySpace';
import type { MemoryProjection } from './types';

const projection: MemoryProjection = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  view: 'overview',
  projection_version: 'memory-projection-v1',
  generated_at: '2026-08-14T00:00:00Z',
  criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
  center: {
    kind: 'continuity_hub',
    projection_id: 'hub:continuity:s:steins_gate:okabe',
    label_primary: 'AMADEUS',
    label_secondary: 'SOUL',
  },
  composition: {
    active_facts: 1,
    active_experiences: 0,
    eligible_topics: 1,
    latest_memory_change_at: '2026-08-14T00:00:00Z',
    person_anchors_supported: false,
  },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 3, edges: 2, records: 1, results: 1 },
  shown: { nodes: 3, edges: 2, records: 1, results: 1 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: ['fact:1'],
  nodes: [
    {
      kind: 'continuity_hub',
      projection_id: 'hub:continuity:s:steins_gate:okabe',
      label_primary: 'AMADEUS',
      label_secondary: 'SOUL',
    },
    {
      kind: 'topic',
      projection_id: 'topic:t1',
      topic_id: 't1',
      label: '咖啡',
      fact_count: 1,
      experience_count: 0,
    },
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
    { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:okabe', to: 'topic:t1' },
    { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
  ],
};

const laterProjection: MemoryProjection = {
  ...projection,
  result_ids: ['fact:2'],
  nodes: [
    projection.nodes[0],
    {
      kind: 'fact',
      projection_id: 'fact:2',
      fact_id: '2',
      label: '后来的记忆',
      is_pinned: true,
      updated_at: '2026-08-15T00:00:00Z',
      topic_id: null,
    },
  ],
  edges: [],
  shown: { nodes: 2, edges: 0, records: 1, results: 1 },
};

function mockApi(impl: (url: string) => Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => impl(String(input))));
}

function okJson(body: unknown) {
  return Promise.resolve({
    ok: true,
    json: async () => body,
  });
}

function activateWithEnter(el: HTMLElement) {
  const allowed = fireEvent.keyDown(el, { key: 'Enter' });
  if (allowed) fireEvent.click(el);
}

const originalMatchMedia = window.matchMedia;

describe('MemorySpace', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.matchMedia = originalMatchMedia;
  });

  it('loads one projection into Overview, LIST, and a single G47 reading surface', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 1 });
      }
      return okJson(projection);
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId('memory-loading')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('memory-graph')).toBeInTheDocument());
    expect(
      screen.queryByTestId('constellation-stage') || screen.getByTestId('memory-compatibility'),
    ).toBeTruthy();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-phase', 'overview');
    expect(screen.getByTestId('soul-lockup')).toHaveTextContent('SOUL');
    expect(screen.getByTestId('soul-lockup')).not.toHaveTextContent('AMADEUS');
    expect(screen.getByTestId('memory-space-veil')).toHaveAttribute('data-chrome', 'compact');
    expect(screen.getByTestId('memory-shown-eligible')).toHaveTextContent('当前呈现 1 / 1');
    expect(screen.queryByText('ORIGIN')).toBeNull();
    expect(screen.queryByText('COMING SOON')).toBeNull();
    expect(screen.queryByRole('button', { name: '深入查看' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    expect(screen.getByTestId('memory-list')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.getByRole('option', { name: '上下文 SOUL' })).toBeInTheDocument();
    expect(screen.getAllByRole('option', { name: '喜欢黑咖啡' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('option', { name: '喜欢黑咖啡' }));
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics')).toContain('topic:t1');
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.queryByTestId('reading-kind')).toBeNull();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-kind')).toBeInTheDocument();
    });
    expect(screen.getByTestId('reading-title')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.getByTestId('reading-summary')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('否');
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('2026-08-14T00:00:00Z');
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('t1');
    expect(screen.queryByRole('button', { name: /置顶|删除|编辑/ })).toBeNull();
    expect(screen.queryByTestId('reading-scene') && screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.queryByRole('button', { name: /置顶|删除|编辑/ })).toBeNull();
  });

  it('opens the G27 inspector from SOUL without Origin/Lived/Self or people=0', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 2, processing_count: 1, failed_count: 3 });
      }
      return okJson(projection);
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('memory-graph')).toBeInTheDocument());
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-phase', 'overview');
    fireEvent.click(screen.getByTestId('select-soul'));
    const inspector = screen.getByTestId('soul-inspector');
    expect(inspector).toHaveTextContent('投影中心');
    expect(screen.getByTestId('inspector-latest-change')).toHaveTextContent('2026-08-14T00:00:00Z');
    expect(screen.getByTestId('inspector-projection-counts')).toHaveTextContent('1 / 1');
    expect(screen.getByTestId('inspector-projection-counts')).toHaveTextContent('3 / 3');
    expect(screen.getByTestId('inspector-diagnostics')).toHaveTextContent('memory-projection-v1');
    expect(screen.getByTestId('inspector-diagnostics')).toHaveTextContent('steins_gate');
    await waitFor(() => expect(screen.getByTestId('inspector-jobs')).toHaveTextContent('失败 3'));
    expect(inspector.textContent).not.toMatch(/people\s*=\s*0/i);
    expect(screen.queryByText('ORIGIN')).toBeNull();
    expect(screen.queryByText('LIVED')).toBeNull();
    expect(screen.queryByText('SELF MODEL')).toBeNull();
    expect(screen.queryByTestId('reading-scene')).toBeNull();
    expect(screen.queryByTestId('reading-dom')).toBeNull();
  });

  it('keeps only the latest envelope when two projection fetches overlap', async () => {
    let releaseOld: ((value: unknown) => void) | undefined;
    let releaseNew: ((value: unknown) => void) | undefined;
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('query=')) {
        return new Promise((resolve) => {
          releaseNew = resolve;
        });
      }
      return new Promise((resolve) => {
        releaseOld = resolve;
      });
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => expect(releaseOld).toBeTypeOf('function'));
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.change(screen.getByLabelText('搜索记忆'), { target: { value: '后来' } });
    await waitFor(() => expect(releaseNew).toBeTypeOf('function'));
    releaseNew?.({
      ok: true,
      json: async () => laterProjection,
    });
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => expect(screen.getByRole('option', { name: /后来的记忆/ })).toBeInTheDocument());
    releaseOld?.({
      ok: true,
      json: async () => projection,
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(screen.getByRole('option', { name: /后来的记忆/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '喜欢黑咖啡' })).toBeNull();
  });

  it('repaints orbit by updating canvas yaw state on pointer drag', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('memory-graph')).toBeInTheDocument());
    const canvas = screen.queryByTestId('constellation-stage');
    if (!canvas) {
      expect(screen.getByTestId('memory-compatibility')).toBeInTheDocument();
      return;
    }
    const before = canvas.getAttribute('data-yaw');
    fireEvent.pointerDown(canvas, { clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { clientX: 80, clientY: 24 });
    expect(canvas.getAttribute('data-yaw')).not.toBe(before);
    const zoomBefore = canvas.getAttribute('data-zoom');
    fireEvent.wheel(canvas, { deltaY: 400 });
    expect(canvas.getAttribute('data-zoom')).not.toBe(zoomBefore);
  });

  it('keeps overflow and keyboard paths to search, LIST, 2D, identity, and worldline', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await screen.findByTestId('memory-graph');
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument();
    expect(screen.getByTestId('select-soul')).toBeInTheDocument();
    expect(screen.getByTestId('memory-plaque')).toHaveTextContent('OKABE');
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'true');
    expect(screen.getByLabelText('搜索记忆')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'LIST' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^3D$|^2D$/ })).toBeInTheDocument();
    expect(screen.getByLabelText('浏览身份')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览世界线')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: '2' });
    expect(screen.getByRole('button', { name: /^3D$|^2D$/ })).toBeInTheDocument();
    expect(screen.queryByText('ORIGIN')).toBeNull();
    expect(screen.queryByText('LIVED')).toBeNull();
    expect(screen.queryByText('SELF MODEL')).toBeNull();
  });

  it('focuses the selected or first LIST option so arrows rove there', async () => {
    const dual: MemoryProjection = {
      ...projection,
      result_ids: ['fact:1', 'fact:2'],
      shown: { ...projection.shown, results: 2 },
      eligible: { ...projection.eligible, results: 2 },
      nodes: [
        ...projection.nodes,
        {
          kind: 'fact',
          projection_id: 'fact:2',
          fact_id: '2',
          label: '后来的记忆',
          is_pinned: true,
          updated_at: '2026-08-15T00:00:00Z',
          topic_id: null,
        },
      ],
    };
    mockApi(() => okJson(dual));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    const first = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    await waitFor(() => expect(document.activeElement).toBe(first));
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    await waitFor(() => expect(document.activeElement).toHaveTextContent('后来的记忆'));
  });

  it('keeps the narrow LIST sheet while ArrowDown roves to the next result', async () => {
    const previousWidth = window.innerWidth;
    window.innerWidth = 480;
    const dual: MemoryProjection = {
      ...projection,
      result_ids: ['fact:1', 'fact:2'],
      shown: { ...projection.shown, results: 2 },
      eligible: { ...projection.eligible, results: 2 },
      nodes: [
        ...projection.nodes,
        {
          kind: 'fact',
          projection_id: 'fact:2',
          fact_id: '2',
          label: '后来的记忆',
          is_pinned: true,
          updated_at: '2026-08-15T00:00:00Z',
          topic_id: null,
        },
      ],
    };
    mockApi(() => okJson(dual));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    const first = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    await waitFor(() => expect(document.activeElement).toBe(first));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    first.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
    await waitFor(() => expect(document.activeElement).toHaveTextContent('后来的记忆'));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.getByTestId('memory-list')).not.toHaveAttribute('hidden');
    window.innerWidth = previousWidth;
  });

  it('focuses the supporting LIST row after selecting SOUL and keeps arrow continuity', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    const soul = await screen.findByRole('option', { name: '上下文 SOUL' });
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(projection.center.projection_id);
    });
    expect(soul).toHaveAttribute('data-outline-id', projection.center.projection_id);
    expect(soul).not.toHaveAttribute('data-result-id');
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowDown' });
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).not.toBe(projection.center.projection_id);
      expect(document.activeElement?.getAttribute('role')).toBe('option');
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowUp' });
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(projection.center.projection_id);
    });
  });

  it('focuses a supporting Topic row when LIST opens from Compatibility', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: '3D' }));
    fireEvent.click(await screen.findByRole('button', { name: '上下文 咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    const topic = await screen.findByRole('option', { name: '上下文 咖啡' });
    await waitFor(() => expect(document.activeElement?.getAttribute('data-outline-id')).toBe('topic:t1'));
    expect(topic).toHaveAttribute('data-outline-id', 'topic:t1');
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowUp' });
    await waitFor(() => expect(document.activeElement?.getAttribute('data-outline-id')).not.toBe('topic:t1'));
  });

  it('shows only one auxiliary surface and keeps the semantic selection', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'tools');
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'true');
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(screen.queryByTestId('reading-dom')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false');
    const fact = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    fireEvent.click(fact);
    expect(fact).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();

    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'inspector');
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(screen.getByRole('option', { name: '上下文 SOUL' })).toHaveAttribute('aria-selected', 'true');
  });

  it('lets a focused group summary toggle with Enter instead of hijacking selection', async () => {
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
      shown: { ...projection.shown, results: 8 },
      eligible: { ...projection.eligible, results: 8 },
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
        { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:okabe', to: 'topic:t2' },
        ...facts.map((item) => ({
          kind: 'has_topic' as const,
          from: item.projection_id,
          to: item.topic_id === 't1' ? 'topic:t1' : 'topic:t2',
        })),
      ],
    };
    mockApi(() => okJson(dense));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('role')).toBe('option');
    });
    const research = await screen.findByText(/研究 · 4 条/);
    const details = research.closest('details');
    expect(details).toBeTruthy();
    expect(details?.open).toBe(false);
    const summary = details?.querySelector('summary');
    expect(summary).toBeTruthy();
    (summary as HTMLElement).focus();
    activateWithEnter(summary as HTMLElement);
    expect(details?.open).toBe(true);
    activateWithEnter(summary as HTMLElement);
    expect(details?.open).toBe(false);
  });

  it('lets Enter on a Compatibility control select that item instead of the old focusedId', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    expect(screen.getByRole('button', { name: '喜欢黑咖啡' })).toHaveAttribute('aria-current', 'true');
    const topic = screen.getByRole('button', { name: '上下文 咖啡' });
    topic.focus();
    activateWithEnter(topic);
    expect(topic).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: '喜欢黑咖啡' })).not.toHaveAttribute('aria-current');
  });

  it('lets the ranked-browse control activate from the keyboard', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    fireEvent.click(screen.getByRole('button', { name: '按主题' }));
    expect(screen.getByTestId('memory-list')).toHaveAttribute('data-browse', 'grouped');
    const ranked = screen.getByRole('button', { name: '按排序' });
    ranked.focus();
    activateWithEnter(ranked);
    expect(screen.getByTestId('memory-list')).toHaveAttribute('data-browse', 'ranked');
  });

  it('advances LIST focus on every consecutive ArrowDown without dropping keys', async () => {
    const dual: MemoryProjection = {
      ...projection,
      result_ids: ['fact:1', 'fact:2'],
      shown: { ...projection.shown, results: 2 },
      eligible: { ...projection.eligible, results: 2 },
      nodes: [
        ...projection.nodes,
        {
          kind: 'fact',
          projection_id: 'fact:2',
          fact_id: '2',
          label: '后来的记忆',
          is_pinned: true,
          updated_at: '2026-08-15T00:00:00Z',
          topic_id: null,
        },
      ],
    };
    mockApi(() => okJson(dual));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe('fact:1');
    });
    const first = document.activeElement as HTMLElement;
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowDown' });
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(
        projection.center.projection_id,
      );
    });
  });

  it('auto-focuses the selected supporting row after LIST remounts from Inspector', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(projection.center.projection_id);
    });
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.queryByTestId('memory-list')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(projection.center.projection_id);
    });
  });

  it('auto-focuses the selected result after LIST remounts from tools', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    const fact = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    fireEvent.click(fact);
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe('fact:1');
    });
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    expect(screen.queryByTestId('memory-list')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe('fact:1');
    });
  });

  it('keeps one exclusive narrow bottom sheet for inspector versus LIST', async () => {
    const previousWidth = window.innerWidth;
    window.innerWidth = 480;
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await screen.findByTestId('memory-list');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'inspector');
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    window.innerWidth = previousWidth;
  });

  it('distinguishes 503 unavailable from generic failure and does not leak paths', async () => {
    mockApi(() => Promise.resolve({
      ok: false,
      status: 503,
      json: async () => ({ detail: { code: 'projection_unavailable', message: 'D\\\\secret.db' } }),
    }));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    expect(await screen.findByTestId('memory-unavailable')).toHaveTextContent('暂时不可用');
    expect(screen.queryByText(/secret\.db/)).toBeNull();
    expect(screen.getByTestId('memory-retry')).toBeInTheDocument();
  });

  it('shows a bounded generic error for non-503 failures without leaking a path', async () => {
    mockApi(() => Promise.resolve({
      ok: false,
      status: 500,
      json: async () => ({ detail: { code: 'projection_failed', message: 'C:\\\\data\\\\memory.sqlite IOERR' } }),
    }));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    const alert = await screen.findByTestId('memory-error');
    expect(alert).toHaveTextContent('无法读取记忆投影');
    expect(screen.queryByTestId('memory-unavailable')).toBeNull();
    expect(screen.queryByText(/memory\.sqlite/)).toBeNull();
    expect(screen.queryByText(/IOERR/)).toBeNull();
    expect(screen.getByTestId('memory-retry')).toBeInTheDocument();
  });

  it('discloses shown/eligible truncation on the shipped Overview', async () => {
    const clipped: MemoryProjection = {
      ...projection,
      shown: { nodes: 3, edges: 2, records: 1, results: 1 },
      eligible: { nodes: 20, edges: 18, records: 12, results: 8 },
      truncated: { nodes: true, edges: true, records: true, results: true },
    };
    mockApi(() => okJson(clipped));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    const note = await screen.findByTestId('memory-truncation');
    expect(note).toHaveTextContent('当前呈现 1 / 8');
    expect(note).toHaveTextContent('并非全部记忆');
    expect(screen.getByTestId('memory-shown-eligible')).toHaveTextContent('1 / 8');
  });

  it('explains criteria-zero separately from true-empty', async () => {
    const vacant: MemoryProjection = {
      ...projection,
      empty: true,
      result_ids: [],
      nodes: [projection.nodes[0]],
      edges: [],
      shown: { nodes: 1, edges: 0, records: 0, results: 0 },
      eligible: { nodes: 1, edges: 0, records: 0, results: 0 },
      composition: {
        active_facts: 0,
        active_experiences: 0,
        eligible_topics: 0,
        latest_memory_change_at: null,
        person_anchors_supported: false,
      },
    };
    const filtered: MemoryProjection = {
      ...vacant,
      composition: { ...vacant.composition, active_facts: 3, eligible_topics: 1 },
      criteria: { query: '拿铁', kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    };
    mockApi((url) => {
      if (url.includes('query=')) return okJson(filtered);
      return okJson(vacant);
    });
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    expect(await screen.findByTestId('memory-empty')).toHaveTextContent('长期记忆');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.change(screen.getByLabelText('搜索记忆'), { target: { value: '拿铁' } });
    const search = screen.getByLabelText('搜索记忆') as HTMLInputElement;
    search.focus();
    search.setSelectionRange(search.value.length, search.value.length);
    fireEvent.keyDown(search, { key: 'Home' });
    expect(search.value).toBe('拿铁');
    expect(await screen.findByTestId('memory-criteria-empty')).toHaveTextContent('没有匹配');
    expect(screen.getByTestId('memory-criteria-chips')).toHaveTextContent('拿铁');
  });

  it('keeps reduced motion as a motion preference rather than a semantic mode switch', async () => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    })) as typeof window.matchMedia;
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-reduced-motion', 'true');
  });

  it('sends kind and pin criteria on the live projection request', async () => {
    const seen: string[] = [];
    mockApi((url) => {
      seen.push(url);
      return okJson(projection);
    });
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByLabelText('事实'));
    fireEvent.click(screen.getByLabelText('仅置顶'));
    await waitFor(() => {
      expect(seen.some((url) => url.includes('kind=fact') && url.includes('pinned_only=true'))).toBe(true);
    });
  });

  it('uses unfiltered composition in the SOUL inspector under active criteria', async () => {
    const filtered: MemoryProjection = {
      ...projection,
      empty: false,
      criteria: { query: '咖啡', kinds: ['fact'], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
      composition: {
        active_facts: 9,
        active_experiences: 2,
        eligible_topics: 4,
        latest_memory_change_at: '2026-08-14T00:00:00Z',
        person_anchors_supported: false,
      },
      shown: { nodes: 2, edges: 1, records: 1, results: 1 },
      eligible: { nodes: 2, edges: 1, records: 1, results: 1 },
    };
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 4 });
      }
      return okJson(filtered);
    });
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('inspector-composition')).toHaveTextContent('9');
    expect(screen.getByTestId('inspector-composition')).toHaveTextContent('2');
    expect(screen.getByTestId('inspector-projection-counts')).toHaveTextContent('1 / 1');
    expect(screen.getByTestId('soul-inspector').textContent).not.toMatch(/people\s*=\s*0/i);
    await waitFor(() => expect(screen.getByTestId('inspector-jobs')).toHaveTextContent('失败 4'));
  });

  it('restores the previous worldline when a scope switch is unavailable', async () => {
    mockApi((url) => {
      if (url.includes('worldline=beta')) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: async () => ({ detail: { code: 'projection_unavailable' } }),
        });
      }
      return okJson(projection);
    });
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.change(screen.getByLabelText('浏览世界线'), { target: { value: 'beta' } });
    await waitFor(() => {
      expect(screen.getByTestId('memory-plaque')).toHaveTextContent('SG');
      expect(screen.getByTestId('memory-announce')).toHaveTextContent('范围切换失败');
    });
  });

  it('clears an illegal selection after a successful scope switch', async () => {
    const beta: MemoryProjection = {
      ...projection,
      scope: { ...projection.scope, worldline: 'beta' },
      center: { ...projection.center, projection_id: 'hub:continuity:s:beta:okabe' },
      result_ids: ['fact:beta'],
      nodes: [
        {
          kind: 'continuity_hub',
          projection_id: 'hub:continuity:s:beta:okabe',
          label_primary: 'AMADEUS',
          label_secondary: 'SOUL',
        },
        {
          kind: 'fact',
          projection_id: 'fact:beta',
          fact_id: 'beta',
          label: 'β 记忆',
          is_pinned: false,
          updated_at: '2026-08-19T00:00:00Z',
          topic_id: null,
        },
      ],
      edges: [],
    };
    mockApi((url) => okJson(String(url).includes('worldline=beta') ? beta : projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.change(screen.getByLabelText('浏览世界线'), { target: { value: 'beta' } });
    await waitFor(() => expect(screen.getByTestId('memory-plaque')).toHaveTextContent('β'));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    expect(screen.queryByRole('option', { name: '喜欢黑咖啡' })).toBeNull();
    expect(screen.getByRole('option', { name: /β 记忆/ })).toBeInTheDocument();
    expect(document.querySelector('[data-result-id="fact:1"]')).toBeNull();
  });

  it('does not render a fabricated row for a malformed result_id', async () => {
    const broken: MemoryProjection = {
      ...projection,
      result_ids: ['fact:1', 'fact:ghost'],
      shown: { ...projection.shown, results: 2 },
      eligible: { ...projection.eligible, results: 2 },
    };
    mockApi(() => okJson(broken));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    expect(screen.getByTestId('memory-malformed')).toBeInTheDocument();
    expect(screen.getByTestId('memory-malformed')).toHaveTextContent(/无效|无法生成/);
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(document.querySelector('[data-result-id]')).toBeNull();
    expect(screen.queryByRole('option', { name: '喜欢黑咖啡' })).toBeNull();
    expect(screen.queryByRole('option', { name: /fact:ghost|unknown/ })).toBeNull();
    expect(screen.queryByText('结果 2')).toBeNull();
  });

  it('opens Compatibility as a structure tree without dumping every evidence caption', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: '3D' }));
    expect(await screen.findByRole('region', { name: '记忆结构' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SOUL' })).toBeInTheDocument();
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
  });

  it('moves a shared selection with arrows and Home', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    const selected = document.querySelector('[aria-selected="true"]');
    expect(selected).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Home' });
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
  });
});
