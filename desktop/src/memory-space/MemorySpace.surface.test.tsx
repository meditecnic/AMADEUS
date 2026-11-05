import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createMemoryStore, handleMemory } from '../dev-mock/memory';
import type { ChartModel } from './chartCoordinates';
import { buildChartModel } from './chartCoordinates';
import type { ConeCallbacks, ConeHandle } from './cone/coneRenderer';
import { CHOICE_KEY, SLOW_KEY, machineKey } from './displayPrefs';
import { MemorySpace } from './MemorySpace';
import type { MemoryChart } from './types';

const h = vi.hoisted(() => ({
  created: [] as Array<{ cb: ConeCallbacks; handle: Record<string, ReturnType<typeof vi.fn>> & { pending: Array<() => void> } }>,
  failures: 0,
  probe: { webgl2: true, renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)' } as { webgl2: boolean; renderer: string | null },
  reduced: false,
}));

vi.mock('../design-system/useReducedMotion', () => ({ useReducedMotion: () => h.reduced }));
vi.mock('./webglProbe', () => ({ probeWebGL: () => h.probe, resetProbe: () => undefined }));
vi.mock('./cone/coneRenderer', () => ({
  createConeRenderer: (host: HTMLElement, cb: ConeCallbacks) => {
    if (h.failures > 0) { h.failures -= 1; return { ok: false, reason: 'nogl', error: 'no context' }; }
    const wrap = document.createElement('div');
    wrap.className = 'c3-wrap';
    host.appendChild(wrap);
    const pending: Array<() => void> = [];
    const handle = {
      element: wrap, mode: '3d', pending,
      setModel: vi.fn(), setWorldline: vi.fn(), setView: vi.fn(), resize: vi.fn(), setTier: vi.fn(), entrance: vi.fn(),
      holdPlate: vi.fn(), setSlow: vi.fn(), kick: vi.fn(), project: vi.fn(), stats: vi.fn(), frames: vi.fn(),
      toPlate: vi.fn((done: () => void) => { pending.push(done); }),
      fromPlate: vi.fn((done: () => void) => { pending.push(done); }),
      dispose: vi.fn(() => wrap.remove()),
    };
    h.created.push({ cb, handle: handle as never });
    return { ok: true, handle: handle as unknown as ConeHandle };
  },
}));

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
  return { chart, model: buildChartModel(chart, { today: Date.now(), lastOpen: null }) as ChartModel };
}
const mount = () => render(<MemorySpace sessionId="s" chatWorldline="steins_gate" chatIdentityMode="okabe" onClose={vi.fn()} />);
const lastCone = () => h.created[h.created.length - 1];
const coneReady = async () => { await waitFor(() => expect(lastCone()?.handle.setModel).toHaveBeenCalledWith(expect.objectContaining({ stars: expect.any(Array) }), expect.anything())); };
const finish = (handle: { pending: Array<() => void> }) => act(() => { while (handle.pending.length) handle.pending.shift()!(); });

