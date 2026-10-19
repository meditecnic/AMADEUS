import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WorldlineShift, SHIFT_TIMING } from './WorldlineShift';
import { ShiftTransaction } from './shiftTransaction';
import { acquireShiftStage, prewarmShiftStage, resetShiftStageForTests, shiftStageReady } from './shiftStage';
import { StrictMode } from 'react';

type MockRenderer = {
  canvas: HTMLCanvasElement;
  onLost: (() => void) | null;
  isLost: boolean;
  render: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>;
  trim: ReturnType<typeof vi.fn>;
};
const graphics = vi.hoisted(() => ({
  available: false,
  /** When set, Renderer.create waits for it (a cold stage still compiling). */
  gate: null as Promise<void> | null,
  instances: [] as MockRenderer[],
}));
vi.mock('../worldline-engine', async importOriginal => ({
  ...await importOriginal<typeof import('../worldline-engine')>(),
  Renderer: class {
    onLost: (() => void) | null = null;
    onRestored = null;
    isLost = false;
    dispose = vi.fn();
    render = vi.fn();
    resize = vi.fn();
    trim = vi.fn();
    flush = vi.fn();
    static async create(canvas: HTMLCanvasElement) {
      if (graphics.gate) await graphics.gate;
      return new this(canvas);
    }
    constructor(readonly canvas: HTMLCanvasElement) {
      if (!graphics.available) throw new Error('WebGL2 不可用');
      graphics.instances.push(this as unknown as MockRenderer);
    }
  },
}));

/** The browser drops the context: the renderer reports lost, then its owner is told. */
const lose = (r: MockRenderer) => act(() => { r.isLost = true; r.onLost?.(); });
/** rAF boot plus any async stage work. */
const settle = (ms = 20) => act(() => vi.advanceTimersByTimeAsync(ms));

