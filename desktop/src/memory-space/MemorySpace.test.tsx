import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createRef, type RefObject } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import { ymd } from './chartFormat';
import { MemorySpace, type MemorySpaceControl } from './MemorySpace';
import type { MemoryChart } from './types';

vi.mock('../design-system/useReducedMotion', () => ({ useReducedMotion: () => true }));

type Override = (url: URL, method: string) => Promise<Response> | Response | null;
let calls: string[] = [];

function serve(preset: string | undefined, override?: Override) {
  const store = createMemoryStore({ facts: [], topics: [], preset });
  calls = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://mock');
    const method = init?.method ?? 'GET';
    calls.push(`${method} ${url.pathname}${url.search}`);
    const hit = override?.(url, method);
    if (hit) return hit;
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : {};
    const result = handleMemory(url, method, body, store);
    if (!result) throw new Error(`unexpected fetch ${url.pathname}`);
    return new Response(JSON.stringify(result.body), { status: result.status, headers: { 'Content-Type': 'application/json' } });
  }));
  return store;
}

function mount(onClose = vi.fn()) {
  const view = render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} />);
  return { ...view, onClose };
}
const plateReady = () => waitFor(() => expect(document.querySelectorAll('.p-star').length).toBeGreaterThan(0));
const titleValue = (key: string) => {
  const cells = [...screen.getByTestId('memory-titleblock').children];
  return cells[cells.findIndex((cell) => cell.textContent === key) + 1].textContent;
};

