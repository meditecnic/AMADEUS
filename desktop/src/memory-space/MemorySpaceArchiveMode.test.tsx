import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
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

const scopeBody = { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' };

function mockApi(impl: (url: string, init?: RequestInit) => Promise<unknown>) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => impl(String(input), init)));
}

function okJson(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => body });
}

function archiveBody(url: string, deletedRef?: { value: boolean }): Promise<unknown> | null {
  if (url.startsWith('/api/memory/facts?')) {
    if (deletedRef?.value) {
      return okJson({
        facts: [],
        scope: scopeBody,
        pagination: { limit: 20, offset: 0, total: 0, has_more: false },
      });
    }
    return okJson({
      facts: [
        {
          fact_id: '1',
          display_text: '档案里的黑咖啡',
          version_no: 1,
          active_version: 1,
          is_pinned: false,
          topic_id: 't1',
          confidence: 0.9,
          updated_at: '2026-08-14T00:00:00Z',
        },
      ],
      scope: scopeBody,
      pagination: { limit: 20, offset: 0, total: 1, has_more: false },
    });
  }
  if (url.startsWith('/api/memory/experiences?')) {
    return okJson({
      experiences: [],
      scope: scopeBody,
      pagination: { limit: 20, offset: 0, total: 0, has_more: false },
    });
  }
  if (url.startsWith('/api/memory/topics?')) {
    return okJson({ topics: [{ topic_id: 't1', display_label: '咖啡', fact_count: 1 }] });
  }
  if (url.startsWith('/api/memory/status?')) {
    return okJson({ session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe', pending_count: 0, processing_count: 0, failed_count: 0, failed_jobs: [] });
  }
  if (url.startsWith('/api/memory/observations?')) {
    return okJson({ observations: [], pagination: { limit: 20, offset: 0, total: 0, has_more: false } });
  }
  if (url.startsWith('/api/memory/facts/1/details')) {
    return okJson({
      scope: scopeBody,
      fact: { fact_id: '1', topic_id: 't1', is_pinned: false, active_version: 1, display_text: '档案里的黑咖啡' },
      versions: [
        {
          version_no: 1,
          display_text: '档案里的黑咖啡',
          change_kind: 'create',
          previous_version: null,
          valid_from: '2026-08-14T00:00:00Z',
          invalid_at: null,
          is_active: true,
        },
      ],
      provenance: [],
    });
  }
  return null;
}

function mountMemorySpace(deletedRef?: { value: boolean }) {
  mockApi((url, init) => {
    if (
      deletedRef
      && url.startsWith('/api/memory/facts/1?')
      && (init as RequestInit | undefined)?.method === 'DELETE'
    ) {
      deletedRef.value = true;
      return okJson({ fact_id: '1', deleted: true });
    }
    const archive = archiveBody(url, deletedRef);
    if (archive) return archive;
    if (url.includes('/api/memory/graph/projection')) {
      if (deletedRef?.value) {
        const withoutDeleted = {
          ...projection,
          composition: { ...projection.composition, active_facts: 0 },
          eligible: { nodes: 2, edges: 1, records: 0, results: 0 },
          shown: { nodes: 2, edges: 1, records: 0, results: 0 },
          empty: false,
          result_ids: [],
          nodes: projection.nodes.filter((node) => node.kind !== 'fact'),
          edges: projection.edges.filter((edge) => !edge.from.startsWith('fact:')),
        };
        return okJson(withoutDeleted);
      }
      return okJson(projection);
    }
    return okJson({});
  });
  return render(
    <MemorySpace
      sessionId="s"
      chatWorldline="steins_gate"
      chatIdentityMode="okabe"
      onClose={() => undefined}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Memory Space peer modes', () => {
  it('defaults to Constellation and offers the ARCHIVE peer mode toggle', async () => {
    mountMemorySpace();
    await waitFor(() => {
      expect(screen.getByTestId('memory-space')).toBeTruthy();
    });
    expect(screen.getByTestId('memory-mode-constellation').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('memory-mode-archive').getAttribute('aria-pressed')).toBe('false');
    expect(screen.queryByTestId('archive-view')).toBeNull();
  });

  it('switching to Archive shows record browsing and hides the constellation surface', async () => {
    mountMemorySpace();
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-view')).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
    });
    expect(screen.getByTestId('memory-mode-archive').getAttribute('aria-pressed')).toBe('true');
    // The 3D host is hidden while Archive owns the surface.
    const graph = screen.getByTestId('memory-graph');
    expect(graph.hasAttribute('hidden')).toBe(true);
  });

  it('switching back to Constellation preserves committed constellation state', async () => {
    mountMemorySpace();
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    // Open the LIST outline so constellation rows are DOM-observable.
    fireEvent.click(screen.getByText('索引'));
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-view')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-constellation'));
    await waitFor(() => {
      expect(screen.queryByTestId('archive-view')).toBeNull();
    });
    expect(screen.getByTestId('memory-graph').hasAttribute('hidden')).toBe(false);
    // Constellation rows survive the round trip (committed state preserved).
    expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
  });

  it('mode switch with unsaved edits demands an explicit discard choice first', async () => {
    mountMemorySpace();
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('档案里的黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-edit-fact')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('archive-edit-fact'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-editor-input')).toBeTruthy();
    });
    fireEvent.change(screen.getByTestId('archive-editor-input'), {
      target: { value: '未保存的草稿文本' },
    });
    // §4.14: clicking Constellation must NOT switch away silently — the
    // explicit discard confirmation appears instead, still inside Archive.
    fireEvent.click(screen.getByTestId('memory-mode-constellation'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-discard-confirm')).toBeTruthy();
    });
    expect(screen.getByTestId('archive-view')).toBeTruthy();
    expect((screen.getByTestId('archive-editor-input') as HTMLTextAreaElement).value).toBe(
      '未保存的草稿文本',
    );
    // Continue editing: still in Archive, draft untouched.
    fireEvent.click(screen.getByTestId('archive-discard-continue'));
    expect(screen.queryByTestId('archive-discard-confirm')).toBeNull();
    expect(screen.getByTestId('archive-view')).toBeTruthy();
    expect((screen.getByTestId('archive-editor-input') as HTMLTextAreaElement).value).toBe(
      '未保存的草稿文本',
    );
    // Explicit discard executes the original action (the mode switch).
    fireEvent.click(screen.getByTestId('memory-mode-constellation'));
    fireEvent.click(screen.getByTestId('archive-discard-confirm-button'));
    await waitFor(() => {
      expect(screen.queryByTestId('archive-view')).toBeNull();
    });
    expect(screen.getByTestId('memory-mode-constellation').getAttribute('aria-pressed')).toBe('true');
    // Returning to Archive: the discarded draft is gone.
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-view')).toBeTruthy();
    });
    expect(screen.queryByTestId('archive-editor-input')).toBeNull();
  });

  it('Archive mode never issues projection requests for its rows', async () => {
    mountMemorySpace();
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    const callsBefore = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) =>
      String(url).includes('graph/projection'),
    ).length;
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
    });
    const callsAfter = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) =>
      String(url).includes('graph/projection'),
    ).length;
    expect(callsAfter).toBe(callsBefore);
  });

  it('drops the direct /api/memory/status summary when its scope echo mismatches', async () => {
    mockApi((url) => {
      const archive = archiveBody(url);
      if (archive) return archive;
      if (url.includes('/api/memory/graph/projection')) return okJson(projection);
      if (url.includes('/api/memory/status')) {
        // Cross-scope summary: counts must never reach the UI.
        return okJson({
          session_id: 'other-session',
          worldline: 'beta',
          identity_mode: 'okabe',
          pending_count: 0,
          processing_count: 0,
          failed_count: 9,
        });
      }
      return okJson({});
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={() => undefined}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    // The summary was discarded: no jobs badge built from cross-scope counts.
    expect(screen.queryByTestId('inspector-jobs')).toBeNull();
  });

  it('holds stale projection content while the erasure refresh is pending', async () => {
    let deletedRef = { value: false };
    let releaseProjection: ((value: unknown) => void) | null = null;
    mockApi((url, init) => {
      if (
        url.startsWith('/api/memory/facts/1?')
        && (init as RequestInit | undefined)?.method === 'DELETE'
      ) {
        deletedRef.value = true;
        return okJson({ fact_id: '1', deleted: true });
      }
      const archive = archiveBody(url, deletedRef);
      if (archive) return archive;
      if (url.includes('/api/memory/graph/projection')) {
        if (!deletedRef.value) return okJson(projection);
        // The authoritative refresh is HELD: everything readable from the old
        // envelope must stay suppressed until it arrives.
        return new Promise((resolve) => {
          releaseProjection = resolve;
        });
      }
      if (url.includes('/api/memory/status')) {
        return okJson({
          session_id: 's',
          worldline: 'steins_gate',
          identity_mode: 'okabe',
          pending_count: 0,
          processing_count: 0,
          failed_count: 0,
        });
      }
      return okJson({});
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={() => undefined}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('索引'));
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('档案里的黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-delete-record')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('archive-delete-record'));
    fireEvent.click(screen.getByTestId('archive-delete-confirm-button'));
    await waitFor(() => {
      expect(screen.queryByTestId('archive-delete-confirm')).toBeNull();
    });
    // Back to Constellation while the authoritative refresh is still pending:
    // NO old LIST row, reading card, or projection summary may be readable.
    fireEvent.click(screen.getByTestId('memory-mode-constellation'));
    await waitFor(() => {
      expect(screen.getByTestId('memory-erasure-sync')).toBeTruthy();
    });
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
    expect(screen.queryByTestId('memory-result-outline')).toBeNull();
    expect(screen.queryByTestId('reading-title')).toBeNull();
    expect(screen.queryByTestId('memory-truncation')).toBeNull();
    // The authoritative envelope finally arrives: it replaces the hold.
    releaseProjection?.(okJson({
      ...projection,
      composition: { ...projection.composition, active_facts: 0 },
      eligible: { nodes: 2, edges: 1, records: 0, results: 0 },
      shown: { nodes: 2, edges: 1, records: 0, results: 0 },
      empty: false,
      result_ids: [],
      nodes: projection.nodes.filter((node) => node.kind !== 'fact'),
      edges: projection.edges.filter((edge) => !edge.from.startsWith('fact:')),
    }));
    await waitFor(() => {
      expect(screen.queryByTestId('memory-erasure-sync')).toBeNull();
    });
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
  });

  it('a failed erasure refresh never resurrects the deleted record', async () => {
    let deletedRef = { value: false };
    mockApi((url, init) => {
      if (
        url.startsWith('/api/memory/facts/1?')
        && (init as RequestInit | undefined)?.method === 'DELETE'
      ) {
        deletedRef.value = true;
        return okJson({ fact_id: '1', deleted: true });
      }
      const archive = archiveBody(url, deletedRef);
      if (archive) return archive;
      if (url.includes('/api/memory/graph/projection')) {
        // First load: original envelope. Post-erasure refresh: FAILS — the
        // old envelope must NOT be restored as a readable fallback.
        if (!deletedRef.value) return okJson(projection);
        return Promise.reject(new Error('projection refresh failed'));
      }
      if (url.includes('/api/memory/status')) {
        return okJson({
          session_id: 's',
          worldline: 'steins_gate',
          identity_mode: 'okabe',
          pending_count: 0,
          processing_count: 0,
          failed_count: 0,
        });
      }
      return okJson({});
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={() => undefined}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('索引'));
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('档案里的黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-delete-record')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('archive-delete-record'));
    fireEvent.click(screen.getByTestId('archive-delete-confirm-button'));
    await waitFor(() => {
      expect(screen.queryByTestId('archive-delete-confirm')).toBeNull();
    });
    fireEvent.click(screen.getByTestId('memory-mode-constellation'));
    await waitFor(() => {
      expect(screen.queryByTestId('memory-erasure-sync')).toBeNull();
    });
    // The failed refresh dropped the stale envelope; the deleted record is
    // NOT resurrected through the previous-truth fallback.
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
    expect(screen.queryByTestId('reading-title')).toBeNull();
  });

  it('the direct status read reuses the strict parser: a malformed summary is dropped', async () => {
    mockApi((url) => {
      if (url.includes('/api/memory/graph/projection')) return okJson(projection);
      if (url.includes('/api/memory/status')) {
        // Scope echo is correct, but the payload is missing failed_jobs and
        // two count fields — the strict shared parser must reject it.
        // (Checked BEFORE archiveBody, which has its own status fixture.)
        return okJson({
          session_id: 's',
          worldline: 'steins_gate',
          identity_mode: 'okabe',
          failed_count: 9,
        });
      }
      const archive = archiveBody(url);
      if (archive) return archive;
      return okJson({});
    });
    render(
      <MemorySpace
        sessionId="s"
        chatWorldline="steins_gate"
        chatIdentityMode="okabe"
        onClose={() => undefined}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    // Open the SOUL inspector so the jobs summary surface is observable.
    fireEvent.click(screen.getByTestId('select-soul'));
    await waitFor(() => {
      expect(screen.getByTestId('soul-inspector')).toBeTruthy();
    });
    // The inspector renders projection data, but the malformed status
    // summary is dropped: no jobs badge built from it.
    expect(screen.getByTestId('inspector-composition')).toBeTruthy();
    // Give any (incorrectly) accepted summary time to land before asserting.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByTestId('inspector-jobs')).toBeNull();
  });

  it('StrictMode remount: Archive exits loading once the held responses land', async () => {
    // Shortest repro of the runtime RED: <React.StrictMode> double-mounts
    // MemorySpace; the FIRST cleanup disposes the shared Archive module, and
    // a second open() on the same instance must still reach a settled state.
    const held: Array<{ url: string; resolve: (v: unknown) => void }> = [];
    let released = false;
    const factRowBody = {
      fact_id: '1',
      display_text: '档案里的黑咖啡',
      version_no: 1,
      active_version: 1,
      is_pinned: false,
      topic_id: 't1',
      updated_at: '2026-08-14T00:00:00Z',
    };
    const bodyFor = (url: string): Record<string, unknown> | null => {
      if (url.startsWith('/api/memory/facts?')) {
        return {
          facts: [factRowBody],
          scope: scopeBody,
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        };
      }
      if (url.startsWith('/api/memory/experiences?')) {
        return {
          experiences: [],
          scope: scopeBody,
          pagination: { limit: 20, offset: 0, total: 0, has_more: false },
        };
      }
      if (url.startsWith('/api/memory/topics?')) {
        return { scope: scopeBody, topics: [{ topic_id: 't1', display_label: '咖啡', fact_count: 1 }] };
      }
      if (url.startsWith('/api/memory/observations?')) {
        return {
          scope: scopeBody,
          observations: [],
          pagination: { limit: 20, offset: 0, total: 0, has_more: false },
        };
      }
      if (url.startsWith('/api/memory/status?')) {
        return {
          session_id: 's',
          worldline: 'steins_gate',
          identity_mode: 'okabe',
          pending_count: 0,
          processing_count: 0,
          failed_count: 0,
          failed_jobs: [],
        };
      }
      return null;
    };
    mockApi((url) => {
      if (url.includes('graph/projection')) return okJson(projection);
      const body = bodyFor(String(url));
      if (body === null) return okJson({});
      if (released) return okJson(body);
      return new Promise((resolve) => {
        held.push({ url: String(url), resolve });
      });
    });
    render(
      <React.StrictMode>
        <MemorySpace
          sessionId="s"
          chatWorldline="steins_gate"
          chatIdentityMode="okabe"
          onClose={() => undefined}
        />
      </React.StrictMode>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('memory-space')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-view')).toBeTruthy();
    });
    // The Archive requests are held in flight: loading is the honest state.
    expect(screen.getByText('正在读取事实…')).toBeTruthy();
    // Release the held responses with real contract-shaped payloads.
    released = true;
    for (const h of held.splice(0)) {
      h.resolve(okJson(bodyFor(h.url) as Record<string, unknown>));
    }
    // THE FIX UNDER TEST: Archive must leave loading and present results —
    // the StrictMode-disposed module must recover on the explicit re-open.
    await waitFor(
      () => {
        expect(screen.queryByText('正在读取事实…')).toBeNull();
      },
      { timeout: 2000 },
    );
    expect(screen.getByTestId('archive-facts-total').textContent).toContain('1');
    expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
  });

  it('after an Archive deletion, Constellation shows no resurrected row or old reading card', async () => {
    const deletedRef = { value: false };
    mountMemorySpace(deletedRef);
    await waitFor(() => {
      expect(screen.getByTestId('memory-graph')).toBeTruthy();
    });
    // Constellation LIST first shows the record from the backend projection.
    fireEvent.click(screen.getByText('索引'));
    await waitFor(() => {
      expect(screen.getByText('喜欢黑咖啡')).toBeTruthy();
    });
    // Delete the same record through the Archive peer mode.
    fireEvent.click(screen.getByTestId('memory-mode-archive'));
    await waitFor(() => {
      expect(screen.getByText('档案里的黑咖啡')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('档案里的黑咖啡'));
    await waitFor(() => {
      expect(screen.getByTestId('archive-delete-record')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('archive-delete-record'));
    fireEvent.click(screen.getByTestId('archive-delete-confirm-button'));
    await waitFor(() => {
      expect(screen.queryByTestId('archive-delete-confirm')).toBeNull();
    });
    // Back to Constellation: the backend-refreshed projection no longer
    // contains the record — no old row, no old text, no old reading card.
    fireEvent.click(screen.getByTestId('memory-mode-constellation'));
    await waitFor(() => {
      expect(screen.queryByTestId('archive-view')).toBeNull();
    });
    expect(screen.queryByText('喜欢黑咖啡')).toBeNull();
    expect(screen.queryByText('档案里的黑咖啡')).toBeNull();
    expect(screen.queryByTestId('reading-title')).toBeNull();
  });
});
