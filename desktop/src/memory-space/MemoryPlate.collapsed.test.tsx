import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel } from './chartCoordinates';
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
