import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MemorySpace } from './MemorySpace';
import type { MemoryProjection } from './types';

const rendererHarness = vi.hoisted(() => ({ supported: true }));

vi.mock('./orreryRenderer', () => {
  class OrreryRuntime {
    supported = rendererHarness.supported;
    soulOpening = { open: false, progress: 0, reading: false, setOpen() {}, dolly() {}, orbit() {} };
    setSize() {}
    setWorldline() {}
    setReducedMotion() {}
    setDecoration() {}
    setExpanded() {}
    setQuery() {}
    setSelected() {}
    setAmbientPaused() {}
    setHover() {}
    applyFrame() {}
    render() {}
    pick() { return null; }
    pickSoulCore() { return false; }
    project() { return null; }
    dispose() {}
  }
  return { OrreryRuntime };
});

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

// Filters now live inside Find; preserve each test's real interaction intent.
function toggleFindFilters() {
  if (!screen.queryByTestId('memory-space-find')) fireEvent.click(screen.getByRole('button', { name: '查找' }));
  fireEvent.click(screen.getByText('主题与时间'));
}
async function chooseWorldline(value: string) {
  // jsdom has no scrolling implementation; Radix calls this when focusing an option.
  if (!HTMLElement.prototype.scrollIntoView) HTMLElement.prototype.scrollIntoView = vi.fn();
  fireEvent.keyDown(screen.getByRole('combobox', { name: '浏览世界线' }), { key: 'ArrowDown' });
  const option = await waitFor(() => {
    const found = screen.getAllByRole('option').find((item) => item.getAttribute('data-value') === value);
    expect(found).toBeTruthy();
    return found!;
  });
  fireEvent.keyDown(option, { key: 'Enter' });
  await waitFor(() => expect(screen.getByRole('combobox', { name: '浏览世界线' })).toHaveAttribute('data-state', 'closed'));
}
function mockApi(impl: (url: string) => Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => impl(String(input))));
}

function okJson(body: unknown) {
  return Promise.resolve({
    ok: true,
    json: async () => body,
  });
}

function errorJson(status: number, code: string) {
  return Promise.resolve({
    ok: false,
    status,
    json: async () => ({ detail: { code } }),
  });
}

// Frozen fact-details payload shape (system_api.py L676). Confidence is
// intentionally present here to prove the reading surface never renders it.
const factDetailsFixture = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  fact: { fact_id: '1', topic_id: 't1', is_pinned: false, active_version: 1, display_text: '喜欢黑咖啡' },
  versions: [
    {
      version_no: 1,
      display_text: '喜欢黑咖啡',
      confidence: 0.92,
      change_kind: 'create',
      previous_version: null,
      valid_from: '2026-08-13T00:00:00Z',
      invalid_at: null,
      is_active: true,
    },
  ],
  provenance: [
    {
      version_no: 1,
      observation_id: 'o1',
      source_message_id: 11,
      conversation_id: 'c1',
      source_created_at: '2026-08-13T00:00:00Z',
      source_state: 'present',
      excerpt: '我挺喜欢黑咖啡的',
    },
  ],
};

function activateWithEnter(el: HTMLElement) {
  const allowed = fireEvent.keyDown(el, { key: 'Enter' });
  if (allowed) fireEvent.click(el);
}

const originalMatchMedia = window.matchMedia;
const originalWidth = window.innerWidth;

