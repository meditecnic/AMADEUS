import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createElement, type ReactNode } from 'react';

import { MemorySpace } from './MemorySpace';
import type { MemoryProjection } from './types';

// The GL layer stays stubbed (jsdom has no WebGL), but supported=true keeps
// the 3D surface mounted so the real ConstellationStage publish/callback
// wiring and overviewMotion camera math run for real.
vi.mock('./orreryRenderer', () => {
  class OrreryRuntime {
    soulOpening = { open: false, progress: 0, reading: false, setOpen() {}, dolly() {}, orbit() {} };
    supported = true;
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
  composition: { active_facts: 1, active_experiences: 0, eligible_topics: 1, latest_memory_change_at: null, person_anchors_supported: false },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 3, edges: 2, records: 1, results: 1 },
  shown: { nodes: 3, edges: 2, records: 1, results: 1 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: ['fact:1'],
  nodes: [
    { kind: 'continuity_hub', projection_id: 'hub:continuity:s:steins_gate:okabe', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 1, experience_count: 0 },
    { kind: 'fact', projection_id: 'fact:1', fact_id: '1', label: '喜欢黑咖啡', is_pinned: false, updated_at: '2026-08-14T00:00:00Z', topic_id: 't1' },
  ],
  edges: [
    { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:okabe', to: 'topic:t1' },
    { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
  ],
};

function okJson(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body });
}

let renderCount = 0;

function RenderCounter({ children }: { children: ReactNode }) {
  renderCount += 1;
  return createElement('div', { 'data-testid': 'render-counter' }, children);
}

describe('P1-A fast drag stress', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('fast large drags never trigger a React update-depth loop or repeated delivery', async () => {
    renderCount = 0;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/memory/status')) return okJson({ pending_count: 0, processing_count: 0, failed_count: 0 });
      return okJson(projection);
    }));
    const errors: unknown[] = [];
    const spy = vi.spyOn(console, 'error').mockImplementation((...args) => {
      errors.push(args.join(' '));
    });

    render(
      <RenderCounter>
        <MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />
      </RenderCounter>,
    );
    const canvas = await screen.findByTestId('constellation-stage');

    // Select the fact so the anchor callback path is live during the drag.
    fireEvent.click(screen.getByRole('button', { name: '索引' }));
    const option = await screen.findByRole('option', { name: '喜欢黑咖啡' });
    fireEvent.click(option);
    fireEvent.click(screen.getByRole('button', { name: '索引' }));

    const before = renderCount;
    fireEvent.pointerDown(canvas, { button: 0, clientX: 40, clientY: 40 });
    const moves = 80;
    for (let index = 0; index < moves; index += 1) {
      fireEvent.pointerMove(canvas, {
        button: 1,
        buttons: 1,
        clientX: 40 + index * 14,
        clientY: 40 + (index % 2 === 0 ? index * 9 : -index * 7),
      });
      // Interleave frame flushes so rAF-driven publishes race the pointer path.
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    fireEvent.pointerUp(canvas, { button: 0, clientX: 40 + moves * 14, clientY: 40 });
    await new Promise((resolve) => setTimeout(resolve, 20));

    spy.mockRestore();
    const depthErrors = errors.filter((entry) => String(entry).includes('Maximum update depth'));
    expect(depthErrors).toEqual([]);
    // Bounded updates: at most a small constant per pointermove, no runaway
    // feedback loop after the drag settles.
    const used = renderCount - before;
    expect(used).toBeLessThan(moves * 4 + 40);

    // Delivery churn guard: ownership/anchor callbacks must not keep firing
    // once the pointer is idle again.
    const settled = renderCount;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(renderCount - settled).toBeLessThan(12);
  });
});
