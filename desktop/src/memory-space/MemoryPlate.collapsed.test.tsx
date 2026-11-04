import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel, plateGeometry, platePoint, type ChartModel } from './chartCoordinates';
import { MemoryPlate, type MemoryPlateProps } from './MemoryPlate';
import type { MemoryChart } from './types';

const TODAY = Date.parse('2026-11-04T04:00:00Z');
const charts = new Map<string, MemoryChart>();
function chartFor(preset: string): MemoryChart {
  if (!charts.has(preset)) {
    const store = createMemoryStore({ facts: [], topics: [], preset });
    charts.set(preset, handleMemory(new URL('http://mock/api/memory/chart?session_id=s&worldline=steins_gate&identity_mode=okabe'), 'GET', {}, store)!.body as MemoryChart);
  }
  return charts.get(preset)!;
}
const modelFor = (preset: string, lastOpen: number | null = null, wasCollapsed?: boolean) =>
  buildChartModel(chartFor(preset), { today: TODAY, lastOpen, wasCollapsed });

export function plate(overrides: Partial<MemoryPlateProps> = {}) {
  const props: MemoryPlateProps = {
    model: modelFor('huge'), soul: 'ready', width: 1440, height: 832, selection: null, focusTopic: null, framedTopic: null,
    reducedMotion: true, entering: false, onEntered: vi.fn(), onSelect: vi.fn(), onOverview: vi.fn(), ...overrides,
  };
  const view = render(<MemoryPlate {...props} />);
  return { ...view, props };
}

afterEach(cleanup);

describe('collapsed plate (step 2)', () => {
  const huge = modelFor('huge', Date.parse('2026-10-28T00:00:00Z'));

  it('is a 2000-record, 30-topic library drawn collapsed', () => {
    expect(huge.stars).toHaveLength(2000);
    expect(huge.collapsed).toBe(true);
    expect(huge.topics).toHaveLength(30);
    expect(huge.stars.filter((s) => s.resident)).toHaveLength(32);
    expect(huge.stars.filter((s) => s.isNew).length).toBeGreaterThan(5);
    expect(huge.stars.filter((s) => s.kind === 'fragment')).toHaveLength(250);
  });

  it('draws one ribbon of ticks per group and a star only for each single, not 2000 stars', () => {
    const { container } = plate({ model: huge });
    const singles = huge.stars.filter((s) => s.single);
    expect(container.querySelectorAll('.p-star')).toHaveLength(singles.length);
    expect(singles.length).toBeLessThan(120);
    const ribbons = [...container.querySelectorAll<SVGPathElement>('.p-ribbon')];
    expect(ribbons.length).toBeLessThanOrEqual(huge.groups.length);
    expect(ribbons.length).toBeGreaterThan(25);
    const ticks = ribbons.reduce((sum, el) => sum + (el.getAttribute('d')!.match(/M/g) ?? []).length, 0);
    expect(ticks).toBe(huge.stars.length - singles.length);
    expect(container.querySelectorAll('.p-ribbon-hit')).toHaveLength(ribbons.length);
    expect(container.querySelectorAll('*').length).toBeLessThan(900);
    expect(container.querySelectorAll('.p-line')).toHaveLength(0);
  });

  it('puts fragment ticks on a dotted ribbon and ungrouped ones on a faint one', () => {
    const { container } = plate({ model: huge });
    expect(container.querySelectorAll('.p-ribbon.is-fragment')).toHaveLength(1);
    expect(container.querySelectorAll('.p-ribbon.is-loose')).toHaveLength(1);
    expect(container.querySelectorAll('.p-ribbon.is-topic').length).toBeGreaterThan(25);
  });

  it('draws the open group as stars with its line, and its ticks give way', () => {
    const topic = huge.groups.find((g) => g.isTopic && g.starIds.length > 40)!;
    const { container } = plate({ model: huge, focusTopic: topic.id, framedTopic: topic.id });
    const stars = [...container.querySelectorAll('.p-star')].map((el) => el.getAttribute('data-id'));
    for (const id of topic.starIds) expect(stars).toContain(id);
    expect(container.querySelector(`.p-ribbon[data-group="${topic.id}"]`)).toBeNull();
    expect(container.querySelectorAll('.p-line')).toHaveLength(1);
    expect(container.querySelectorAll('.p-star.lit')).toHaveLength(topic.starIds.length);
    expect(container.querySelector('.p-root')).toHaveClass('dim');
  });

  it('draws the selected and this-turn-referenced records as stars even when they are ticks', () => {
    const ordinary = huge.stars.filter((s) => !s.single);
    const [a, b] = [ordinary[100], ordinary[900]];
    const { container } = plate({ model: huge, selection: { kind: 'star', id: a.id }, marks: [b.id] });
    const stars = [...container.querySelectorAll('.p-star')].map((el) => el.getAttribute('data-id'));
    expect(stars).toContain(a.id);
    expect(stars).toContain(b.id);
    expect(container.querySelectorAll('[data-testid="plate-ref"]')).toHaveLength(1);
  });

  it('draws a chart under the line exactly as before', () => {
    const mid = modelFor('mid');
    expect(mid.stars).toHaveLength(140);
    expect(mid.collapsed).toBe(false);
    const { container } = plate({ model: mid });
    expect(container.querySelectorAll('.p-star')).toHaveLength(140);
    expect(container.querySelectorAll('.p-ribbon, .p-ribbon-hit')).toHaveLength(0);
    expect(container.querySelectorAll('.p-line')).toHaveLength(mid.topics.filter((t) => t.starIds.length).length);
    expect(container.querySelector('.fig, .p-legend .fig')).toHaveTextContent(/每圈一个月 · 越往外越近$/);
  });

  it('keeps the collapsed state across 130 to 150 records the way the caller passes it', () => {
    expect(modelFor('mid', null, true).collapsed).toBe(true);
    expect(modelFor('mid', null, false).collapsed).toBe(false);
  });
});