beforeEach(() => {
  localStorage.clear();
  h.created.length = 0;
  h.failures = 0;
  h.reduced = false;
  h.probe = { webgl2: true, renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)' };
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-04T04:00:00Z'));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('MemorySpace: cone and plate', () => {
  it('draws the cone on a machine with a real GPU, with the switch showing 立体', async () => {
    serve('memory');
    mount();
    expect(await screen.findByTestId('memory-cone')).toBeInTheDocument();
    expect(document.querySelector('.memory-plate')).toBeNull();
    expect(screen.getByTestId('memory-display-3d')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('memory-display-plate')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('group', { name: '星图的显示方式' })).toBeInTheDocument();
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
    await coneReady();
    expect(lastCone().handle.entrance).toHaveBeenCalledTimes(1);
  });

  it('switching to the plate flattens the cone first, then fades the plate in, then lets the cone go', async () => {
    serve('memory');
    mount();
    await coneReady();
    fireEvent.click(screen.getByTestId('memory-display-plate'));
    const { handle } = lastCone();
    expect(handle.toPlate).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-surface-layer="plate"]')).toHaveClass('is-hidden');
    expect(localStorage.getItem(CHOICE_KEY)).toBe('plate');
    finish(handle);
    expect(document.querySelector('[data-surface-layer="plate"]')).not.toHaveClass('is-hidden');
    await waitFor(() => expect(screen.queryByTestId('memory-cone')).toBeNull(), { timeout: 6000 });
    expect(handle.dispose).toHaveBeenCalled();
    expect(screen.getByTestId('memory-display-plate')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
  });

  it('switching back makes the cone rise from under the plate (1.1 s) and then drops the plate', async () => {
    localStorage.setItem(CHOICE_KEY, 'plate');
    serve('memory');
    mount();
    await waitFor(() => expect(document.querySelector('.memory-plate')).not.toBeNull());
    expect(screen.queryByTestId('memory-cone')).toBeNull();
    fireEvent.click(screen.getByTestId('memory-display-3d'));
    await screen.findByTestId('memory-cone');
    const { handle } = lastCone();
    await waitFor(() => expect(handle.fromPlate).toHaveBeenCalledTimes(1));
    expect(document.querySelector('[data-surface-layer="plate"]')).toHaveClass('is-hidden');
    finish(handle);
    await waitFor(() => expect(document.querySelector('.memory-plate')).toBeNull());
    expect(localStorage.getItem(CHOICE_KEY)).toBe('3d');
  });

  it('moves nothing but the picture: selection, open group and reading stay put across both switches', async () => {
    const { model } = serve('huge');
    mount();
    await coneReady();
    expect(model.collapsed).toBe(true);
    const group = model.groups.find((t) => t.isTopic && t.starIds.length > 50)!;
    const star = model.byId.get(group.starIds[Math.floor(group.starIds.length / 2)])!;
    act(() => lastCone().cb.onSelect({ kind: 'topic', id: group.id }));
    act(() => lastCone().cb.onSelect({ kind: 'star', id: star.id }));
    const crumbs = screen.getByTestId('memory-crumbs').textContent;
    expect(crumbs).toContain(group.label);
    expect(screen.getByTestId('reading-dom')).toBeInTheDocument();
    const view = () => lastCone().handle.setView.mock.calls.at(-1)![0];
    expect(view()).toMatchObject({ selection: { kind: 'star', id: star.id }, focusTopic: group.id });

    // cone -> plate
    fireEvent.click(screen.getByTestId('memory-display-plate'));
    finish(lastCone().handle);
    await waitFor(() => expect(document.querySelector('.memory-plate')).not.toBeNull());
    expect(screen.getByTestId('memory-crumbs').textContent).toBe(crumbs);
    expect(screen.getByTestId('reading-dom')).toBeInTheDocument();
    expect(document.querySelectorAll('.p-star.lit')).toHaveLength(group.starIds.length);
    expect(screen.getByTestId('plate-reticle')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('memory-cone')).toBeNull(), { timeout: 6000 });

    // plate -> cone
    fireEvent.click(screen.getByTestId('memory-display-3d'));
    await screen.findByTestId('memory-cone');
    await waitFor(() => expect(lastCone().handle.fromPlate).toHaveBeenCalled());
    finish(lastCone().handle);
    expect(screen.getByTestId('memory-crumbs').textContent).toBe(crumbs);
    expect(screen.getByTestId('reading-dom')).toBeInTheDocument();
    expect(view()).toMatchObject({ selection: { kind: 'star', id: star.id }, focusTopic: group.id });
  });

  it('leaving for the archive in the middle of cone -> plate comes back to the plate, with no cone left over', async () => {
    serve('memory');
    mount();
    await coneReady();
    fireEvent.click(screen.getByTestId('memory-display-plate'));
    expect(lastCone().handle.toPlate).toHaveBeenCalledTimes(1);
    // the flattening is still under way: its callback is never called
    fireEvent.click(screen.getByTestId('memory-tab-archive'));
    await waitFor(() => expect(screen.queryByTestId('memory-cone')).toBeNull());
    fireEvent.click(screen.getByTestId('memory-tab-chart'));
    await waitFor(() => expect(document.querySelector('.memory-plate')).not.toBeNull());
    expect(document.querySelector('[data-surface-layer="plate"]')).not.toHaveClass('is-hidden');
    expect(screen.queryByTestId('memory-cone')).toBeNull();
    expect(screen.getByTestId('memory-display-plate')).toHaveAttribute('aria-pressed', 'true');
    expect(h.created).toHaveLength(1);
  });

  it('leaving for the archive in the middle of plate -> cone comes back to a cone that is not held flat', async () => {
    localStorage.setItem(CHOICE_KEY, 'plate');
    serve('memory');
    mount();
    await waitFor(() => expect(document.querySelector('.memory-plate')).not.toBeNull());
    fireEvent.click(screen.getByTestId('memory-display-3d'));
    await screen.findByTestId('memory-cone');
    await waitFor(() => expect(lastCone().handle.fromPlate).toHaveBeenCalledTimes(1));
    // the rise is still under way: its callback is never called
    fireEvent.click(screen.getByTestId('memory-tab-archive'));
    await waitFor(() => expect(screen.queryByTestId('memory-cone')).toBeNull());
    const before = h.created.length;
    fireEvent.click(screen.getByTestId('memory-tab-chart'));
    await screen.findByTestId('memory-cone');
    await waitFor(() => expect(h.created.length).toBe(before + 1));
    const { handle } = lastCone();
    await waitFor(() => expect(handle.setModel).toHaveBeenCalled());
    expect(handle.holdPlate).not.toHaveBeenCalled();
    expect(handle.fromPlate).not.toHaveBeenCalled();
    expect(document.querySelector('.memory-plate')).toBeNull();
    expect(screen.getByTestId('memory-display-3d')).toHaveAttribute('aria-pressed', 'true');
  });

  it('reads SOUL from the tip on the cone and from the middle on the plate', async () => {
    serve('memory');
    mount();
    await coneReady();
    act(() => lastCone().cb.onSelect({ kind: 'soul' }));
    expect(screen.getByTestId('soul-note')).toHaveTextContent('从锥尖往上，是你们第一次对话以来的每一个月。');
    fireEvent.click(screen.getByTestId('memory-display-plate'));
    finish(lastCone().handle);
    expect(screen.getByTestId('soul-note')).toHaveTextContent('从中心往外，是你们第一次对话以来的每一个月。');
  });
});