beforeEach(() => localStorage.clear());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('MemorySpace sheet', () => {
  it('draws the chart sheet under the header: tabs, title block with real readings, no own header or find layer', async () => {
    serve('normal');
    mount();
    await plateReady();
    expect(screen.getByTestId('memory-tab-chart')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('memory-tab-archive')).toHaveTextContent('档案 INDEX');
    expect(screen.getByTestId('memory-back')).toHaveTextContent('回到对话');
    expect(titleValue('PLATE')).toBe('MEMORY · SG');
    expect(titleValue('SCOPE')).toBe('OKABE · SG');
    expect(titleValue('SINCE')).toBe(ymd(Date.parse('2026-08-20T12:00:00+08:00')));
    expect(titleValue('STARS')).toBe('21 + 4');
    expect(document.querySelectorAll('.p-star')).toHaveLength(25);
    expect(screen.queryByRole('button', { name: '查找' })).toBeNull();
    expect(screen.queryByTestId('memory-space-veil')).toBeNull();
    expect(screen.queryByText(/3D 当前不可用|重试 3D/)).toBeNull();
    expect(calls.some((call) => call.startsWith('GET /api/memory/chart?'))).toBe(true);
    expect(calls.some((call) => call.includes('/graph/projection'))).toBe(false);
  });

  it('opens a record, then Esc steps back record → topic → overview → conversation', async () => {
    serve('normal');
    const { onClose } = mount();
    await plateReady();
    fireEvent.click(document.querySelector('[data-id="f12"]')!);
    expect(await screen.findByTestId('reading-her')).toHaveTextContent('你养了一只叫小黑的猫。');
    expect(screen.getByTestId('memory-crumbs')).toHaveTextContent(/^总览\/家里的猫\/No\.\d{3}$/);
    expect(screen.getByTestId('memory-back')).toHaveTextContent('回退');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('reading-dom')).toHaveTextContent('在档案中查看这个主题 ↗');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    expect(screen.queryByTestId('memory-crumbs')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('right-click steps back one layer and never leaves the sheet', async () => {
    serve('normal');
    const { onClose } = mount();
    await plateReady();
    fireEvent.click(screen.getByRole('button', { name: 'SOUL' }));
    expect(screen.getByTestId('soul-facts')).toBeInTheDocument();
    fireEvent.contextMenu(screen.getByTestId('memory-graph'));
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    fireEvent.contextMenu(screen.getByTestId('memory-graph'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a month label opens that month; the back button reads 回退 until the overview', async () => {
    serve('normal');
    mount();
    await plateReady();
    fireEvent.click(screen.getByRole('button', { name: '2026 年 10 月' }));
    expect(screen.getByTestId('reading-month-summary')).toBeInTheDocument();
    expect(screen.getByTestId('memory-crumbs')).toHaveTextContent('总览/2026.10');
    fireEvent.click(screen.getByTestId('memory-back'));
    expect(screen.getByTestId('memory-back')).toHaveTextContent('回到对话');
  });

  it('"/" jumps to the archive search', async () => {
    serve('normal');
    mount();
    await plateReady();
    fireEvent.keyDown(window, { key: '/' });
    expect(await screen.findByTestId('archive-view')).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('archive-query')));
    expect(screen.queryByTestId('memory-titleblock')).toBeNull();
  });

  it('opens a topic in the archive filtered to it, and a record in the archive focused on it', async () => {
    serve('normal');
    mount();
    await plateReady();
    fireEvent.click(screen.getByRole('button', { name: /^家里的猫，/ }));
    fireEvent.click(screen.getByTestId('open-topic-in-archive'));
    expect(await screen.findByTestId('archive-view')).toBeInTheDocument();
    await waitFor(() => expect(calls.some((call) => call.startsWith('GET /api/memory/facts?') && call.includes('topic_id=t4'))).toBe(true));
    fireEvent.click(screen.getByTestId('memory-tab-chart'));
    await plateReady();
    fireEvent.click(document.querySelector('[data-id="f13"]')!);
    fireEvent.click(await screen.findByTestId('open-in-archive'));
    await waitFor(() => expect(within(screen.getByTestId('archive-view')).getAllByText('小黑三岁了。').length).toBeGreaterThan(0));
  });

  it('opens with focus on the sheet; Tab reaches the stars once, arrows walk them, Enter reads, Esc returns focus to the star', async () => {
    serve('normal');
    mount();
    await plateReady();
    expect(document.activeElement).toBe(screen.getByTestId('memory-space'));
    const tabStops = [...screen.getByTestId('memory-plate').querySelectorAll<SVGElement>('[tabindex="0"]')];
    const starStops = tabStops.filter((el) => el.getAttribute('data-hit') === 'star');
    expect(starStops).toHaveLength(1);
    act(() => starStops[0].focus());
    fireEvent.keyDown(starStops[0], { key: 'ArrowRight' });
    const star = document.activeElement as SVGElement;
    expect(star.getAttribute('data-hit')).toBe('star');
    expect(star).not.toBe(starStops[0]);
    fireEvent.keyDown(star, { key: 'Enter' });
    expect(await screen.findByTestId('reading-dom')).toBeInTheDocument();
    expect(screen.getByTestId('plate-reticle')).toBeInTheDocument();
    fireEvent.keyDown(star, { key: 'Escape' });
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('reading-dom')).toBeNull());
    expect(document.activeElement).toBe(star);
  });

  it('returns focus to the star when its reading closes from inside the reading', async () => {
    serve('normal');
    mount();
    await plateReady();
    const star = document.querySelector<SVGElement>('[data-id="e1"]')!;
    act(() => star.focus());
    fireEvent.keyDown(star, { key: 'Enter' });
    const close = await screen.findByRole('button', { name: '关闭阅读' });
    act(() => close.focus());
    fireEvent.click(close);
    await waitFor(() => expect(screen.queryByTestId('reading-dom')).toBeNull());
    expect(document.activeElement).toBe(star);
  });
  it('browses another scope from the SCOPE row without touching the conversation', async () => {
    serve('normal');
    mount();
    await plateReady();
    fireEvent.click(screen.getByTestId('memory-scope'));
    const menu = screen.getByTestId('memory-scope-menu');
    expect(menu).toHaveTextContent('只是浏览，对话不变');
    expect(within(menu).getAllByRole('option')).toHaveLength(4);
    fireEvent.click(within(menu).getByRole('option', { name: 'OKABE · β' }));
    await waitFor(() => expect(titleValue('PLATE')).toBe('MEMORY · β'));
    expect(screen.getByTestId('memory-space')).toHaveAttribute('data-worldline', 'beta');
    await waitFor(() => expect(calls.some((call) => call.startsWith('GET /api/memory/chart?') && call.includes('worldline=beta'))).toBe(true));
  });

  it('marks what was recorded since the last visit and remembers this visit', async () => {
    localStorage.setItem('amadeus_memory_last_open', JSON.stringify({ 's|steins_gate|okabe': Date.parse('2026-10-30T15:02:00Z') }));
    serve('normal');
    mount();
    await plateReady();
    expect(document.querySelectorAll('.p-new').length).toBeGreaterThan(0);
    const stored = JSON.parse(localStorage.getItem('amadeus_memory_last_open')!) as Record<string, number>;
    expect(stored['s|steins_gate|okabe']).toBeGreaterThan(Date.parse('2026-10-30T15:02:00Z'));
  });
});

