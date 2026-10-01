import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConstellationStage } from './ConstellationStage';
import { deriveOverviewPresentation } from './overviewPresentation';
import type { MemoryProjection } from './types';

const facts = [1, 2, 3, 4, 5].map((n) => ({
  kind: 'fact' as const,
  projection_id: `fact:${n}`,
  fact_id: String(n),
  label: `事实${n}`,
  is_pinned: false,
  updated_at: '2026-08-19T00:00:00Z',
  topic_id: 't1',
}));

const projection: MemoryProjection = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  view: 'overview',
  projection_version: 'memory-projection-v1',
  generated_at: '2026-08-19T00:00:00Z',
  criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
  center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
  composition: {
    active_facts: 5,
    active_experiences: 0,
    eligible_topics: 1,
    latest_memory_change_at: null,
    person_anchors_supported: false,
  },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 7, edges: 6, records: 5, results: 5 },
  shown: { nodes: 7, edges: 6, records: 5, results: 5 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: facts.map((item) => item.projection_id),
  nodes: [
    { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 5, experience_count: 0 },
    ...facts,
  ],
  edges: [
    { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
    ...facts.map((item) => ({ kind: 'has_topic' as const, from: item.projection_id, to: 'topic:t1' })),
  ],
};

describe('ConstellationStage camera ownership', () => {
  afterEach(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    cleanup();
  });

  it('keeps material motion alive after dragging and when a node is selected', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan, selectedId: null as string | null, reducedMotion: false,
      expandedTopicIds: [] as string[], worldline: 'steins_gate' as const,
      query: '', nowMs: 0, onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { button: 0, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { clientX: 90, clientY: 26 });
    fireEvent.pointerUp(canvas, { button: 0, clientX: 90, clientY: 26 });
    expect(canvas).toHaveAttribute('data-ownership', 'user');
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    rerender(<ConstellationStage {...props} selectedId="topic:t1" nowMs={1500} />);
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    rerender(<ConstellationStage {...props} selectedId="topic:t1" reducedMotion nowMs={2000} />);
    expect(canvas).toHaveAttribute('data-ambient', 'settled');
  });

  it('29. pointer drag produces userCamera without idle yaw', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    render(
      <ConstellationStage
        plan={plan}
        selectedId={null}
        reducedMotion={false}
        expandedTopicIds={[]}
        worldline="steins_gate"
        query=""
        onSelect={vi.fn()}
      />,
    );
    const canvas = screen.getByTestId('constellation-stage');
    expect(canvas).toHaveAttribute('data-ownership', 'rest');
    const before = canvas.getAttribute('data-yaw');
    fireEvent.pointerDown(canvas, { clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { clientX: 80, clientY: 24 });
    expect(canvas.getAttribute('data-ownership')).toBe('user');
    expect(canvas.getAttribute('data-yaw')).not.toBe(before);
    const userYaw = canvas.getAttribute('data-yaw');
    fireEvent.pointerUp(canvas, { clientX: 80, clientY: 24 });
    expect(canvas.getAttribute('data-yaw')).toBe(userYaw);
    expect(canvas.getAttribute('data-ownership')).toBe('user');
    const zoomBefore = canvas.getAttribute('data-zoom');
    fireEvent.wheel(canvas, { deltaY: 400 });
    expect(canvas.getAttribute('data-zoom')).not.toBe(zoomBefore);
    expect(canvas.getAttribute('data-ownership')).toBe('user');
  });

  it('keeps ambient alive while pointer hold pauses camera automation', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan,
      selectedId: null,
      reducedMotion: false,
      expandedTopicIds: [] as string[],
      worldline: 'steins_gate' as const,
      query: '',
      nowMs: 0,
      onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    expect(canvas).toHaveAttribute('data-ownership', 'rest');

    fireEvent.pointerDown(canvas, { button: 0, clientX: 4, clientY: 4 });
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    expect(canvas).toHaveAttribute('data-ownership', 'rest');
    const heldYaw = canvas.getAttribute('data-yaw');
    rerender(<ConstellationStage {...props} nowMs={5200} />);
    expect(screen.getByTestId('constellation-stage')).toHaveAttribute('data-yaw', heldYaw);
    fireEvent.pointerUp(canvas, { button: 0, clientX: 4, clientY: 4 });
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');

    fireEvent.pointerDown(canvas, { button: 2, clientX: 8, clientY: 8 });
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    expect(canvas).toHaveAttribute('data-ownership', 'rest');
    fireEvent.pointerCancel(canvas, { button: 2, clientX: 8, clientY: 8 });
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    expect(canvas).toHaveAttribute('data-ownership', 'rest');
  });

  it('releases an active pointer hold when the pointer leaves the canvas', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan,
      selectedId: null,
      reducedMotion: false,
      expandedTopicIds: [] as string[],
      worldline: 'steins_gate' as const,
      query: '',
      nowMs: 0,
      onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { button: 0, buttons: 1, clientX: 8, clientY: 8 });
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    fireEvent.pointerLeave(canvas, { buttons: 1, clientX: -4, clientY: 8 });
    expect(canvas).toHaveAttribute('data-ambient', 'pulse');
    const releasedYaw = canvas.getAttribute('data-yaw');
    rerender(<ConstellationStage {...props} nowMs={5200} />);
    expect(canvas.getAttribute('data-yaw')).not.toBe(releasedYaw);
    expect(canvas).toHaveAttribute('data-ownership', 'rest');
  });

  it('starts the idle delay from a late stage mount clock sample', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan,
      selectedId: null,
      reducedMotion: false,
      expandedTopicIds: [] as string[],
      worldline: 'steins_gate' as const,
      query: '',
      nowMs: 10_000,
      onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    const restYaw = canvas.getAttribute('data-yaw');
    rerender(<ConstellationStage {...props} nowMs={10_016} />);
    expect(canvas.getAttribute('data-yaw')).toBe(restYaw);
    rerender(<ConstellationStage {...props} nowMs={15_200} />);
    expect(canvas.getAttribute('data-yaw')).not.toBe(restYaw);
  });

  it('right-click without drag unwinds and does not open a context menu', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const onSelect = vi.fn();
    const onUnwind = vi.fn();
    render(
      <ConstellationStage
        plan={plan}
        selectedId="topic:t1"
        reducedMotion
        expandedTopicIds={['topic:t1']}
        worldline="steins_gate"
        query=""
        onSelect={onSelect}
        onUnwind={onUnwind}
      />,
    );
    const canvas = screen.getByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { button: 2, clientX: 40, clientY: 40 });
    const menu = fireEvent.contextMenu(canvas, { clientX: 41, clientY: 41 });
    expect(menu).toBe(false);
    expect(onUnwind).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
    expect(canvas.getAttribute('data-ownership')).not.toBe('user');
  });

  it('right-drag does not unwind or take user camera ownership', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const onUnwind = vi.fn();
    render(
      <ConstellationStage
        plan={plan}
        selectedId={null}
        reducedMotion={false}
        expandedTopicIds={[]}
        worldline="steins_gate"
        query=""
        onSelect={vi.fn()}
        onUnwind={onUnwind}
      />,
    );
    const canvas = screen.getByTestId('constellation-stage');
    const yaw = canvas.getAttribute('data-yaw');
    fireEvent.pointerDown(canvas, { button: 2, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { buttons: 2, clientX: 90, clientY: 28 });
    fireEvent.contextMenu(canvas, { clientX: 90, clientY: 28 });
    fireEvent.pointerUp(canvas, { button: 2, clientX: 90, clientY: 28 });
    expect(onUnwind).not.toHaveBeenCalled();
    expect(canvas.getAttribute('data-ownership')).not.toBe('user');
    expect(canvas.getAttribute('data-yaw')).toBe(yaw);
  });

  it('a background miss does not call onSelect', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const onSelect = vi.fn();
    render(
      <ConstellationStage
        plan={plan}
        selectedId="topic:t1"
        reducedMotion
        expandedTopicIds={['topic:t1']}
        worldline="steins_gate"
        query=""
        onSelect={onSelect}
      />,
    );
    const canvas = screen.getByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { clientX: 4, clientY: 4 });
    fireEvent.pointerMove(canvas, { clientX: 7, clientY: 6 });
    fireEvent.pointerUp(canvas, { clientX: 7, clientY: 6 });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('ordinary pointer jitter does not take user camera ownership', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    render(
      <ConstellationStage
        plan={plan}
        selectedId={null}
        reducedMotion={false}
        expandedTopicIds={[]}
        worldline="steins_gate"
        query=""
        onSelect={vi.fn()}
      />,
    );
    const canvas = screen.getByTestId('constellation-stage');
    const yaw = canvas.getAttribute('data-yaw');
    fireEvent.pointerDown(canvas, { clientX: 20, clientY: 20 });
    fireEvent.pointerMove(canvas, { clientX: 24, clientY: 22 });
    fireEvent.pointerUp(canvas, { clientX: 24, clientY: 22 });
    expect(canvas.getAttribute('data-ownership')).not.toBe('user');
    expect(canvas.getAttribute('data-yaw')).toBe(yaw);
  });

  it('30. advancing nowMs after pointer-up does not resume yaw', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan,
      selectedId: null,
      reducedMotion: false,
      expandedTopicIds: [] as string[],
      worldline: 'steins_gate' as const,
      query: '',
      nowMs: 0,
      onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10 });
    fireEvent.pointerMove(canvas, { clientX: 70, clientY: 12 });
    fireEvent.pointerUp(canvas, { clientX: 70, clientY: 12 });
    const yaw = canvas.getAttribute('data-yaw');
    expect(canvas).toHaveAttribute('data-ownership', 'user');
    rerender(<ConstellationStage {...props} nowMs={2500} />);
    expect(screen.getByTestId('constellation-stage').getAttribute('data-yaw')).toBe(yaw);
    expect(screen.getByTestId('constellation-stage')).toHaveAttribute('data-ownership', 'user');
    rerender(<ConstellationStage {...props} nowMs={8000} />);
    const later = screen.getByTestId('constellation-stage');
    expect(Math.abs(Number(later.getAttribute('data-yaw')) - Number(yaw))).toBeLessThan(0.09);
    expect(later.getAttribute('data-zoom')).toBe(canvas.getAttribute('data-zoom'));
  });

  it('hidden document pauses the clock and does not catch up when visible again', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan,
      selectedId: null,
      reducedMotion: false,
      expandedTopicIds: [] as string[],
      worldline: 'steins_gate' as const,
      query: '',
      nowMs: 0,
      onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    const restYaw = canvas.getAttribute('data-yaw');
    expect(canvas.getAttribute('data-clock')).toBe('running');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(screen.getByTestId('constellation-stage').getAttribute('data-clock')).toBe('paused');
    expect(screen.getByTestId('constellation-stage').getAttribute('data-yaw')).toBe(restYaw);
    rerender(<ConstellationStage {...props} nowMs={8000} />);
    expect(screen.getByTestId('constellation-stage').getAttribute('data-yaw')).toBe(restYaw);
    expect(screen.getByTestId('constellation-stage').getAttribute('data-clock')).toBe('paused');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(screen.getByTestId('constellation-stage').getAttribute('data-clock')).toBe('running');
    rerender(<ConstellationStage {...props} nowMs={8020} />);
    const after = screen.getByTestId('constellation-stage');
    expect(Math.abs(Number(after.getAttribute('data-yaw')) - Number(restYaw))).toBeLessThan(0.01);
  });

  it('31. wheel zoom then advancing nowMs does not start idle yaw', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: [] });
    const props = {
      plan,
      selectedId: null,
      reducedMotion: false,
      expandedTopicIds: [] as string[],
      worldline: 'steins_gate' as const,
      query: '',
      nowMs: 10,
      onSelect: vi.fn(),
    };
    const { rerender } = render(<ConstellationStage {...props} />);
    const canvas = screen.getByTestId('constellation-stage');
    fireEvent.wheel(canvas, { deltaY: 300 });
    const yaw = canvas.getAttribute('data-yaw');
    const zoom = canvas.getAttribute('data-zoom');
    expect(canvas).toHaveAttribute('data-ownership', 'user');
    rerender(<ConstellationStage {...props} nowMs={4000} />);
    const later = screen.getByTestId('constellation-stage');
    expect(later.getAttribute('data-yaw')).toBe(yaw);
    expect(later.getAttribute('data-zoom')).toBe(zoom);
  });
});

