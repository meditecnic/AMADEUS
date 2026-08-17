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

describe('MemorySpace', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
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
    await waitFor(() => expect(screen.getByTestId('constellation-stage')).toBeInTheDocument());
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
    fireEvent.click(screen.getByRole('button', { name: '喜欢黑咖啡' }));
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics')).toContain('topic:t1');
    expect(screen.getByTestId('reading-kind')).toHaveTextContent('事实');
    expect(screen.getByTestId('reading-title')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.getByTestId('reading-summary')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('否');
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('2026-08-14T00:00:00Z');
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('t1');
    expect(screen.queryByRole('button', { name: /置顶|删除|编辑/ })).toBeNull();
    expect(screen.queryByTestId('reading-scene') && screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => expect(screen.getByTestId('reading-scene')).toBeInTheDocument());
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.getByTestId('reading-scene')).toHaveAttribute('data-attached', 'sphere');
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
    await waitFor(() => expect(screen.getByTestId('constellation-stage')).toBeInTheDocument());
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
    await waitFor(() => expect(screen.getByTestId('constellation-stage')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'LIST' }));
    await waitFor(() => expect(screen.getByText('后来的记忆')).toBeInTheDocument());
    releaseOld?.({
      ok: true,
      json: async () => projection,
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(screen.getByText('后来的记忆')).toBeInTheDocument();
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
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
    const canvas = await screen.findByTestId('constellation-stage');
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
    await screen.findByTestId('constellation-stage');
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument();
    expect(screen.getByTestId('select-soul')).toBeInTheDocument();
    expect(screen.getByTestId('memory-plaque')).toHaveTextContent('OKABE');
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false');
    fireEvent.click(screen.getByRole('button', { name: '仪器' }));
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'true');
    expect(screen.getByLabelText('搜索记忆')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'LIST' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '3D' })).toBeInTheDocument();
    expect(screen.getByLabelText('浏览身份')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览世界线')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: '2' });
    expect(screen.getByRole('button', { name: '2D' })).toBeInTheDocument();
    expect(screen.queryByText('ORIGIN')).toBeNull();
    expect(screen.queryByText('LIVED')).toBeNull();
    expect(screen.queryByText('SELF MODEL')).toBeNull();
  });
});
