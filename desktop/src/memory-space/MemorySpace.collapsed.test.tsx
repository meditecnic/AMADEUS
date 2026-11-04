import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { buildChartModel, plateGeometry, platePoint, type ChartModel } from './chartCoordinates';
import { MemorySpace } from './MemorySpace';
import type { MemoryChart } from './types';

vi.mock('../design-system/useReducedMotion', () => ({ useReducedMotion: () => true }));

function serve(preset: string) {
  const store = createMemoryStore({ facts: [], topics: [], preset });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://mock');
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {};
    const result = handleMemory(url, init?.method ?? 'GET', body, store);
    if (!result) throw new Error(`unexpected fetch ${url.pathname}`);
    return new Response(JSON.stringify(result.body), { status: result.status, headers: { 'Content-Type': 'application/json' } });
  }));
  const chart = handleMemory(new URL('http://mock/api/memory/chart?session_id=s&worldline=steins_gate&identity_mode=okabe'), 'GET', {}, store)!.body as MemoryChart;
  return { chart, model: buildChartModel(chart, { today: Date.now(), lastOpen: null }) };
}
const mount = () => render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />);
const ready = () => waitFor(() => expect(document.querySelectorAll('.p-ribbon, .p-star').length).toBeGreaterThan(0));

// jsdom has no layout: the sheet falls back to the window size and the svg sits at 0,0.
function pointOnCell(model: ChartModel, cellKey: string) {
  const cell = model.cells.get(cellKey)!;
  const group = model.groups.find((t) => t.id === cell.groupId)!;
  const g = plateGeometry(window.innerWidth, Math.max(320, window.innerHeight - 60));
  const p = platePoint((cell.band.theta0 + cell.band.theta1) / 2, group.phi, g.radius, model.thetaFit);
  return { clientX: g.cx + p.x, clientY: g.cy + p.y };
}
const busiest = (model: ChartModel) => [...model.cells.values()]
  .filter((c) => model.groups.find((t) => t.id === c.groupId)!.isTopic && c.collapsedIds.length > 12)
  .sort((a, b) => b.collapsedIds.length - a.collapsedIds.length)[0];

