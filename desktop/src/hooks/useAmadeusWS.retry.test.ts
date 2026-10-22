import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RECONNECT_ATTEMPTS, useAmadeusWS, type UseAmadeusWSParams } from './useAmadeusWS';

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string) { FakeWebSocket.instances.push(this); }
  send(payload: string) { this.sent.push(payload); }
  close() { this.readyState = 3; }
}

const params: UseAmadeusWSParams = {
  sessionId: 'session-one',
  conversationId: 'conversation-one',
  systemPrompt: 'prompt',
  temperature: 0.8,
  worldline: 'steins_gate',
  enableTts: false,
  sovitsUrl: '',
  providerId: 'deepseek',
  model: 'deepseek-v4-flash',
  reasoningEffort: 'high',
};

const latest = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
/** A connection attempt that never opens: the error event, then the close that always follows it. */
const fail = (socket: FakeWebSocket) => act(() => { socket.onerror?.({}); socket.onclose?.(); });

describe('useAmadeusWS automatic reconnection', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.spyOn(console, 'log').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });
  afterEach(() => {
    FakeWebSocket.instances = [];
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('counts attempts n/5 after a drop, keeps a socket error from flashing ERROR, and gives up as ERROR', () => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().onopen?.());
    expect(h.result.current.status).toBe('READY');
    expect(h.result.current.retry).toBeNull();

    act(() => latest().onclose?.());
    expect(h.result.current.status).toBe('CLOSED');
    expect(h.result.current.retry).toEqual({ attempt: 1, max: RECONNECT_ATTEMPTS, exhausted: false });

    const delays = [1000, 2000, 4000, 8000, 16000];
    delays.forEach((delay, i) => {
      const before = FakeWebSocket.instances.length;
      act(() => { vi.advanceTimersByTime(delay - 1); });
      expect(FakeWebSocket.instances).toHaveLength(before);
      act(() => { vi.advanceTimersByTime(1); });
      expect(FakeWebSocket.instances).toHaveLength(before + 1);
      // While an attempt is in flight the count stays on screen.
      expect(h.result.current.status).toBe('CONNECTING');
      expect(h.result.current.retry).toMatchObject({ attempt: i + 1, exhausted: false });
      fail(latest());
      if (i < delays.length - 1) {
        // Retries remain: the error does not surface as ERROR between attempts.
        expect(h.result.current.status).toBe('CLOSED');
        expect(h.result.current.retry).toMatchObject({ attempt: i + 2, exhausted: false });
      }
    });

    expect(h.result.current.status).toBe('ERROR');
    expect(h.result.current.retry).toEqual({ attempt: RECONNECT_ATTEMPTS, max: RECONNECT_ATTEMPTS, exhausted: true });
    const count = FakeWebSocket.instances.length;
    act(() => { vi.advanceTimersByTime(120_000); });
    expect(FakeWebSocket.instances).toHaveLength(count);
    h.unmount();
  });

  it('reconnect() starts a fresh set of attempts after giving up', () => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().onopen?.());
    act(() => latest().onclose?.());
    for (const delay of [1000, 2000, 4000, 8000, 16000]) {
      act(() => { vi.advanceTimersByTime(delay); });
      // Closed without an error event: once exhausted it reads CLOSED, not ERROR.
      act(() => latest().onclose?.());
    }
    expect(h.result.current.status).toBe('CLOSED');
    expect(h.result.current.retry?.exhausted).toBe(true);

    act(() => h.result.current.reconnect());
    expect(h.result.current.status).toBe('CONNECTING');
    expect(h.result.current.retry).toBeNull();
    act(() => latest().onclose?.());
    expect(h.result.current.retry).toEqual({ attempt: 1, max: RECONNECT_ATTEMPTS, exhausted: false });
    act(() => { vi.advanceTimersByTime(1000); });
    act(() => latest().onopen?.());
    expect(h.result.current.status).toBe('READY');
    expect(h.result.current.retry).toBeNull();
    h.unmount();
  });
});