describe('MemorySpace', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.matchMedia = originalMatchMedia; window.innerWidth = originalWidth;
    rendererHarness.supported = true;
  });

  it('uses the complete LIST as the only unavailable-3D path and preserves reading through retry', async () => {
    rendererHarness.supported = false;
    mockApi((url) => {
      if (url.includes('/api/memory/facts/1/details')) return okJson(factDetailsFixture);
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

    const list = await screen.findByTestId('memory-list');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-surface', 'list');
    expect(list).toHaveTextContent('喜欢黑咖啡');
    expect(screen.queryByTestId('memory-compatibility')).toBeNull();
    expect(screen.queryByRole('button', { name: /^2D$|^3D$/ })).toBeNull();

    fireEvent.click(screen.getByRole('option', { name: '喜欢黑咖啡' }));
    await waitFor(() => expect(screen.getByTestId('reading-dom')).toBeInTheDocument());
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-id', 'fact:1');

    fireEvent.click(screen.getByTestId('memory-back'));
    expect(await screen.findByTestId('memory-list')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-id', 'fact:1');

    rendererHarness.supported = true;
    fireEvent.click(screen.getByTestId('memory-retry-3d'));
    await waitFor(() => expect(screen.getByTestId('constellation-stage')).toBeInTheDocument());
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-surface', '3d');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-id', 'fact:1');
  });

  it('keeps the sticky LIST after repeated context loss even when another frame arrives', async () => {
    let runFrame: FrameRequestCallback | null = null;
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      runFrame = callback;
      return 1;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    mockApi(() => okJson(projection));
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );

    const firstStage = await screen.findByTestId('constellation-stage');
    await waitFor(() => expect(runFrame).not.toBeNull());
    act(() => {
      firstStage.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
      runFrame?.(16);
    });
    await waitFor(() => {
      expect(screen.getByTestId('constellation-stage')).not.toBe(firstStage);
    });

    act(() => {
      screen.getByTestId('constellation-stage').dispatchEvent(
        new Event('webglcontextlost', { cancelable: true }),
      );
      runFrame?.(32);
    });
    await waitFor(() => {
      expect(screen.getByTestId('memory-space')).toHaveAttribute('data-surface', 'list');
    });

    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-surface', 'list');
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.getAllByRole('option', { name: '喜欢黑咖啡' })).toHaveLength(1);
  });

  it('loads one projection into Overview, LIST, and a single G47 reading surface', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 1 });
      }
      if (url.includes('/api/memory/facts/1/details')) {
        return okJson(factDetailsFixture);
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
    expect(screen.getByTestId('constellation-stage')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-phase', 'overview');
    expect(screen.getByTestId('soul-lockup')).toHaveTextContent('SOUL');
    expect(screen.getByTestId('soul-lockup')).not.toHaveTextContent('AMADEUS');
    expect(screen.getByTestId('memory-space-veil')).toHaveAttribute('data-chrome', 'compact');
    if (!screen.queryByTestId('memory-space-find')) fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(screen.getByTestId('memory-shown-eligible')).toHaveTextContent('当前呈现 1 / 1');
    expect(screen.queryByText('ORIGIN')).toBeNull();
    expect(screen.queryByText('COMING SOON')).toBeNull();
    expect(screen.queryByRole('button', { name: '深入查看' })).toBeNull();

    expect(screen.getByTestId('memory-list')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.getByRole('option', { name: '上下文 SOUL' })).toBeInTheDocument();
    expect(screen.getAllByRole('option', { name: '喜欢黑咖啡' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('option', { name: '喜欢黑咖啡' }));
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics')).toContain('topic:t1');
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.getByTestId('reading-kind')).toBeInTheDocument();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-kind')).toBeInTheDocument();
    });
    expect(screen.getByTestId('reading-title')).toHaveTextContent('喜欢黑咖啡');
    // P1R-3 §7.1: the title is not duplicated as a second label.
    expect(screen.queryByTestId('reading-summary')).toBeNull();
    expect(screen.getByTestId('reading-provenance')).toHaveTextContent('否');
    // Humanized envelope fields; raw ISO and raw ids stay out of default slots.
    expect(screen.getByTestId('reading-provenance').textContent ?? '').not.toContain('2026-08-14T');
    expect(screen.getByTestId('reading-provenance').textContent ?? '').not.toContain('t1');
    expect(screen.getByTestId('reading-topic-label')).toHaveTextContent('咖啡');
    // P1 spec-3: exactly one payload-owned primary text in the success state.
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    });
    expect(screen.getByTestId('reading-title')).toHaveTextContent('喜欢黑咖啡');
    expect(screen.queryByTestId('reading-evidence-text')).toBeNull();
    expect(screen.queryByTestId('reading-projection-summary')).toBeNull();
    expect(screen.getByTestId('reading-source-excerpt')).toHaveTextContent('我挺喜欢黑咖啡的');
    expect(screen.getByTestId('reading-correction-notice')).toHaveTextContent('当前视图只读，不能在此纠正');
    expect(screen.getByTestId('reading-deeplink-notice')).toBeInTheDocument();
    expect(screen.getByTestId('reading-dom').textContent ?? '').not.toContain('0.92');
    // P1 spec-4: diagnostics carry source ids, raw ISO, and the scope echo.
    expect(screen.getByTestId('reading-diagnostics-source-messages')).toHaveTextContent('11');
    expect(screen.getByTestId('reading-diagnostics-raw-time').textContent ?? '').toContain('2026-08-13T00:00:00Z');
    expect(screen.getByTestId('reading-diagnostics-scope')).toHaveTextContent('s / steins_gate / okabe');
    // P1 spec-4 + P2-B: here the LIST closes before the payload resolves, so
    // the reading surface is visible at resolution time.
    await waitFor(() => {
      expect(screen.getByTestId('memory-announce')).toHaveTextContent('证据已读取');
    });
    expect(screen.queryByRole('button', { name: /置顶|删除|编辑/ })).toBeNull();
    expect(screen.queryByTestId('reading-scene') && screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.queryByRole('button', { name: /置顶|删除|编辑/ })).toBeNull();
  });

  it('pure LIST rove moves focus only: zero selection and zero details requests', async () => {
    const dualProjection: MemoryProjection = {
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
          is_pinned: false,
          updated_at: '2026-08-15T00:00:00Z',
          topic_id: null,
        },
      ],
    };
    const calls: string[] = [];
    mockApi((url) => {
      calls.push(url);
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(factDetailsFixture);
      }
      return okJson(dualProjection);
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    const first = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    // Pure rove: arrows move focus only; no selection, no request — even
    // after the async effect flush window.
    fireEvent.focus(first);
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.getByTestId('memory-space').getAttribute('data-selected-id')).toBe('');
    expect(calls.filter((url) => url.includes('/details'))).toHaveLength(0);
    fireEvent.keyDown(screen.getByRole('option', { name: /后来的记忆/ }), { key: 'ArrowUp' });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.getByTestId('memory-space').getAttribute('data-selected-id')).toBe('');
    expect(calls.filter((url) => url.includes('/details'))).toHaveLength(0);
    // Keyboard-activated selection loads details once, using the canonical
    // fact_id read from the projection node (never projection_id parsing).
    activateWithEnter(first);
    await waitFor(() => {
      expect(calls.filter((url) => url.includes('/api/memory/facts/1/details'))).toHaveLength(1);
    });
    expect(calls.filter((url) => url.includes('fact:1/details'))).toHaveLength(0);
    // The same active record is retained across views; no refetch.
    fireEvent.click(first);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(calls.filter((url) => url.includes('/details'))).toHaveLength(1);
  });

  it('renders only the payload-owned primary text when it differs from the envelope label', async () => {
    const verifiedFixture = {
      ...factDetailsFixture,
      fact: { ...factDetailsFixture.fact, display_text: '已核验的正文' },
    };
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(verifiedFixture);
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-title')).toHaveTextContent('已核验的正文');
    });
    // The stale envelope label must not head the verified card; the title is
    // the single payload-owned primary text.
    expect(screen.getByTestId('reading-title')).not.toHaveTextContent('喜欢黑咖啡');
    expect(screen.queryByTestId('reading-summary')).toBeNull();
    expect(screen.queryByTestId('reading-projection-summary')).toBeNull();
  });

  it('never issues a new-scope details request for a stale selection during scope switch', async () => {
    const calls: string[] = [];
    let releaseBeta: ((value: unknown) => void) | undefined;
    mockApi((url) => {
      calls.push(url);
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(factDetailsFixture);
      }
      if (url.includes('worldline=beta')) {
        return new Promise((resolve) => {
          releaseBeta = resolve;
        });
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    await waitFor(() => {
      expect(calls.filter((url) => url.includes('/api/memory/facts/1/details'))).toHaveLength(1);
    });
    toggleFindFilters();
    await chooseWorldline('beta');
    await new Promise((resolve) => setTimeout(resolve, 40));
    // Before the new envelope arrives, the stale SG selection must not be
    // requested under the beta scope.
    expect(calls.filter((url) => url.includes('/details') && url.includes('worldline=beta'))).toHaveLength(0);
    releaseBeta?.({
      ok: true,
      json: async () => ({
        ...projection,
        scope: { session_id: 's', worldline: 'beta', identity_mode: 'okabe' },
        result_ids: [],
        nodes: [projection.nodes[0]],
        edges: [],
        empty: true,
        eligible: { nodes: 1, edges: 0, records: 0, results: 0 },
        shown: { nodes: 1, edges: 0, records: 0, results: 0 },
      }),
    });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(calls.filter((url) => url.includes('/details') && url.includes('worldline=beta'))).toHaveLength(0);
  });

  it('never revives a stale activation across scope switch or roundtrip without a fresh gesture', async () => {
    const calls: string[] = [];
    // Both worldlines contain the SAME record id, so a stale activation
    // would match again after the roundtrip unless it is invalidated.
    const betaProjection: MemoryProjection = {
      ...projection,
      scope: { session_id: 's', worldline: 'beta', identity_mode: 'okabe' },
    };
    mockApi((url) => {
      calls.push(url);
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(factDetailsFixture);
      }
      if (url.includes('worldline=beta')) {
        return okJson(betaProjection);
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    });
    expect(calls.filter((url) => url.includes('/details') && url.includes('worldline=steins_gate'))).toHaveLength(1);

    // Switch to beta; the new envelope still contains the same record.
    toggleFindFilters();
    await chooseWorldline('beta');
    // Close the tools sheet so the reading surface becomes active again.
    toggleFindFilters();
    await waitFor(() => {
      expect(screen.getByTestId('memory-plaque')).toHaveTextContent('β');
    });
    await new Promise((resolve) => setTimeout(resolve, 40));
    // No beta request without a fresh gesture, and the stale SG payload must
    // not reappear on the reading surface.
    expect(calls.filter((url) => url.includes('/details') && url.includes('worldline=beta'))).toHaveLength(0);
    expect(screen.queryByTestId('reading-evidence')).toBeNull();
    expect(screen.getByTestId('reading-projection-summary')).toBeInTheDocument();

    // Roundtrip back to SG: still no automatic request, no revival.
    await chooseWorldline('steins_gate');
    await waitFor(() => {
      expect(screen.getByTestId('memory-plaque')).toHaveTextContent('SG');
    });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(calls.filter((url) => url.includes('/details') && url.includes('worldline=steins_gate'))).toHaveLength(1);
    expect(screen.queryByTestId('reading-evidence')).toBeNull();

    // A fresh activation gesture is allowed to request again in the still-open Find.
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    await waitFor(() => {
      expect(calls.filter((url) => url.includes('/details') && url.includes('worldline=steins_gate'))).toHaveLength(2);
    });
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    });
  });

  it('canvas semantic arrows rove only: zero details requests until keyboard activation', async () => {
    const calls: string[] = [];
    mockApi((url) => {
      calls.push(url);
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        // Stall the payload so the loading transition stays observable.
        return new Promise(() => undefined);
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
    await screen.findByTestId('memory-graph');
    // Rove across the semantic order without touching selection. The first
    // result sits at order[0]; further arrows move to SOUL / Topic, which
    // must still not trigger evidence requests.
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.getByTestId('memory-space').getAttribute('data-selected-id')).toBe('');
    expect(calls.filter((url) => url.includes('/details'))).toHaveLength(0);
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    fireEvent.keyDown(window, { key: 'ArrowDown' });
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.getByTestId('memory-space').getAttribute('data-selected-id')).toBe('');
    expect(calls.filter((url) => url.includes('/details'))).toHaveLength(0);
    // Rove back to the fact and activate it with Enter.
    fireEvent.keyDown(window, { key: 'ArrowUp' });
    fireEvent.keyDown(window, { key: 'ArrowUp' });
    fireEvent.keyDown(window, { key: 'Enter' });
    await waitFor(() => {
      expect(calls.filter((url) => url.includes('/api/memory/facts/1/details'))).toHaveLength(1);
    });
    expect(calls.filter((url) => url.includes('fact:1/details'))).toHaveLength(0);
    // P2 spec-3: the loading transition is perceivable in the live region and
    // on the reading surface.
    await waitFor(() => {
      expect(screen.getByTestId('memory-announce')).toHaveTextContent('正在读取证据');
    });
    expect(screen.getByTestId('reading-evidence-loading')).toBeInTheDocument();
  });

  it('shows total versions plus the latest change in the collapsed revision summary', async () => {
    const revisedFixture = {
      ...factDetailsFixture,
      fact: { ...factDetailsFixture.fact, active_version: 2 },
      versions: [
        {
          ...factDetailsFixture.versions[0],
          is_active: false,
          invalid_at: '2026-08-14T00:00:00Z',
        },
        {
          version_no: 2,
          display_text: '喜欢黑咖啡',
          confidence: 0.9,
          change_kind: 'user_edit',
          previous_version: 1,
          valid_from: '2026-08-14T00:00:00Z',
          invalid_at: null,
          is_active: true,
        },
      ],
      provenance: [
        factDetailsFixture.provenance[0],
        { ...factDetailsFixture.provenance[0], version_no: 2, observation_id: 'o2', source_message_id: 12 },
      ],
    };
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(revisedFixture);
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-revision-summary')).toHaveTextContent('2 个版本 · 最新变更：用户修订');
    });
    // Historical versions keep their invalidation disclosure.
    expect(screen.getByTestId('reading-version-invalid').textContent ?? '').toContain('失效于');
  });

  it('P2-A: reactivating the same record never re-GETs; other records and retry do', async () => {
    const calls: string[] = [];
    mockApi((url) => {
      calls.push(url);
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(factDetailsFixture);
      }
      return okJson(projection);
    });
    const factCalls = () => calls.filter((url) => url.includes('/api/memory/facts/1/details')).length;
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    });
    expect(factCalls()).toBe(1);
    // Deactivate through SOUL, then reactivate the SAME record.
    fireEvent.click(screen.getByTestId('select-soul'));
    await new Promise((resolve) => setTimeout(resolve, 30));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    });
    // Cache reuse across LIST / 3D reactivation: no second GET.
    expect(factCalls()).toBe(1);
  });

  it('announces evidence once while Find and reading coexist', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson(factDetailsFixture);
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    // Wide Find and reading coexist; completion describes the visible evidence.
    await waitFor(() => {
      expect(screen.getByTestId('memory-announce')).toHaveTextContent('证据已读取');
    });
    // Closing Find keeps the same evidence and announcement; it never
    // never duplicates.
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    });
    expect(screen.getByTestId('memory-announce')).toHaveTextContent('证据已读取');
  });

  it('P2 round 5: a repeated failure after retry announces the terminal wording again', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return errorJson(500, 'memory_operation_failed');
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence-error')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId('memory-announce')).toHaveTextContent('证据暂时不可用');
    });
    // Retry: the fresh attempt re-arms the terminal announcement. The
    // transient loading wording is racy to observe; the contract point is
    // that the repeated failure is announced again, not swallowed.
    fireEvent.click(screen.getByTestId('reading-evidence-retry'));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence-error')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId('memory-announce')).toHaveTextContent('证据暂时不可用');
    });
  });

  it('P2 round 5: a fact without provenance shows an honest empty-source disclosure', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return okJson({ ...factDetailsFixture, provenance: [] });
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-sources-empty')).toHaveTextContent('未提供可显示的来源');
    });
    expect(screen.getByTestId('reading-sources-empty').textContent ?? '').not.toContain('删除');
  });

  it('P2 round 5: an experience without provenance shows the same honest disclosure', async () => {
    const projectionWithExperience: MemoryProjection = {
      ...projection,
      result_ids: ['experience:e1'],
      eligible: { nodes: 2, edges: 1, records: 1, results: 1 },
      shown: { nodes: 2, edges: 1, records: 1, results: 1 },
      nodes: [
        projection.nodes[0],
        {
          kind: 'experience',
          projection_id: 'experience:e1',
          experience_id: 'e1',
          label: '一起看星空',
          is_pinned: false,
          created_at: '2026-08-13T00:00:00Z',
          updated_at: '2026-08-13T00:00:00Z',
          expires_at: null,
          is_expired: false,
          conversation_id: 'c1',
        },
      ],
      edges: [{ kind: 'hub_to_evidence', from: projection.center.projection_id, to: 'experience:e1' }],
    };
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/api/memory/experiences/e1/details')) {
        return okJson({
          scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
          experience: {
            experience_id: 'e1',
            conversation_id: 'c1',
            display_text: '一起看星空',
            status: 'active',
            expires_at: null,
            created_at: '2026-08-13T00:00:00Z',
            is_expired: false,
          },
          observation_id: 'o9',
          provenance: [],
        });
      }
      return okJson(projectionWithExperience);
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={vi.fn()}
      />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: /一起看星空/ }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-sources-empty')).toHaveTextContent('未提供可显示的来源');
    });
    expect(screen.getByTestId('reading-sources-empty').textContent ?? '').not.toContain('删除');
  });

  it('renders honest not-found wording when the details endpoint returns 404', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        return errorJson(404, 'fact_not_found');
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    // Close LIST so the reading surface becomes the active sheet (wide IA).
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence-missing')).toHaveTextContent('该记忆在此范围内不再可用');
    });
    // Envelope text stays labeled as an unverified projection summary.
    expect(screen.getByTestId('reading-projection-summary')).toBeInTheDocument();
    // No ghost verified content, no retry loop against a 404.
    expect(screen.queryByTestId('reading-evidence-text')).toBeNull();
    expect(screen.queryByTestId('reading-evidence-retry')).toBeNull();
  });

  it('offers one retry on a 500-class backend failure and recovers on success', async () => {
    let attempts = 0;
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('/details')) {
        attempts += 1;
        return attempts === 1
          ? errorJson(500, 'memory_operation_failed')
          : okJson(factDetailsFixture);
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
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    activateWithEnter(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    // Close LIST so the reading surface becomes the active sheet (wide IA).
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(screen.getByTestId('reading-evidence-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('reading-evidence-error').textContent ?? '').not.toMatch(/SOUL.*(损伤|受伤)/);
    fireEvent.click(screen.getByTestId('reading-evidence-retry'));
    await waitFor(() => {
      expect(screen.getByTestId('reading-title')).toHaveTextContent('喜欢黑咖啡');
    });
    expect(screen.getByTestId('reading-evidence')).toBeInTheDocument();
    expect(screen.queryByTestId('reading-projection-summary')).toBeNull();
    expect(attempts).toBe(2);
  });

  it('opens the G27 inspector from SOUL without Origin/Lived/Self or people=0', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe', pending_count: 2, processing_count: 1, failed_count: 3, failed_jobs: [] });
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
    expect(screen.getByTestId('inspector-latest-change')).toHaveAttribute('title', '2026-08-14T00:00:00Z');
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

    fireEvent.click(screen.getByRole('button', { name: '展开核心' }));
    expect(screen.getByTestId('soul-interior')).toHaveAttribute('data-reading', 'false');
    fireEvent.click(screen.getByRole('button', { name: /了解这个起点/ }));
    expect(screen.getByTestId('soul-interior')).toHaveAttribute('data-reading', 'true');
    expect(screen.getByText(/Amadeus 以牧濑红莉栖/)).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('soul-interior')).toHaveAttribute('data-reading', 'false');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('soul-interior')).toBeNull();
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
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
    toggleFindFilters();
    fireEvent.change(screen.getByLabelText('搜索记忆'), { target: { value: '后来' } });
    await waitFor(() => expect(releaseNew).toBeTypeOf('function'));
    releaseNew?.({
      ok: true,
      json: async () => laterProjection,
    });
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
    const canvas = screen.getByTestId('constellation-stage');
    expect(canvas).toHaveAttribute('data-mode', '3d');
    const before = canvas.getAttribute('data-yaw');
    fireEvent.pointerDown(canvas, { clientX: 20, clientY: 20, buttons: 1 });
    fireEvent.pointerMove(canvas, { clientX: 80, clientY: 24, buttons: 1 });
    if (canvas.getAttribute('data-ownership') !== 'user') {
      expect(screen.getByTestId('memory-graph')).toBeInTheDocument();
      return;
    }
    expect(canvas.getAttribute('data-yaw')).not.toBe(before);
    const zoomBefore = canvas.getAttribute('data-zoom');
    fireEvent.wheel(canvas, { deltaY: 400 });
    expect(canvas.getAttribute('data-zoom')).not.toBe(zoomBefore);
  });

  it('keeps overflow and keyboard paths to search, LIST, identity, and worldline', async () => {
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false');
    toggleFindFilters();
    await waitFor(() => expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'true'));
    expect(screen.getByLabelText('搜索记忆')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查找' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^3D$|^2D$/ })).toBeNull();
    expect(screen.getByLabelText('浏览身份')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览世界线')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: '2' });
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-surface', '3d');
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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

  it('focuses a supporting Topic row when LIST opens from graph selection', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    fireEvent.keyDown(window, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    const topic = await screen.findByRole('option', { name: '上下文 咖啡' });
    await waitFor(() => expect(document.activeElement?.getAttribute('data-outline-id')).toBe('topic:t1'));
    expect(topic).toHaveAttribute('data-outline-id', 'topic:t1');
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowUp' });
    await waitFor(() => expect(document.activeElement?.getAttribute('data-outline-id')).not.toBe('topic:t1'));
  });

  it('keeps filters and results together while wide reading follows the semantic selection', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    toggleFindFilters();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    await waitFor(() => expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'true'));
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(screen.queryByTestId('reading-dom')).toBeNull();

    toggleFindFilters();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    await waitFor(() => expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false'));
    const fact = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    fireEvent.click(fact);
    expect(fact).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('reading-dom')).toBeInTheDocument();
    expect(screen.queryByTestId('soul-inspector')).toBeNull();

    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space-tools')).toHaveAttribute('data-open', 'false');

    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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

  it('lets the ranked-browse control activate from the keyboard', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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

  it('restores supporting-row focus when Find reopens alongside Inspector', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(projection.center.projection_id);
    });
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭查找' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe(projection.center.projection_id);
    });
  });

  it('preserves result selection through filters and restores focus when Find reopens', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    const fact = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    fireEvent.click(fact);
    await waitFor(() => {
      expect(document.activeElement?.getAttribute('data-outline-id')).toBe('fact:1');
    });
    toggleFindFilters();
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(fact).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: '关闭查找' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await screen.findByTestId('memory-list');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'list');
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'inspector');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    if (!screen.queryByTestId('memory-space-find')) fireEvent.click(screen.getByRole('button', { name: '查找' }));
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
    toggleFindFilters();
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
    toggleFindFilters();
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
        return okJson({ session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe', pending_count: 0, processing_count: 0, failed_count: 4, failed_jobs: [] });
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
    toggleFindFilters();
    await chooseWorldline('beta');
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    toggleFindFilters();
    await chooseWorldline('beta');
    await waitFor(() => expect(screen.getByTestId('memory-plaque')).toHaveTextContent('β'));
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
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(screen.getByTestId('memory-malformed')).toBeInTheDocument();
    expect(screen.getByTestId('memory-malformed')).toHaveTextContent(/无效|无法生成/);
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(document.querySelector('[data-result-id]')).toBeNull();
    expect(screen.queryByRole('option', { name: '喜欢黑咖啡' })).toBeNull();
    expect(screen.queryByRole('option', { name: /fact:ghost|unknown/ })).toBeNull();
    expect(screen.queryByText('结果 2')).toBeNull();
  });

  it('moves a shared selection with arrows and Home', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    // Arrows rove only; Enter activates the focused node.
    fireEvent.keyDown(window, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    const selected = document.querySelector('[aria-selected="true"]');
    expect(selected).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Home' });
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
  });

  it('panel close preserves the selected node and activation reopens it', async () => {
    mockApi((url) => url.includes('/details') ? okJson(factDetailsFixture) : okJson(projection));
    render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />);
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.click(screen.getByRole('button', { name: '关闭 SOUL 概览' }));
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'continuity_hub');
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    await screen.findByTestId('reading-dom');
    const branch = screen.getByTestId('memory-space').getAttribute('data-expanded-topics');
    fireEvent.click(screen.getByRole('button', { name: '关闭阅读' }));
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-id', 'fact:1');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-expanded-topics', branch);
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(screen.getByRole('option', { name: '喜欢黑咖啡' }));
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(await screen.findByTestId('reading-dom')).toBeInTheDocument();
  });

  it('RMB and Escape dismiss the SOUL inspector before leaving Memory', async () => {
    mockApi(() => okJson(projection));
    const onClose = vi.fn();
    render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} />);
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    fireEvent.contextMenu(screen.getByTestId('constellation-stage'));
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('select-soul'));
    expect(screen.getByTestId('soul-inspector')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.contextMenu(screen.getByTestId('soul-inspector'));
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
    fireEvent.click(screen.getByTestId('select-soul'));
    fireEvent.click(screen.getByRole('button', { name: '关闭 SOUL 概览' }));
    expect(screen.queryByTestId('soul-inspector')).toBeNull();
  });

  it('canvas RMB unwinds Fact to Topic to Overview and Back still closes Memory at root', async () => {
    mockApi(() => okJson(projection));
    const onClose = vi.fn();
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} />,
    );
    await screen.findByTestId('memory-graph');
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics') ?? '').toBe('');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'fact');
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics')).toContain('topic:');
    const canvas = await screen.findByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { button: 2, clientX: 24, clientY: 24 });
    fireEvent.contextMenu(canvas, { clientX: 24, clientY: 24 });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'fact');
    fireEvent.pointerDown(canvas, { button: 2, clientX: 26, clientY: 26 });
    fireEvent.contextMenu(canvas, { clientX: 26, clientY: 26 });
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'topic');
    fireEvent.pointerDown(canvas, { button: 2, clientX: 28, clientY: 28 });
    fireEvent.contextMenu(canvas, { clientX: 28, clientY: 28 });
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', '');
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerDown(canvas, { button: 2, clientX: 30, clientY: 30 });
    fireEvent.contextMenu(canvas, { clientX: 30, clientY: 30 });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('memory-back'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('canvas right-drag does not unwind or take left-button orbit', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    const canvas = await screen.findByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { button: 2, clientX: 20, clientY: 20 });
    const heldYaw = canvas.getAttribute('data-yaw');
    fireEvent.pointerMove(canvas, { buttons: 2, clientX: 96, clientY: 28 });
    fireEvent.contextMenu(canvas, { clientX: 96, clientY: 28 });
    expect(canvas.getAttribute('data-yaw')).toBe(heldYaw);
    fireEvent.pointerUp(canvas, { button: 2, clientX: 96, clientY: 28 });
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'fact');
    expect(canvas.getAttribute('data-ownership')).not.toBe('user');
  });

  it('Escape at Overview root closes Memory', async () => {
    mockApi(() => okJson(projection));
    const onClose = vi.fn();
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('staged Back closes LIST then Evidence then Topic without a canvas miss', async () => {
    mockApi(() => okJson(projection));
    const onClose = vi.fn();
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'fact');
    expect(screen.getByTestId('memory-list')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('memory-back'));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByTestId('memory-list')).toBeNull();
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'fact');
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics')).toContain('topic:');
    fireEvent.click(screen.getByTestId('memory-back'));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', 'topic');
    expect(screen.getByTestId('memory-space').getAttribute('data-expanded-topics')).toContain('topic:');
    fireEvent.click(screen.getByTestId('memory-back'));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-expanded-topics', '');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-kind', '');
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('memory-back'));
    if (!onClose.mock.calls.length) fireEvent.click(screen.getByTestId('memory-back'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape unwind retracts the visual branch', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    const canvas = screen.queryByTestId('constellation-stage');
    if (canvas) {
      expect(canvas.getAttribute('data-visible-evidence') ?? '').toContain('fact:1');
    }
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-expanded-topics', '');
    const after = screen.queryByTestId('constellation-stage');
    if (after) {
      expect(after.getAttribute('data-visible-evidence') ?? '').not.toContain('fact:1');
    }
  });

  it('same-scope envelope refresh drops the old result from LIST', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/status')) {
        return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      }
      if (url.includes('query=')) return okJson(laterProjection);
      return okJson(projection);
    });
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(await screen.findByRole('option', { name: '喜欢黑咖啡' })).toBeInTheDocument();
    toggleFindFilters();
    fireEvent.change(screen.getByLabelText('搜索记忆'), { target: { value: '后来' } });
    await waitFor(() => expect(screen.getByRole('option', { name: /后来的记忆/ })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: '喜欢黑咖啡' })).toBeNull();
  });

  it('one selection plus auxiliary change keeps the selection cause', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    expect(screen.getByTestId('memory-space').getAttribute('data-motion-cause')).toMatch(
      /selection|reselect|post-reconcile-replacement/,
    );
  });

  it('stale viewport notes cannot overwrite an explicit reselect', async () => {
    mockApi(() => okJson(projection));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    const option = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    fireEvent.click(option);
    fireEvent.click(option);
    expect(screen.getByTestId('memory-space').getAttribute('data-motion-cause')).toBe('reselect');
  });

  it('restores local datetime-local values from criteria after closing Find', async () => {
    mockApi((url) => {
      const parsed = new URL(url, 'http://memory.test');
      if (parsed.pathname.includes('/api/memory/graph/projection')) {
        return okJson({
          ...projection,
          criteria: {
            ...projection.criteria,
            updated_from: parsed.searchParams.get('updated_from'),
            updated_to: parsed.searchParams.get('updated_to'),
          },
        });
      }
      return okJson(projection);
    });
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(screen.getByText('主题与时间'));
    const start = await screen.findByLabelText('起始时间');
    fireEvent.change(start, { target: { value: '2026-01-01T09:00' } });
    expect(start).toHaveValue('2026-01-01T09:00');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(screen.queryByTestId('memory-space-find')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(await screen.findByLabelText('起始时间')).toHaveValue('2026-01-01T09:00');
    fireEvent.click(await screen.findByRole('button', { name: '清除筛选' }));
    expect(screen.getByLabelText('起始时间')).toHaveValue('');
    expect(screen.getByLabelText('结束时间')).toHaveValue('');
  });

  it('reads adjacent records without visiting topic rows or losing the narrow reading sheet', async () => {
    window.innerWidth = 390;
    const nextNode = laterProjection.nodes.find((node) => node.kind === 'fact')!;
    const resultProjection = {
      ...projection,
      result_ids: ['fact:1', 'topic:t1', 'fact:2'],
      nodes: [...projection.nodes, nextNode],
      shown: { ...projection.shown, results: 3 },
      eligible: { ...projection.eligible, results: 3 },
    };
    const requests: string[] = [];
    mockApi((url) => {
      requests.push(url);
      if (url.includes('/facts/2/details')) return okJson({
        ...factDetailsFixture,
        fact: { ...factDetailsFixture.fact, fact_id: '2', display_text: '后来的记忆' },
      });
      if (url.includes('/details')) return okJson(factDetailsFixture);
      return okJson(resultProjection);
    });
    render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />);
    await screen.findByTestId('memory-graph');
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    fireEvent.click(await screen.findByRole('option', { name: '喜欢黑咖啡' }));
    await screen.findByTestId('reading-evidence');
    expect(screen.getByTestId('reading-position')).toHaveTextContent('1 / 2');
    expect(screen.getByRole('button', { name: '上一条' })).toBeDisabled();
    const scroller = screen.getByTestId('reading-title').parentElement!;
    scroller.scrollTop = 200;
    fireEvent.click(screen.getByRole('button', { name: '下一条' }));
    await waitFor(() => expect(requests.some((url) => url.includes('/facts/2/details'))).toBe(true));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-id', 'fact:2');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'reading');
    expect(screen.getByTestId('reading-position')).toHaveTextContent('2 / 2');
    expect(screen.getByTestId('reading-title').parentElement!.scrollTop).toBe(0);
    expect(screen.getByRole('button', { name: '下一条' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '上一条' }));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-selected-id', 'fact:1');
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-narrow-sheet', 'reading');
  });

  it('keeps Find chrome available when the initial projection is unavailable', async () => {
    mockApi(() => Promise.resolve({
      ok: false,
      status: 503,
      json: async () => ({ detail: { code: 'projection_unavailable' } }),
    }));
    render(
      <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />,
    );
    expect(await screen.findByTestId('memory-unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查找' }));
    expect(screen.getByTestId('memory-space-find')).toBeInTheDocument();
    expect(screen.getByLabelText('搜索记忆')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览身份')).toBeInTheDocument();
    expect(screen.getByLabelText('浏览世界线')).toBeInTheDocument();
    expect(screen.getByTestId('memory-find-results-state')).toHaveTextContent('暂时不可用');
    expect(screen.queryByTestId('memory-list')).toBeNull();
  });
});
