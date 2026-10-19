import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WorldlineShift } from './WorldlineShift';
import { ShiftTransaction } from './shiftTransaction';
import { StrictMode } from 'react';

const graphics = vi.hoisted(() => ({ available: false, instances: [] as Array<{ canvas: HTMLCanvasElement; onLost: (() => void) | null; render: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> }> }));
vi.mock('../worldline-engine', async importOriginal => ({
  ...await importOriginal<typeof import('../worldline-engine')>(),
  Renderer: class {
    onLost: (() => void) | null = null;
    onRestored = null;
    dispose = vi.fn();
    render = vi.fn();
    resize = vi.fn();
    constructor(readonly canvas: HTMLCanvasElement) {
      if (!graphics.available) throw new Error('WebGL unavailable');
      graphics.instances.push(this);
    }
  },
}));

describe('CSS shift presentation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    graphics.available = false;
    graphics.instances = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  async function setup() {
    const commit = vi.fn();
    const send = vi.fn(() => true);
    const done = vi.fn();
    const transaction = new ShiftTransaction('request', 'steins_gate', 'beta', commit);
    transaction.start(send);
    const view = render(<WorldlineShift transaction={transaction} onDone={done} />);
    await act(async () => {});
    return { transaction, commit, send, done, ...view };
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
    await act(async () => vi.advanceTimersByTime(150));
    expect(done).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('failure and failed skip retain source with zero success publications', async () => {
    const { transaction, commit, container, done } = await setup();
    await act(async () => { transaction.accept('request', 'beta', 'fail'); });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(container.querySelector('[data-shift-reading]')?.textContent).toBe('1.048596');
    expect(screen.getByText(/跃迁失败/)).toBeTruthy();
    await act(async () => vi.advanceTimersByTime(1000));
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

  it('commits at final frame lock before engine end, then ignores context loss after publication', async () => {
    graphics.available = true;
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    const { transaction, commit, send, container, done } = await setup();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    await act(async () => vi.advanceTimersByTime(7500));
    expect(commit).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(200));
    expect(commit).toHaveBeenCalledOnce();
    expect(done).not.toHaveBeenCalled();
    expect(container.querySelector('[data-locked="true"]')).toBeTruthy();
    act(() => graphics.instances[0].onLost?.());
    await act(async () => vi.advanceTimersByTime(150));
    expect(done).toHaveBeenCalledOnce();
    expect(commit).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
    expect(graphics.instances[0].dispose).toHaveBeenCalledOnce();
  });

  it('context loss while pending continues at the gate and locks on the same ACK', async () => {
    graphics.available = true;
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    const { transaction, commit, send, container, unmount } = await setup();
    act(() => graphics.instances[0].onLost?.());
    expect(container.querySelector('[data-renderer="css"][data-state="waiting"]')).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
    unmount();
    expect(graphics.instances[0].dispose).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('discards the first StrictMode setup before GPU allocation and releases the owned canvas once', async () => {
    graphics.available = true;
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    const commit = vi.fn();
    const send = vi.fn(() => true);
    const transaction = new ShiftTransaction('strict', 'steins_gate', 'beta', commit);
    transaction.start(send);
    const { unmount, container } = render(<StrictMode><WorldlineShift transaction={transaction} onDone={vi.fn()} /></StrictMode>);
    await act(async () => {});
    expect(graphics.instances).toHaveLength(1);
    expect(container.querySelectorAll('canvas')).toHaveLength(1);
    await act(async () => { transaction.accept('strict', 'beta', 'success'); });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(commit).toHaveBeenCalledOnce();
    unmount();
    expect(graphics.instances.every(r => r.dispose.mock.calls.length === 1)).toBe(true);
    expect(send).toHaveBeenCalledOnce();
  });

  it('sustained low fps hands the same pending controller to CSS without resending or recommitting', async () => {
    graphics.available = true;
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    const { transaction, commit, send, container } = await setup();
    let clock = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => clock);
    graphics.instances[0].render.mockImplementation(() => { clock += 100; });
    await act(async () => vi.advanceTimersByTime(1000));
    expect(container.querySelector('[data-fallback="low-fps"][data-state="waiting"]')).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();
    await act(async () => { transaction.accept('request', 'beta', 'success'); });
    expect(commit).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(commit).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
    expect(graphics.instances[0].dispose).toHaveBeenCalledOnce();
  });
});