/** Client point (the svg sits at 0,0 in jsdom) of a month on a group's ribbon. */
function pointOn(model: ChartModel, groupId: string, theta: number, view = { s: 1, x: 0, y: 0 }) {
  const g = plateGeometry(1440, 832);
  const group = model.groups.find((t) => t.id === groupId)!;
  const p = platePoint(theta, group.phi, g.radius, model.thetaFit);
  return { clientX: g.cx + view.x + p.x * view.s, clientY: g.cy + view.y + p.y * view.s };
}
const busiest = (model: ChartModel) => [...model.cells.values()]
  .filter((c) => c.collapsedIds.length > 4 && model.groups.find((t) => t.id === c.groupId)!.isTopic)
  .sort((a, b) => b.collapsedIds.length - a.collapsedIds.length)[0];

describe('collapsed plate (step 3)', () => {
  const huge = modelFor('huge');

  it('reads the month under the pointer: hover names the cell, a click selects it', () => {
    const cell = busiest(huge);
    const { container, props } = plate({ model: huge });
    const hit = container.querySelector(`.p-ribbon-hit[data-group="${cell.groupId}"]`)!;
    const at = pointOn(huge, cell.groupId, (cell.band.theta0 + cell.band.theta1) / 2);
    fireEvent.pointerMove(hit, at);
    const label = huge.groups.find((t) => t.id === cell.groupId)!.label;
    expect(screen.getByTestId('plate-band-hover')).toHaveTextContent(`${label} · ${cell.band.key.replace('-', '.')} · ${cell.starIds.length} 条点一下展开`);
    fireEvent.click(hit, at);
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'cell', key: cell.key });
    fireEvent.pointerLeave(hit);
    expect(screen.queryByTestId('plate-band-hover')).toBeNull();
  });

  it('a click on the ribbon between months opens the topic instead', () => {
    const topic = huge.groups.find((t) => t.isTopic && t.cells.length)!;
    const { container, props } = plate({ model: huge });
    const hit = container.querySelector(`.p-ribbon-hit[data-group="${topic.id}"]`)!;
    fireEvent.click(hit, pointOn(huge, topic.id, huge.thetaToday + 20));
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'topic', id: topic.id });
  });

  it('undoes the view scale and shift when it reads the month', () => {
    const cell = busiest(huge);
    const { container, props } = plate({ model: huge, focusTopic: 'elsewhere', framedTopic: huge.groups.find((t) => t.id !== cell.groupId && t.isTopic && t.starIds.length)!.id });
    const view = { s: Number(container.querySelector('.plate')!.getAttribute('data-view-scale')), x: 0, y: 0 };
    const transform = container.querySelector('.p-root')!.getAttribute('transform')!.match(/translate\(([-\d.]+) ([-\d.]+)\) scale/)!;
    const g = plateGeometry(1440, 832);
    expect(view.s).toBeCloseTo(1.06);
    const hit = container.querySelector(`.p-ribbon-hit[data-group="${cell.groupId}"]`)!;
    fireEvent.click(hit, pointOn(huge, cell.groupId, (cell.band.theta0 + cell.band.theta1) / 2, { s: view.s, x: Number(transform[1]) - g.cx, y: Number(transform[2]) - g.cy }));
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'cell', key: cell.key });
  });

  it('frames a collapsed group by 1.06 and a plate under the line by 1.1', () => {
    const topic = huge.groups.find((t) => t.isTopic && t.starIds.length)!;
    const a = plate({ model: huge, focusTopic: topic.id, framedTopic: topic.id });
    expect(a.container.querySelector('.plate')!.getAttribute('data-view-scale')).toBe('1.060');
    a.unmount();
    const mid = modelFor('mid');
    const b = plate({ model: mid, focusTopic: mid.topics[2].id, framedTopic: mid.topics[2].id });
    expect(b.container.querySelector('.plate')!.getAttribute('data-view-scale')).toBe('1.100');
  });

  it('frames the ungrouped group too, which has no topic of its own', () => {
    const { container } = plate({ model: huge, focusTopic: '@loose', framedTopic: '@loose' });
    expect(container.querySelector('.plate')!.getAttribute('data-view-scale')).toBe('1.060');
    expect(container.querySelectorAll('.p-star.lit').length).toBe(huge.groups.find((t) => t.id === '@loose')!.starIds.length);
    expect(container.querySelector('.p-ribbon[data-group="@loose"]')).toBeNull();
  });

  it('frames a selected cell: other months of the open group fade and a dashed fan outlines the cell', () => {
    const cell = busiest(huge);
    const { container } = plate({ model: huge, selection: { kind: 'cell', key: cell.key }, focusTopic: cell.groupId, framedTopic: cell.groupId });
    expect(screen.getByTestId('plate-cellbox')).toHaveAttribute('d', expect.stringMatching(/^M.*Z$/));
    const lit = [...container.querySelectorAll('.p-star.lit')];
    const faded = lit.filter((el) => el.classList.contains('out')).map((el) => el.getAttribute('data-id'));
    const group = huge.groups.find((t) => t.id === cell.groupId)!;
    expect(lit).toHaveLength(group.starIds.length);
    expect(faded.sort()).toEqual(group.starIds.filter((id) => huge.byId.get(id)!.cellKey !== cell.key).sort());
    expect(faded.length).toBeGreaterThan(0);
    for (const id of cell.starIds) expect(container.querySelector(`[data-id="${id}"]`)).not.toHaveClass('out');
  });

  describe('opening and closing a group', () => {
    it('draws the ticks back into the stars over 420 ms, and out again over 260 ms', () => {
      vi.useFakeTimers();
      const topic = huge.groups.find((t) => t.isTopic && t.starIds.length > 30)!;
      const first = plate({ model: huge, reducedMotion: false });
      const { rerender, props, container } = first;
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
      rerender(<MemoryPlate {...props} reducedMotion={false} focusTopic={topic.id} framedTopic={topic.id} />);
      expect(screen.getByTestId('plate-unfurl')).toHaveAttribute('data-dir', 'out');
      expect(container.querySelector('.plate')).toHaveClass('is-unfurling');
      expect(container.querySelector(`.p-ribbon[data-group="${topic.id}"]`)).toBeNull();
      const start = screen.getByTestId('plate-unfurl').getAttribute('d')!;
      expect((start.match(/M/g) ?? []).length).toBe(topic.cells.reduce((n, c) => n + c.collapsedIds.length, 0));
      act(() => { vi.advanceTimersByTime(200); });
      expect(screen.getByTestId('plate-unfurl').getAttribute('d')).not.toBe(start);
      act(() => { vi.advanceTimersByTime(300); });
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
      expect(container.querySelector('.plate')).not.toHaveClass('is-unfurling');
      rerender(<MemoryPlate {...props} reducedMotion={false} focusTopic={null} framedTopic={null} />);
      expect(screen.getByTestId('plate-unfurl')).toHaveAttribute('data-dir', 'in');
      expect(container.querySelector(`.p-ribbon[data-group="${topic.id}"]`)).toBeNull();
      act(() => { vi.advanceTimersByTime(200); });
      expect(screen.getByTestId('plate-unfurl')).toBeInTheDocument();
      act(() => { vi.advanceTimersByTime(100); });
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
      expect(container.querySelector(`.p-ribbon[data-group="${topic.id}"]`)).not.toBeNull();
      vi.useRealTimers();
    });

    it('closes the group that was open at once when another opens, and takes 1.3 times longer in β', () => {
      vi.useFakeTimers();
      const [a, b] = huge.groups.filter((t) => t.isTopic && t.starIds.length > 30);
      const { rerender, props, container } = plate({ model: huge, reducedMotion: false, worldline: 'beta', focusTopic: a.id, framedTopic: a.id });
      rerender(<MemoryPlate {...props} reducedMotion={false} worldline="beta" focusTopic={b.id} framedTopic={b.id} />);
      expect(container.querySelector(`.p-ribbon[data-group="${a.id}"]`)).not.toBeNull();
      expect(container.querySelector(`.p-ribbon[data-group="${b.id}"]`)).toBeNull();
      expect(screen.getByTestId('plate-unfurl')).toHaveAttribute('data-dir', 'out');
      expect(container.querySelector('.plate')).toHaveStyle('--unfurl-ms: 546ms');
      act(() => { vi.advanceTimersByTime(500); });
      expect(screen.getByTestId('plate-unfurl')).toBeInTheDocument();
      act(() => { vi.advanceTimersByTime(100); });
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
      vi.useRealTimers();
    });

    it('moves nothing when motion is reduced', () => {
      const topic = huge.groups.find((t) => t.isTopic && t.starIds.length > 30)!;
      const { rerender, props } = plate({ model: huge });
      rerender(<MemoryPlate {...props} focusTopic={topic.id} framedTopic={topic.id} />);
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
      rerender(<MemoryPlate {...props} focusTopic={null} framedTopic={null} />);
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
    });

    it('does not animate a plate that is not collapsed', () => {
      const mid = modelFor('mid');
      const { rerender, props } = plate({ model: mid, reducedMotion: false });
      rerender(<MemoryPlate {...props} reducedMotion={false} focusTopic={mid.topics[1].id} framedTopic={mid.topics[1].id} />);
      expect(screen.queryByTestId('plate-unfurl')).toBeNull();
    });
  });
});