describe('ConstellationStage presentation display', () => {
  afterEach(() => cleanup());

  it('uses the shared plan evidence set even when query would reveal every result', () => {
    const plan = deriveOverviewPresentation(projection, { expandedTopicIds: ['topic:t1'] });
    const { rerender } = render(
      <ConstellationStage
        plan={plan}
        selectedId={null}
        reducedMotion
        expandedTopicIds={['topic:t1']}
        worldline="steins_gate"
        query="事实"
        onSelect={vi.fn()}
      />,
    );
    const first = screen.getByTestId('constellation-stage').getAttribute('data-visible-evidence');
    expect(first?.split(',').filter(Boolean).sort()).toEqual(['fact:1', 'fact:2', 'fact:3']);
    expect(first).not.toContain('fact:5');
    const nextPlan = deriveOverviewPresentation(
      { ...projection, generated_at: '2026-08-19T01:00:00Z' },
      { expandedTopicIds: ['topic:t1'] },
    );
    rerender(
      <ConstellationStage
        plan={nextPlan}
        selectedId={null}
        reducedMotion
        expandedTopicIds={['topic:t1']}
        worldline="steins_gate"
        query="事实"
        onSelect={vi.fn()}
      />,
    );
    const next = screen.getByTestId('constellation-stage').getAttribute('data-visible-evidence');
    expect(next?.split(',').filter(Boolean).sort()).toEqual(['fact:1', 'fact:2', 'fact:3']);
    expect(next).not.toContain('fact:5');
  });
});
