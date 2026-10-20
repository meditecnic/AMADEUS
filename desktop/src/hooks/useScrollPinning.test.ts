import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useScrollPinning } from './useScrollPinning';

let frames: Map<number, FrameRequestCallback>;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(1000);
  frames = new Map();
  let frameId = 0;
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.set(++frameId, cb); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function harness() {
  const metrics = { height: 900, client: 300, top: 600 };
  const el = document.createElement('div');
  Object.defineProperties(el, {
    scrollHeight: { get: () => metrics.height },
    clientHeight: { get: () => metrics.client },
    scrollTop: { get: () => metrics.top, set: (top: number) => { metrics.top = Math.max(0, Math.min(top, metrics.height - metrics.client)); } },
  });
  el.scrollTo = vi.fn((options: ScrollToOptions) => { el.scrollTop = options.top ?? el.scrollTop; }) as typeof el.scrollTo;
  const props = { viewportRef: { current: el }, scope: 'sg:a', content: 0, messageCount: 1, activity: '', reducedMotion: false, paused: false };
  const view = renderHook(p => useScrollPinning(p), { initialProps: props });
  const update = (changes: Partial<typeof props>) => { Object.assign(props, changes); view.rerender({ ...props }); };
  const flushFrames = () => act(() => { const queued = [...frames.values()]; frames.clear(); queued.forEach(cb => cb(0)); });
  flushFrames();
  return { ...view, metrics, el, update, flushFrames };
}

describe('transcript reading ownership', () => {
  it('protects the last update for 150ms then catches up at the 40px boundary', () => {
    const h = harness();
    act(() => { h.metrics.top = 560; h.result.current.onScroll(); });
    h.metrics.height = 1000;
    h.update({ content: 1 });
    act(() => vi.advanceTimersByTime(149));
    expect(h.metrics.top).toBe(560);
    act(() => vi.advanceTimersByTime(1));
    expect(h.metrics.top).toBe(700);
    h.unmount();
  });

  it('unpins at 41px and does not let thinking or later content pull the reader back', () => {
    const h = harness();
    act(() => { h.metrics.top = 559; h.result.current.onScroll(); });
    h.metrics.height = 1200;
    h.update({ content: 1, activity: 'thinking' });
    act(() => vi.advanceTimersByTime(500));
    h.flushFrames();
    expect(h.metrics.top).toBe(559);
    expect(h.result.current.showJumpToLatest).toBe(true);
    h.unmount();
  });

  it('does not start a manual cooldown for its own scroll event', () => {
    const h = harness();
    h.metrics.height = 1000;
    h.update({ content: 1 });
    act(() => h.result.current.onScroll());
    h.metrics.height = 1100;
    h.update({ content: 2 });
    expect(h.metrics.top).toBe(800);
    h.unmount();
  });

  it('cancels a queued frame on manual reading and a pending catchup on unpin', () => {
    const h = harness();
    h.metrics.height = 1000;
    h.update({ content: 1 });
    expect(frames.size).toBe(1);
    act(() => { h.metrics.top = 680; h.result.current.onScroll(); });
    expect(frames.size).toBe(0);
    h.metrics.height = 1100;
    h.update({ content: 2 });
    act(() => { h.metrics.top = 20; h.result.current.onScroll(); });
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(500));
    expect(h.metrics.top).toBe(20);
    h.unmount();
  });

  it('lets explicit jump/send override the cooldown and supports reduced motion', () => {
    const h = harness();
    act(() => { h.metrics.top = 100; h.result.current.onScroll(); });
    h.update({ reducedMotion: true });
    act(() => h.result.current.scrollToLatest({ smooth: true }));
    expect(h.metrics.top).toBe(600);
    expect(h.el.scrollTo).not.toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' }));
    h.unmount();
  });

  it('reports independent edges and removes both fades for short content', () => {
    const h = harness();
    expect(h.result.current).toMatchObject({ overflow: true, atStart: false, atEnd: true });
    act(() => { h.metrics.top = 0; h.result.current.onScroll(); });
    expect(h.result.current).toMatchObject({ atStart: true, atEnd: false });
    act(() => { h.metrics.top = 100; h.result.current.onScroll(); });
    expect(h.result.current).toMatchObject({ atStart: false, atEnd: false });
    h.metrics.height = 200;
    h.update({ content: 1 });
    expect(h.result.current).toMatchObject({ overflow: false, atStart: true, atEnd: true, showJumpToLatest: false });
    h.unmount();
  });

  it('drops pending source work on scope change, pause, cancel and unmount', () => {
    const h = harness();
    act(() => { h.metrics.top = 580; h.result.current.onScroll(); });
    h.update({ content: 1 });
    expect(vi.getTimerCount()).toBe(1);
    h.update({ scope: 'beta:b', paused: true });
    expect(vi.getTimerCount()).toBe(0);
    expect(frames.size).toBe(0);
    h.metrics.top = 10;
    act(() => vi.advanceTimersByTime(500));
    expect(h.metrics.top).toBe(10);
    h.update({ paused: false });
    expect(h.metrics.top).toBe(600);
    act(() => h.result.current.cancelPending());
    expect(frames.size).toBe(0);
    h.update({ content: 2 });
    h.unmount();
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
