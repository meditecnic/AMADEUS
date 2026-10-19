import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Renderer } from '../worldline-engine/engine/renderer';
import { Controller } from '../worldline-engine/show/controller';
import type { FrameState } from '../worldline-engine/show/frame';
import * as mock from '../worldline-engine/show/mock';
import { parseReading, T } from '../worldline-engine/show/timeline';
import type { Outcome } from '../worldline-engine/show/timeline';

const origin = parseReading('1.048596')!;
const target = parseReading('1.129848')!;
const options = { scenario: 'success' as const, reduced: false, origin, target };

function deferred() {
  let resolve!: (outcome: Outcome) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Outcome>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

describe('vendored controller outcome ownership', () => {
  let controller: Controller;
  let frame: FrameState;
  let now: number;
  let nextRaf: number;
  let callbacks: Map<number, FrameRequestCallback>;
  let render: ReturnType<typeof vi.fn>;
  let onChange: ReturnType<typeof vi.fn>;

  function advance(seconds: number) {
    for (let remaining = seconds * 1000; remaining > 0.001;) {
      const dt = Math.min(remaining, 50);
      remaining -= dt;
      now += dt;
      const scheduled = [...callbacks.values()];
      callbacks.clear();
      scheduled.forEach(callback => callback(now));
      expect(callbacks.size).toBeLessThanOrEqual(1);
    }
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    now = 0;
    nextRaf = 1;
    callbacks = new Map();
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const id = nextRaf++;
      callbacks.set(id, callback);
      return id;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => callbacks.delete(id));
    vi.spyOn(mock, 'mockSwitch');
    render = vi.fn((next: FrameState) => { frame = next; });
    onChange = vi.fn();
    controller = new Controller({ render, finish: vi.fn() } as unknown as Renderer, origin, { onChange });
  });

  afterEach(() => {
    controller.dispose();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('waits for the external result past every demo delay without starting a mock', async () => {
    controller.start({ ...options, outcome: deferred().promise });
    expect(mock.mockSwitch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(20_000);
    advance(12);
    expect(controller.snapshot()).toMatchObject({
      phase: 'running', outcome: null, waiting: true, showT: T.GATE,
    });
    expect(frame.readout.locked.some(Boolean)).toBe(false);
    expect(controller.rafScheduled).toBe(1);
  });

  it('accepts an external success and keeps the original final lock and ending', async () => {
    const result = deferred();
    controller.start({ ...options, scenario: 'fail', outcome: result.promise });
    result.resolve('success');
    await result.promise;
    expect(controller.snapshot().outcome).toBe('success');
    advance(7.6);
    expect(frame.readout.locked[7]).toBe(false);
    advance(0.1);
    expect(frame.readout.locked.every(Boolean)).toBe(true);
    expect(frame.readout.shown).toBe('1.129848');
    expect(controller.snapshot().phase).toBe('running');
    advance(3);
    expect(controller.snapshot()).toMatchObject({ phase: 'ended', shown: '1.129848' });
    expect(controller.rafScheduled).toBe(0);
  });

  it('uses the external failure instead of the successful demo scenario', async () => {
    const result = deferred();
    controller.start({ ...options, outcome: result.promise });
    result.resolve('fail');
    await result.promise;
    advance(9);
    expect(controller.snapshot()).toMatchObject({ phase: 'ended', outcome: 'fail', shown: '1.048596' });
    expect(frame.readout.locked.some(Boolean)).toBe(false);
    expect(controller.rafScheduled).toBe(0);
  });

  it('treats a rejected external result as failure and returns to the origin', async () => {
    const result = deferred();
    controller.start({ ...options, outcome: result.promise });
    result.reject(new Error('host request failed'));
    await Promise.resolve();
    expect(controller.snapshot().outcome).toBe('fail');
    advance(9);
    expect(controller.snapshot()).toMatchObject({ phase: 'ended', shown: '1.048596' });
  });

  it('keeps repeated pending skips at the gate until the same external result arrives', async () => {
    const result = deferred();
    controller.start({ ...options, outcome: result.promise });
    controller.skip();
    controller.skip();
    expect(controller.snapshot()).toMatchObject({
      phase: 'running', skipRequested: true, outcome: null, waiting: true, showT: T.GATE,
    });
    expect(frame.readout.locked.some(Boolean)).toBe(false);
    result.resolve('success');
    await result.promise;
    expect(controller.snapshot()).toMatchObject({ phase: 'ended', outcome: 'success', shown: '1.129848' });
    expect(frame.readout.locked.every(Boolean)).toBe(true);
    const changes = onChange.mock.calls.length;
    controller.skip();
    expect(onChange).toHaveBeenCalledTimes(changes);
    expect(callbacks.size).toBe(0);
  });

  it('ignores results from a replaced run and from an exited run', async () => {
    const first = deferred();
    const second = deferred();
    controller.start({ ...options, outcome: first.promise });
    const firstId = controller.snapshot().runId;
    controller.start({ ...options, outcome: second.promise });
    expect(controller.snapshot().runId).not.toBe(firstId);
    first.resolve('success');
    await first.promise;
    expect(controller.snapshot().outcome).toBeNull();
    controller.exit();
    const changes = onChange.mock.calls.length;
    second.reject(new Error('old request'));
    await Promise.resolve();
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', shown: '1.048596' });
    expect(onChange).toHaveBeenCalledTimes(changes);
    expect(callbacks.size).toBe(0);
  });

  it('cancels playback on disposal without acting on a later host result', async () => {
    const result = deferred();
    controller.start({ ...options, outcome: result.promise });
    controller.dispose();
    const renders = render.mock.calls.length;
    const changes = onChange.mock.calls.length;
    result.resolve('success');
    await result.promise;
    expect(render).toHaveBeenCalledTimes(renders);
    expect(onChange).toHaveBeenCalledTimes(changes);
    expect(controller.rafScheduled).toBe(0);
    expect(callbacks.size).toBe(0);
  });

  it('uses external results in reduced playback too', async () => {
    const result = deferred();
    controller.start({ ...options, reduced: true, outcome: result.promise });
    advance(2);
    expect(controller.snapshot()).toMatchObject({ waiting: true, outcome: null, shown: '1.048596' });
    result.resolve('success');
    await result.promise;
    advance(2);
    expect(controller.snapshot()).toMatchObject({ phase: 'ended', shown: '1.129848' });
    expect(mock.mockSwitch).not.toHaveBeenCalled();
  });

  it('retains the mock fallback when no external outcome is supplied and aborts it on exit', async () => {
    controller.start(options);
    expect(mock.mockSwitch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2600);
    expect(controller.snapshot().outcome).toBe('success');
    controller.start({ ...options, scenario: 'slow' });
    expect(vi.getTimerCount()).toBe(1);
    controller.exit();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(controller.snapshot().phase).toBe('idle');
    expect(callbacks.size).toBe(0);
  });
});
