import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel } from './chartCoordinates';
import { MemoryPlate, layoutLabels, type MemoryPlateProps } from './MemoryPlate';
import type { MemoryChart } from './types';

const TODAY = Date.parse('2026-11-02T14:10:00Z');
function chartFor(preset: string, worldline = 'steins_gate'): MemoryChart {
  const store = createMemoryStore({ facts: [], topics: [], preset });
  return handleMemory(new URL(`http://mock/api/memory/chart?session_id=s&worldline=${worldline}&identity_mode=okabe`), 'GET', {}, store)!.body as MemoryChart;
}
const normal = buildChartModel(chartFor('normal'), { today: TODAY, lastOpen: Date.parse('2026-10-30T15:02:00Z') });

function plate(overrides: Partial<MemoryPlateProps> = {}) {
  const props: MemoryPlateProps = {
    model: normal, soul: 'ready', width: 1440, height: 832, selection: null, focusTopic: null, framedTopic: null,
    reducedMotion: true, entering: false, onEntered: vi.fn(), onSelect: vi.fn(), onOverview: vi.fn(), ...overrides,
  };
  const view = render(<MemoryPlate {...props} />);
  return { ...view, props };
}
const translateOf = (el: Element) => (el.getAttribute('transform') ?? '').match(/translate\(([-\d.]+) ([-\d.]+)\)/)!.slice(1).map(Number);

afterEach(cleanup);

describe('MemoryPlate', () => {
  it('draws every record, one constellation line per topic, SOUL, legend and caption', () => {
    const { container } = plate();
    expect(container.querySelectorAll('.p-star')).toHaveLength(normal.stars.length);
    expect(container.querySelectorAll('.p-line')).toHaveLength(normal.topics.length);
    expect(container.querySelectorAll('.p-star.is-fragment .p-tail').length).toBe(3);
    expect(container.querySelectorAll('.p-ring').length).toBeGreaterThanOrEqual(2);
    expect(container.querySelectorAll('.p-new').length).toBe(normal.stars.filter((s) => s.isNew).length);
    expect(screen.getByTestId('plate-legend')).toHaveTextContent('FIG. 1 — 自 2026.08.20 起 · 每圈一个月 · 越往外越近');
    expect(container.querySelector('.p-todaylab')).toHaveTextContent('今天 11/02');
  });

  it('makes the selected record visually larger than SOUL', () => {
    const star = normal.stars[4];
    const { container } = plate({ selection: { kind: 'star', id: star.id } });
    const reticle = Math.max(...[...screen.getByTestId('plate-reticle').querySelectorAll('circle')].map((c) => Number(c.getAttribute('r'))));
    const soul = Number(container.querySelector('.p-soul .shell')!.getAttribute('r'));
    expect(reticle).toBeGreaterThan(soul);
  });

  it('keeps SOUL in the viewport and the same size while a topic is in focus', () => {
    const topic = normal.topics[3];
    const { container, rerender, props } = plate();
    const before = Number(container.querySelector('.p-soul .shell')!.getAttribute('r'));
    rerender(<MemoryPlate {...props} focusTopic={topic.id} framedTopic={topic.id} />);
    expect(container.querySelector('.plate')!.getAttribute('data-view-scale')).toBe('1.100');
    expect(Number(container.querySelector('.p-soul .shell')!.getAttribute('r'))).toBe(before);
    const [x, y] = translateOf(screen.getByTestId('plate-soul'));
    expect(x).toBeGreaterThan(0);
    expect(x).toBeLessThan(1440);
    expect(y).toBeGreaterThan(0);
    expect(y).toBeLessThan(832);
    expect(container.querySelector('.p-root')).toHaveClass('dim');
    const lit = [...container.querySelectorAll('.p-star.lit')].map((el) => el.getAttribute('data-id'));
    expect(lit).toEqual(topic.starIds);
  });

  it('routes clicks: star, topic, month, SOUL; double-click on blank paper returns to overview', () => {
    const { container, props } = plate();
    fireEvent.click(container.querySelector(`[data-id="${normal.stars[0].id}"]`)!);
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'star', id: normal.stars[0].id });
    fireEvent.click(screen.getByRole('button', { name: /^家里的猫，/ }));
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'topic', id: 't4' });
    fireEvent.click(screen.getByRole('button', { name: '2026 年 10 月' }));
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'month', key: '2026-10' });
    fireEvent.keyDown(screen.getByRole('button', { name: 'SOUL' }), { key: 'Enter' });
    expect(props.onSelect).toHaveBeenLastCalledWith({ kind: 'soul' });
    fireEvent.doubleClick(container.querySelector(`[data-id="${normal.stars[0].id}"]`)!);
    expect(props.onOverview).not.toHaveBeenCalled();
    fireEvent.doubleClick(container.querySelector('.plate')!);
    expect(props.onOverview).toHaveBeenCalledTimes(1);
  });

  it('shows a hover note with number, date and mentions', () => {
    const { container } = plate();
    const cat = normal.byId.get('f12')!;
    fireEvent.pointerEnter(container.querySelector('[data-id="f12"]')!);
    expect(screen.getByTestId('plate-hover')).toHaveTextContent(`No.${String(cat.no).padStart(3, '0')} · 09/15 · 你提过 4 次`);
  });

  it('labels each star of a young chart and keeps SOUL inert while reading', () => {
    const young = buildChartModel(chartFor('new'), { today: TODAY, lastOpen: null });
    const { container, unmount } = plate({ model: young });
    expect(container.querySelectorAll('.p-young')).toHaveLength(3);
    expect(container).toHaveTextContent('你养了一只叫小黑的猫。');
    expect(screen.queryByTestId('plate-legend')).toBeNull();
    unmount();
    plate({ model: null, soul: 'loading' });
    expect(screen.queryByRole('button', { name: 'SOUL' })).toBeNull();
  });

  it('plays the entrance once and reports when it settles; reduced motion skips it', () => {
    vi.useFakeTimers();
    const { container, props } = plate({ reducedMotion: false, entering: true });
    expect(container.querySelector('.plate')).toHaveClass('is-entering');
    act(() => { vi.advanceTimersByTime(8000); });
    expect(container.querySelector('.plate')).not.toHaveClass('is-entering');
    expect(props.onEntered).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
    const still = plate({ entering: true });
    expect(still.container.querySelectorAll('.plate.is-entering')).toHaveLength(0);
    expect(still.props.onEntered).toHaveBeenCalledTimes(1);
  });

  it('keeps topic names and month labels of a 300-record library apart', () => {
    const large = buildChartModel(chartFor('large'), { today: TODAY, lastOpen: null });
    const { topics, months, today } = layoutLabels(large, 1440, 832);
    const boxes = [today.box, ...months.map((m) => m.box), ...topics.map((t) => t.box)];
    expect(topics).toHaveLength(12);
    for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) {
      const [a, b] = [boxes[i], boxes[j]];
      expect(a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h, `${i}/${j}`).toBe(false);
    }
  });
});