describe('worldline shift presentation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetShiftStageForTests();
    graphics.available = false;
    graphics.gate = null;
    graphics.instances = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); resetShiftStageForTests(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  async function warmStage() {
    graphics.available = true;
    await act(async () => { await prewarmShiftStage({ urgent: true }); });
    expect(shiftStageReady()).toBe(true);
  }

  async function setup(id = 'request') {
    const commit = vi.fn();
    const send = vi.fn(() => true);
    const done = vi.fn();
    const covered = vi.fn();
    const transaction = new ShiftTransaction(id, 'steins_gate', 'beta', commit);
    transaction.start(send);
    const view = render(<WorldlineShift transaction={transaction} onDone={done} onCovered={covered} />);
    await settle();
    return { transaction, commit, send, done, covered, ...view };
  }

  it('mixed repeated click/Esc skips wait for the same request then statically lock once', async () => {
    const { transaction, commit, send, done, container } = await setup();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: /跳过/ }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByText('SYNC · 等待目标世界线确认')).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(container.querySelector('[data-shift-reading]')?.textContent).toBe('1.129848');
    expect(commit).toHaveBeenCalledTimes(1);
    await settle(200 + SHIFT_TIMING.skipExit - 40);
    expect(done).not.toHaveBeenCalled();
    await settle(60);
    expect(done).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('without WebGL2 the overlay presents in CSS at once and tunes after the real lock', async () => {
    const { transaction, commit, done, container } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    expect(el.dataset.renderer).toBe('css');
    expect(el.dataset.fallback).toBe('unavailable');
    expect(el.querySelector('canvas')).toBeNull();
    // The CSS instrument holds the source reading until the controller has locked the target.
    expect(screen.getByRole('img', { name: 'World line divergence 1.048596%' })).toBeTruthy();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
    expect(screen.getByRole('img', { name: 'World line divergence 1.129848%' })).toBeTruthy();
    // Not a user skip: the tubes get their tuning time before the dissolve.
    await settle(SHIFT_TIMING.cssHold + SHIFT_TIMING.cssExit - 50);
    expect(done).not.toHaveBeenCalled();
    await settle(80);
    expect(done).toHaveBeenCalledOnce();
  });

  it('failure and failed skip retain source with zero success publications', async () => {
    const { transaction, commit, container, done } = await setup();
    await act(async () => { transaction.accept('request', 'beta', 'fail'); });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(container.querySelector('[data-shift-reading]')?.textContent).toBe('1.048596');
    expect(screen.getByText(/跃迁失败/)).toBeTruthy();
    await settle(SHIFT_TIMING.failHold + SHIFT_TIMING.failExit + 40);
    expect(done).toHaveBeenCalledOnce();
    expect(commit).not.toHaveBeenCalled();
  });

  it('unmount prevents late outcome/frame/timer callbacks while host retains outcome', async () => {
    const { transaction, commit, done, unmount } = await setup();
    unmount();
    await act(async () => { transaction.accept('request', 'beta', 'success'); vi.advanceTimersByTime(3000); });
    expect(transaction.result).toBe('success');
    expect(commit).not.toHaveBeenCalled();
    expect(done).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a warm stage starts the GPU show on the first frame, no compile in the click path', async () => {
    await warmStage();
    const { container, covered } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    expect(graphics.instances).toHaveLength(1);
    expect(el.dataset.renderer).toBe('webgl2');
    expect(el.dataset.started).toBe('');
    expect(el.querySelector('canvas.shift-canvas')).toBe(graphics.instances[0].canvas);
    expect(graphics.instances[0].render).toHaveBeenCalled();
    expect(covered).not.toHaveBeenCalled();
    await settle(SHIFT_TIMING.cover);
    expect(covered).toHaveBeenCalledOnce();
  });

  it('a natural GPU end dissolves only the overlay; the shared canvas is never animated', async () => {
    await warmStage();
    const calls: { target: Element; frames: Keyframe[] }[] = [];
    Element.prototype.animate = vi.fn(function (this: Element, frames: Keyframe[]) {
      calls.push({ target: this, frames });
      return { cancel: vi.fn(), finish: vi.fn(), onfinish: null } as unknown as Animation;
    }) as unknown as Element['animate'];
    const { transaction, done, container } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    const canvas = graphics.instances[0].canvas;
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    await settle(14000);
    expect(done).toHaveBeenCalledOnce();
    expect(calls.some(c => c.target === el && c.frames.some(f => f.opacity === 0))).toBe(true);
    expect(calls.filter(c => c.target === canvas)).toHaveLength(0);
    delete (Element.prototype as { animate?: unknown }).animate;
  });

  it('whatever a show leaves on the shared canvas is cleared before the next one', async () => {
    await warmStage();
    const lease = acquireShiftStage()!;
    document.body.append(lease.canvas);
    const leftover = { cancel: vi.fn() };
    Object.assign(lease.canvas, { getAnimations: () => [leftover] });
    lease.canvas.style.filter = 'brightness(.42)';
    lease.release();
    expect(leftover.cancel).toHaveBeenCalledOnce();
    expect(lease.canvas.getAttribute('style')).toBeNull();
    expect(lease.canvas.isConnected).toBe(false);
    expect(acquireShiftStage()?.canvas).toBe(lease.canvas);
  });

  it('commits at final frame lock before engine end, then ignores context loss after publication', async () => {
    await warmStage();
    const { transaction, commit, send, container, done } = await setup();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    await settle(7500);
    expect(commit).not.toHaveBeenCalled();
    await settle(200);
    expect(commit).toHaveBeenCalledOnce();
    expect(done).not.toHaveBeenCalled();
    expect(container.querySelector('[data-locked="true"]')).toBeTruthy();
    await lose(graphics.instances[0]);
    expect(container.querySelector('[data-renderer="css"][data-fallback="context-lost"]')).toBeTruthy();
    await settle(SHIFT_TIMING.cssHold + SHIFT_TIMING.cssExit + 40);
    expect(done).toHaveBeenCalledOnce();
    expect(commit).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
    expect(graphics.instances[0].dispose).toHaveBeenCalledOnce();
    expect(shiftStageReady()).toBe(false);
  });

  it('natural GPU end holds the locked frame, dissolves, and keeps the stage warm for the next shift', async () => {
    await warmStage();
    const { transaction, commit, container, done, unmount } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    const animate = vi.fn();
    el.animate = animate;
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    await settle(10600);
    expect(commit).toHaveBeenCalledOnce();
    expect(el.dataset.state).toBe('settled');
    expect(el.dataset.exit).toBeUndefined();
    await settle(SHIFT_TIMING.hold + 20);
    expect(el.dataset.exit).toBe('');
    expect(animate).toHaveBeenCalledWith([{ opacity: 1 }, { opacity: 0 }], expect.objectContaining({ duration: SHIFT_TIMING.exit }));
    expect(done).not.toHaveBeenCalled();
    await settle(SHIFT_TIMING.exit);
    expect(done).toHaveBeenCalledOnce();
    unmount();
    expect(graphics.instances[0].dispose).not.toHaveBeenCalled();
    expect(graphics.instances[0].trim).toHaveBeenCalled();
    expect(graphics.instances[0].canvas.isConnected).toBe(false);
    expect(shiftStageReady()).toBe(true);
    const again = await setup('second');
    expect(graphics.instances).toHaveLength(1);
    expect(again.container.querySelector('canvas')).toBe(graphics.instances[0].canvas);
  });

  it('skipping the GPU show dips to dark before the cut instead of jumping', async () => {
    await warmStage();
    const { transaction, commit, container, done } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    await settle(1500);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(el.dataset.dip).toBe('in');
    expect(commit).not.toHaveBeenCalled();
    await settle(SHIFT_TIMING.dipIn + 10);
    expect(commit).toHaveBeenCalledOnce();
    expect(el.dataset.locked).toBe('true');
    // Already dark: straight dissolve, no lift back to the picture.
    expect(el.dataset.dip).toBe('in');
    await settle(SHIFT_TIMING.skipExit + 20);
    expect(done).toHaveBeenCalledOnce();
    expect(commit).toHaveBeenCalledOnce();
  });

  it('context loss while pending continues at the gate and locks on the same ACK', async () => {
    await warmStage();
    const { transaction, commit, send, container, unmount } = await setup();
    await lose(graphics.instances[0]);
    expect(container.querySelector('[data-renderer="css"][data-state="waiting"]')).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
    unmount();
    expect(graphics.instances[0].dispose).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a cold stage keeps the overlay up while it compiles, then the same run starts on the GPU', async () => {
    graphics.available = true;
    let open!: () => void;
    graphics.gate = new Promise<void>(resolve => { open = resolve; });
    const { transaction, commit, container } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    expect(el.dataset.boot).toBe('warming');
    expect(el.dataset.renderer).toBe('pending');
    expect(el.dataset.started).toBeUndefined();
    await act(async () => { open(); });
    await settle();
    expect(el.dataset.renderer).toBe('webgl2');
    expect(el.dataset.started).toBe('');
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    await settle(7800);
    expect(commit).toHaveBeenCalledOnce();
  });

  it('a cold stage that misses its bound presents in CSS; the late stage is kept for next time', async () => {
    graphics.available = true;
    let open!: () => void;
    graphics.gate = new Promise<void>(resolve => { open = resolve; });
    const { transaction, commit, container } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    await settle(SHIFT_TIMING.coldWait);
    expect(el.dataset.renderer).toBe('css');
    expect(el.dataset.fallback).toBe('warming');
    await act(async () => { open(); });
    await settle();
    expect(el.querySelector('canvas')).toBeNull();
    expect(shiftStageReady()).toBe(true);
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
  });

  it('discards the first StrictMode setup before taking the stage and keeps the canvas warm on unmount', async () => {
    await warmStage();
    const commit = vi.fn();
    const send = vi.fn(() => true);
    const transaction = new ShiftTransaction('strict', 'steins_gate', 'beta', commit);
    transaction.start(send);
    const { unmount, container } = render(<StrictMode><WorldlineShift transaction={transaction} onDone={vi.fn()} /></StrictMode>);
    await settle();
    expect(graphics.instances).toHaveLength(1);
    expect(container.querySelectorAll('canvas')).toHaveLength(1);
    await act(async () => { transaction.accept('strict', 'beta', 'success'); });
    fireEvent.keyDown(window, { key: 'Escape' });
    await settle(SHIFT_TIMING.dipIn + 10);
    expect(commit).toHaveBeenCalledOnce();
    unmount();
    expect(graphics.instances[0].canvas.isConnected).toBe(false);
    expect(graphics.instances[0].dispose).not.toHaveBeenCalled();
    expect(acquireShiftStage()).not.toBeNull();
    expect(send).toHaveBeenCalledOnce();
  });

  it('sustained low fps hands the same pending controller to CSS without resending or recommitting', async () => {
    await warmStage();
    const { transaction, commit, send, container } = await setup();
    let clock = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => clock);
    graphics.instances[0].render.mockImplementation(() => { clock += 100; });
    await settle(1000);
    expect(container.querySelector('[data-fallback="low-fps"][data-state="waiting"]')).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(commit).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
    expect(graphics.instances[0].dispose).toHaveBeenCalledOnce();
  });

  it('reduced motion never takes the GPU stage and ends on a static beat', async () => {
    await warmStage();
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('reduce'), media: query, onchange: null,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList);
    const { transaction, commit, container, done, covered } = await setup();
    const el = container.querySelector('.worldline-shift') as HTMLElement;
    expect(el.dataset.renderer).toBe('css');
    expect(el.dataset.fallback).toBe('reduced-motion');
    expect(el.querySelector('canvas')).toBeNull();
    expect(covered).toHaveBeenCalledOnce();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
    await settle(SHIFT_TIMING.reducedHold + 10);
    expect(done).toHaveBeenCalledOnce();
    expect(shiftStageReady()).toBe(true);
  });
});