describe('MemorySpace states', () => {
  it('reading: paper, frame and title block, SOUL outline only, one honest line', async () => {
    serve('normal', (url) => (url.pathname === '/api/memory/chart' ? new Promise<Response>(() => undefined) : null));
    mount();
    expect(await screen.findByTestId('memory-loading')).toHaveTextContent('正在读取记忆…');
    expect(screen.getByTestId('plate-soul')).toHaveAttribute('data-state', 'loading');
    expect(screen.queryByRole('button', { name: 'SOUL' })).toBeNull();
    expect(titleValue('SINCE')).toBe('--');
    expect(titleValue('STARS')).toBe('--');
    expect(document.querySelectorAll('.p-star, .p-ring')).toHaveLength(0);
  });

  it('unavailable (503): the locked card with retry and back to the conversation', async () => {
    serve('normal', (url) => (url.pathname === '/api/memory/chart'
      ? new Response(JSON.stringify({ detail: { code: 'memory_store_unavailable' } }), { status: 503 }) : null));
    const { onClose } = mount();
    const card = await screen.findByTestId('memory-unavailable');
    expect(card).toHaveTextContent('记忆存储暂时不可用');
    expect(card).not.toHaveTextContent('memory_store_unavailable');
    fireEvent.click(within(card).getByRole('button', { name: '返回对话' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('error: says nothing changed and retries into the chart', async () => {
    let fail = true;
    serve('normal', (url) => (url.pathname === '/api/memory/chart' && fail
      ? new Response(JSON.stringify({ detail: { code: 'memory_operation_failed' } }), { status: 500 }) : null));
    mount();
    const card = await screen.findByTestId('memory-error');
    expect(card).toHaveTextContent('无法读取记忆。记忆本身没有改动。');
    fail = false;
    fireEvent.click(within(card).getByRole('button', { name: '重试' }));
    await plateReady();
    expect(screen.queryByTestId('memory-error')).toBeNull();
  });

  it('empty and new-user charts say so plainly', async () => {
    serve(undefined);
    mount();
    expect(await screen.findByTestId('memory-empty')).toHaveTextContent('这里还没有长期记忆；可以返回对话，或稍后再来查看。');
    expect(titleValue('STARS')).toBe('0 + 0');
    cleanup();
    serve('new');
    mount();
    expect(await screen.findByTestId('memory-new')).toHaveTextContent('已经记下 3 件事。记得多了以后，会连成主题。');
    expect(document.querySelectorAll('.p-young')).toHaveLength(3);
  });

  it('a truncated chart footnotes where the full list is', async () => {
    serve('normal', (url, method) => {
      if (url.pathname !== '/api/memory/chart' || method !== 'GET') return null;
      const body = handleMemory(url, 'GET', {}, createMemoryStore({ facts: [], topics: [], preset: 'normal' }))!.body as MemoryChart;
      return new Response(JSON.stringify({ ...body, facts: body.facts.slice(0, 15), truncated: true }), { status: 200 });
    });
    mount();
    expect(await screen.findByTestId('plate-footnote')).toHaveTextContent('星图只画了其中 19 / 25 条；完整的在档案里。');
  });
});

describe('MemorySpace with the archive', () => {
  it('leaving the archive with an unsaved correction asks first', async () => {
    serve('normal');
    mount();
    await plateReady();
    fireEvent.click(screen.getByTestId('memory-tab-archive'));
    fireEvent.click((await screen.findAllByText('小黑三岁了。'))[0]);
    fireEvent.click(await screen.findByTestId('archive-edit-fact'));
    fireEvent.change(await screen.findByTestId('archive-editor-input'), { target: { value: '未保存的草稿文本' } });
    fireEvent.click(screen.getByTestId('memory-tab-chart'));
    expect(await screen.findByTestId('archive-discard-confirm')).toBeInTheDocument();
    expect(screen.getByTestId('archive-view')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('archive-discard-confirm-button'));
    await waitFor(() => expect(screen.queryByTestId('archive-view')).toBeNull());
    expect(screen.getByTestId('memory-tab-chart')).toHaveAttribute('aria-selected', 'true');
  });

  it('the header key leaves through the same draft guard: asks, keeps the draft on cancel, closes only after discard', async () => {
    serve('normal');
    const control = createRef<MemorySpaceControl>() as RefObject<MemorySpaceControl | null>;
    const onClose = vi.fn();
    render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} controlRef={control} />);
    await plateReady();
    fireEvent.click(screen.getByTestId('memory-tab-archive'));
    fireEvent.click((await screen.findAllByText('小黑三岁了。'))[0]);
    fireEvent.click(await screen.findByTestId('archive-edit-fact'));
    fireEvent.change(await screen.findByTestId('archive-editor-input'), { target: { value: '未保存的草稿文本' } });
    act(() => control.current!.requestClose());
    expect(await screen.findByTestId('archive-discard-confirm')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('archive-discard-continue'));
    expect(screen.queryByTestId('archive-discard-confirm')).toBeNull();
    expect(screen.getByTestId('archive-editor-input')).toHaveValue('未保存的草稿文本');
    expect(onClose).not.toHaveBeenCalled();
    act(() => control.current!.requestClose());
    fireEvent.click(await screen.findByTestId('archive-discard-confirm-button'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('the header key closes at once when nothing is unsaved', async () => {
    serve('normal');
    const control = createRef<MemorySpaceControl>() as RefObject<MemorySpaceControl | null>;
    const onClose = vi.fn();
    render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={onClose} controlRef={control} />);
    await plateReady();
    act(() => control.current!.requestClose());
    expect(onClose).toHaveBeenCalledTimes(1);
  });
  it('a record deleted in the archive is gone from the chart and its reading closes', async () => {
    serve('normal');
    mount();
    await plateReady();
    fireEvent.click(document.querySelector('[data-id="f13"]')!);
    fireEvent.click(await screen.findByTestId('open-in-archive'));
    fireEvent.click(await screen.findByTestId('archive-delete-record'));
    fireEvent.click(screen.getByTestId('archive-delete-confirm-button'));
    await waitFor(() => expect(screen.queryByTestId('archive-delete-confirm')).toBeNull());
    fireEvent.click(screen.getByTestId('memory-tab-chart'));
    await plateReady();
    await waitFor(() => expect(document.querySelector('[data-id="f13"]')).toBeNull());
    expect(document.querySelectorAll('.p-star')).toHaveLength(24);
    expect(screen.queryByTestId('reading-dom')).toBeNull();
    await act(async () => { await Promise.resolve(); });
  });
});