describe('MemorySpace: when the plate is used instead (DIRECTION 16.1, COPY-TABLE round 2)', () => {
  it('follows "reduce motion" with the plate and a way to the cone', async () => {
    h.reduced = true;
    serve('memory');
    mount();
    expect(await screen.findByTestId('memory-fallback')).toHaveTextContent('系统开了「减少动态」，先用图版。');
    expect(document.querySelector('.memory-plate')).not.toBeNull();
    expect(h.created).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '切到立体' }));
    await screen.findByTestId('memory-cone');
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
    expect(localStorage.getItem(CHOICE_KEY)).toBe('3d');
    // with reduced motion the cone gets no entrance, and the camera is placed at once
    await waitFor(() => expect(lastCone().handle.fromPlate).toHaveBeenCalled());
    expect(lastCone().handle.entrance).not.toHaveBeenCalled();
  });

  it('takes software rendering for the plate, and "仍然用立体" overrides it', async () => {
    h.probe = { webgl2: true, renderer: 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)' };
    serve('memory');
    mount();
    expect(await screen.findByTestId('memory-fallback')).toHaveTextContent('这台电脑现在没有显卡加速，先用图版。');
    expect(h.created).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '仍然用立体' }));
    await screen.findByTestId('memory-cone');
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
  });

  it('a pick of their own is followed without a word, even on software rendering', async () => {
    h.probe = { webgl2: true, renderer: 'llvmpipe (LLVM 15.0.7, 256 bits)' };
    localStorage.setItem(CHOICE_KEY, '3d');
    serve('memory');
    mount();
    await screen.findByTestId('memory-cone');
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
  });

  it('says it cannot open the cone, tries again on request, and says so when it still cannot', async () => {
    h.failures = 2;
    serve('memory');
    mount();
    const notice = await screen.findByTestId('memory-fallback');
    expect(notice).toHaveTextContent('这台电脑现在打不开立体星图，先用图版。');
    await waitFor(() => expect(document.querySelector('.memory-plate')).not.toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '再试一次' }));
    await waitFor(() => expect(screen.getByTestId('memory-fallback')).toHaveTextContent('还是打不开，继续用图版。'));
    expect(screen.queryByTestId('memory-fallback-action')).toBeNull();
    expect(document.querySelector('.memory-plate')).not.toBeNull();
  });

  it('rebuilds a lost context once and goes to the plate on the second loss', async () => {
    serve('memory');
    mount();
    await coneReady();
    expect(h.created).toHaveLength(1);
    act(() => h.created[0].cb.onContextLost());
    await waitFor(() => expect(h.created).toHaveLength(2));
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
    act(() => h.created[1].cb.onContextLost());
    expect(await screen.findByTestId('memory-fallback')).toHaveTextContent('这台电脑现在打不开立体星图，先用图版。');
    await waitFor(() => expect(document.querySelector('.memory-plate')).not.toBeNull());
  });

  it('moves to the plate when the frames are measured slow, remembers the machine for a week, and "切回立体" forgets it', async () => {
    serve('memory');
    mount();
    await coneReady();
    act(() => lastCone().cb.onSelect({ kind: 'soul' }));
    // The timing itself is covered in renderCapability.test.ts; here two slow windows arrive from the renderer.
    const created = lastCone();
    const props = created.cb as unknown as { onDrawn: (s: { at: number; ms: number }) => void };
    let at = performance.now() + 1600;
    for (let i = 0; i < 2 * 200; i += 1) { at += 40; props.onDrawn({ at, ms: 40 }); }
    await waitFor(() => expect(screen.getByTestId('memory-fallback')).toHaveTextContent('这台电脑跑立体星图偏慢，已换成图版。'));
    expect(created.handle.setTier).toHaveBeenCalledWith(1);
    expect(Object.keys(JSON.parse(localStorage.getItem(SLOW_KEY)!))).toHaveLength(1);
    expect(screen.getByTestId('reading-dom')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切回立体' }));
    await screen.findByTestId('memory-cone');
    expect(JSON.parse(localStorage.getItem(SLOW_KEY)!)).toEqual({});
    expect(screen.queryByTestId('memory-fallback')).toBeNull();
  });

  it('goes straight to the plate for a machine noted slow within the week, and only that machine', async () => {
    serve('memory');
    localStorage.setItem(SLOW_KEY, JSON.stringify({ 'some other machine': Date.now() + 1000 }));
    mount();
    await screen.findByTestId('memory-cone');
    cleanup();
    localStorage.setItem(SLOW_KEY, JSON.stringify({ [machineKey(h.probe.renderer)]: Date.now() + 1000 }));
    mount();
    expect(await screen.findByTestId('memory-fallback')).toHaveTextContent('这台电脑跑立体星图偏慢，已换成图版。');
    expect(h.created).toHaveLength(1);
  });
});