// The sample libraries end on 2026-11-03: pin the clock so rings and months do not drift with the date.
beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-04T04:00:00Z'));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('MemorySpace with a collapsed chart', () => {
  it('opens a cell from a click on its ribbon: group open, month in the crumbs, the cell in the reading', async () => {
    const { model } = serve('huge');
    mount();
    await ready();
    expect(document.querySelectorAll('.p-star').length).toBeLessThan(150);
    const cell = busiest(model);
    const group = model.groups.find((t) => t.id === cell.groupId)!;
    fireEvent.click(document.querySelector(`.p-ribbon-hit[data-group="${cell.groupId}"]`)!, pointOnCell(model, cell.key));
    expect(await screen.findByTestId('reading-cell-title')).toHaveTextContent(`${group.label} · ${cell.band.key.replace('-', '.')}`);
    expect(screen.getByTestId('memory-crumbs')).toHaveTextContent(`总览/${group.label}/${cell.band.key.replace('-', '.')}`);
    expect(document.querySelectorAll('.p-star.lit')).toHaveLength(group.starIds.length);
    expect(document.querySelector(`.p-ribbon[data-group="${group.id}"]`)).toBeNull();
    expect(screen.getByTestId('plate-cellbox')).toBeInTheDocument();
    expect(document.querySelectorAll('.p-star.lit.out').length).toBe(group.starIds.length - cell.starIds.length);
    expect(screen.getByTestId('memory-back')).toHaveTextContent('回退');
  });

  it('Esc steps cell → topic → overview, and the ribbon comes back', async () => {
    const { model } = serve('huge');
    mount();
    await ready();
    const cell = busiest(model);
    fireEvent.click(document.querySelector(`.p-ribbon-hit[data-group="${cell.groupId}"]`)!, pointOnCell(model, cell.key));
    await screen.findByTestId('reading-cell-title');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('open-topic-in-archive')).toBeInTheDocument();
    expect(screen.queryByTestId('plate-cellbox')).toBeNull();
    expect(document.querySelectorAll('.p-star.lit.out')).toHaveLength(0);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    expect(document.querySelector(`.p-ribbon[data-group="${cell.groupId}"]`)).not.toBeNull();
    expect(document.querySelector('.p-root')).not.toHaveClass('dim');
    expect(document.querySelector('.plate')).toHaveAttribute('data-view-scale', '1.000');
  });

  it('opens a group from its name and closes the one that was open at once', async () => {
    const { model } = serve('huge');
    mount();
    await ready();
    const [a, b] = model.groups.filter((t) => t.isTopic && t.starIds.length > 50);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${a.label}，${a.starIds.length} 条`) }));
    expect(document.querySelectorAll('.p-star.lit')).toHaveLength(a.starIds.length);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${b.label}，${b.starIds.length} 条`) }));
    expect(document.querySelectorAll('.p-star.lit')).toHaveLength(b.starIds.length);
    expect(document.querySelector(`.p-ribbon[data-group="${a.id}"]`)).not.toBeNull();
    expect(document.querySelector(`.p-ribbon[data-group="${b.id}"]`)).toBeNull();
    expect(document.querySelector('.plate')).toHaveAttribute('data-view-scale', '1.060');
  });

  it('sends the archive the cell: a filter bar names it and the list holds only its records', async () => {
    const { model, chart } = serve('huge');
    mount();
    await ready();
    const cell = busiest(model);
    const group = model.groups.find((t) => t.id === cell.groupId)!;
    fireEvent.click(document.querySelector(`.p-ribbon-hit[data-group="${cell.groupId}"]`)!, pointOnCell(model, cell.key));
    fireEvent.click(await screen.findByTestId('open-cell-in-archive'));
    const bar = await screen.findByTestId('archive-within');
    expect(bar).toHaveTextContent(`只看：${group.label} · ${cell.band.key.replace('-', '.')} · ${cell.starIds.length} 条`);
    await waitFor(() => expect(screen.getByTestId('archive-tab-fact')).toHaveTextContent(`认知（${cell.starIds.filter((id) => chart.facts.some((f) => f.id === id)).length}）`));
    expect(screen.getByTestId('archive-topic-select')).toHaveValue(group.id);
    expect(within(screen.getByTestId('archive-view')).getAllByTestId('archive-row').length).toBeLessThanOrEqual(20);
    fireEvent.click(screen.getByTestId('archive-within-clear'));
    expect(screen.queryByTestId('archive-within')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('archive-tab-fact')).toHaveTextContent(`认知（${group.starIds.length}）`));
  });

  it('sends the archive a busy month from its reading', async () => {
    const { model } = serve('huge');
    mount();
    await ready();
    const labelled = [...document.querySelectorAll<SVGTextElement>('[data-hit="month"]')]
      .map((el) => ({ el, band: model.bands.find((b) => el.getAttribute('aria-label') === `${b.key.replace('-', ' 年 ')} 月`)! }))
      .find(({ band }) => band && band.starIds.length > 12);
    expect(labelled, 'a month number on the plate with more than 12 records').toBeDefined();
    fireEvent.click(labelled!.el);
    fireEvent.click(await screen.findByTestId('open-month-in-archive'));
    expect(await screen.findByTestId('archive-within')).toHaveTextContent(`只看：${labelled!.band.key.replace('-', '.')} · ${labelled!.band.starIds.length} 条`);
  });

  // Archive keeps its filters between visits; a month or cell from an earlier visit must not follow
  // the user to another topic or record.
  async function archiveOfCellThenChart() {
    const { model } = serve('huge');
    mount();
    await ready();
    const cell = busiest(model);
    fireEvent.click(document.querySelector(`.p-ribbon-hit[data-group="${cell.groupId}"]`)!, pointOnCell(model, cell.key));
    fireEvent.click(await screen.findByTestId('open-cell-in-archive'));
    await screen.findByTestId('archive-within');
    fireEvent.click(screen.getByTestId('memory-tab-chart'));
    await ready();
    return { model, cell };
  }

  it('opens another topic in the archive without the earlier cell filter', async () => {
    const { model, cell } = await archiveOfCellThenChart();
    const other = model.groups.find((t) => t.isTopic && t.id !== cell.groupId && t.starIds.length > 50)!;
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${other.label}，${other.starIds.length} 条`) }));
    fireEvent.click(await screen.findByTestId('open-topic-in-archive'));
    await waitFor(() => expect(screen.getByTestId('archive-view')).toBeInTheDocument());
    expect(screen.queryByTestId('archive-within')).toBeNull();
    expect(screen.getByTestId('archive-topic-select')).toHaveValue(other.id);
    await waitFor(() => expect(screen.getByTestId('archive-tab-fact')).toHaveTextContent(`认知（${other.starIds.length}）`));
  });

  it('opens a record of another topic in the archive, found and without the earlier cell filter', async () => {
    const { model, cell } = await archiveOfCellThenChart();
    const other = model.groups.find((t) => t.isTopic && t.id !== cell.groupId && t.starIds.length > 50)!;
    const star = model.byId.get(other.starIds[0])!;
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${other.label}，${other.starIds.length} 条`) }));
    fireEvent.click(document.querySelector(`.p-star[data-id="${star.id}"]`)!);
    fireEvent.click(await screen.findByTestId('open-in-archive'));
    await screen.findByTestId('archive-view');
    expect(screen.queryByTestId('archive-within')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('archive-tab-fact')).toHaveTextContent(`认知（${model.stars.filter((s) => s.kind !== 'fragment').length}）`));
    expect(screen.queryByTestId('archive-focus-fallback')).toBeNull();
  });
});
