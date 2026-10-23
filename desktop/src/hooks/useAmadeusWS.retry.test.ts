import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RECONNECT_ATTEMPTS, useAmadeusWS, type UseAmadeusWSParams } from './useAmadeusWS';

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string) { FakeWebSocket.instances.push(this); }
  send(payload: string) { this.sent.push(payload); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
  drop(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
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
const fail = (socket: FakeWebSocket) => act(() => { socket.onerror?.({}); socket.drop(); });

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

    act(() => latest().drop());
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

  it('manual reconnect recovers through session.ready and an ordinary drop starts at 1/5', () => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().onopen?.());
    act(() => latest().drop());
    for (const delay of [1000, 2000, 4000, 8000, 16000]) {
      act(() => { vi.advanceTimersByTime(delay); });
      // Closed without an error event: once exhausted it reads CLOSED, not ERROR.
      act(() => latest().drop());
    }
    expect(h.result.current.status).toBe('CLOSED');
    expect(h.result.current.retry?.exhausted).toBe(true);

    act(() => h.result.current.reconnect());
    expect(h.result.current.status).toBe('CONNECTING');
    expect(h.result.current.retry).toBeNull();
    act(() => latest().open());
    act(() => latest().emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conversation-one' }));
    expect(h.result.current.status).toBe('READY');
    expect(h.result.current.sessionState).toBe('ready');
    expect(h.result.current.retry).toBeNull();
    act(() => latest().drop());
    expect(h.result.current.retry).toEqual({ attempt: 1, max: RECONNECT_ATTEMPTS, exhausted: false });
    act(() => { vi.advanceTimersByTime(1000); });
    act(() => latest().onopen?.());
    expect(h.result.current.status).toBe('READY');
    expect(h.result.current.retry).toMatchObject({ attempt: 1, exhausted: false });
    act(() => latest().emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conversation-one' }));
    expect(h.result.current.retry).toBeNull();
    act(() => latest().drop());
    expect(h.result.current.retry).toMatchObject({ attempt: 1, exhausted: false });
    h.unmount();
  });

  it.each([
    [4403, 'forbidden'],
    [4409, 'replaced'],
  ] as const)('stops automatic reconnection after an open socket closes with %i, but permits manual reconnect', (code, reason) => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().open());
    if (code === 4409) {
      act(() => latest().emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conversation-one' }));
    }
    act(() => latest().drop(code));

    expect(h.result.current.status).toBe('CLOSED');
    expect(h.result.current.sessionState).toBe('unknown');
    expect(h.result.current.retry).toBeNull();
    expect(h.result.current.reconnectStopReason).toBe(reason);
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => h.result.current.reconnect());
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(h.result.current.status).toBe('CONNECTING');
    expect(h.result.current.reconnectStopReason).toBeNull();
    act(() => latest().open());
    // Even a stable rejected session must honor a later policy close.
    act(() => latest().emit({ type: 'error', message: '缺失 deepseek API Key。请打开设置保存供应商凭据后重新连接。' }));
    act(() => vi.advanceTimersByTime(30_000));
    act(() => latest().drop(code));
    expect(h.result.current.reconnectStopReason).toBe(reason);
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(2);
    h.unmount();
  });

  it.each([false, true])('restores all five retries after a rejected session stays open (recoverable=%s)', (recoverable) => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().open());
    act(() => latest().drop());
    act(() => vi.advanceTimersByTime(1000));
    const socket = latest();
    act(() => socket.open());
    expect(h.result.current.retry).toEqual({ attempt: 1, max: 5, exhausted: false });

    act(() => socket.emit({ type: 'error', message: '缺失 deepseek API Key。请打开设置保存供应商凭据后重新连接。', recoverable }));
    expect(h.result.current.sessionState).toBe('rejected');
    expect(h.result.current.retry).toBeNull();
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(2);

    act(() => socket.drop());
    [1000, 2000, 4000, 8000, 16000].forEach((delay, index) => {
      expect(h.result.current.retry).toEqual({ attempt: index + 1, max: 5, exhausted: false });
      const before = FakeWebSocket.instances.length;
      act(() => vi.advanceTimersByTime(delay - 1));
      expect(FakeWebSocket.instances).toHaveLength(before);
      act(() => vi.advanceTimersByTime(1));
      expect(FakeWebSocket.instances).toHaveLength(before + 1);
      fail(latest());
    });
    expect(h.result.current.retry).toEqual({ attempt: 5, max: 5, exhausted: true });
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(7);
    h.unmount();
  });

  it.each([1000, 1006])('bounds immediate auth rejection and close cycles with code %i', (code) => {
    const h = renderHook(() => useAmadeusWS(params));
    const rejectAndDrop = () => act(() => {
      latest().open();
      latest().emit({ type: 'error', message: '缺失 deepseek API Key。请打开设置保存供应商凭据后重新连接。' });
      latest().drop(code);
    });
    rejectAndDrop();

    [1000, 2000, 4000, 8000, 16000].forEach((delay, index) => {
      expect(h.result.current.retry).toEqual({ attempt: index + 1, max: 5, exhausted: false });
      act(() => vi.advanceTimersByTime(delay - 1));
      expect(FakeWebSocket.instances).toHaveLength(index + 1);
      act(() => vi.advanceTimersByTime(1));
      expect(FakeWebSocket.instances).toHaveLength(index + 2);
      rejectAndDrop();
    });
    expect(h.result.current.retry).toEqual({ attempt: 5, max: 5, exhausted: true });
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(6);
    h.unmount();
  });

  it.each([
    [29_999, 2],
    [30_000, 1],
  ])('only restores a rejected socket budget after a stable interval (%i ms)', (elapsed, nextAttempt) => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().open());
    act(() => latest().drop());
    act(() => vi.advanceTimersByTime(1000));
    act(() => latest().open());
    act(() => latest().emit({ type: 'error', message: '缺失 deepseek API Key。请打开设置保存供应商凭据后重新连接。' }));
    act(() => vi.advanceTimersByTime(elapsed));
    // Repeated rejection frames must not restart the stability interval.
    act(() => latest().emit({ type: 'error', message: '未认证。请打开设置并配置您的 API Key。' }));
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => latest().drop());
    expect(h.result.current.retry).toEqual({ attempt: nextAttempt, max: 5, exhausted: false });
    h.unmount();
  });

  it('stops a 4409 close during retry 3/5 with no timers and permits manual recovery', () => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().open());
    act(() => latest().drop());
    [1000, 2000, 4000].forEach((delay, index) => {
      act(() => vi.advanceTimersByTime(delay));
      act(() => latest().open());
      expect(h.result.current.retry).toEqual({ attempt: index + 1, max: 5, exhausted: false });
      act(() => latest().drop(index === 2 ? 4409 : 1006));
    });

    expect(h.result.current.status).toBe('CLOSED');
    expect(h.result.current.retry).toBeNull();
    expect(h.result.current.reconnectStopReason).toBe('replaced');
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(4);

    act(() => h.result.current.reconnect());
    act(() => latest().open());
    act(() => latest().emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conversation-one' }));
    expect(h.result.current.status).toBe('READY');
    expect(h.result.current.sessionState).toBe('ready');
    expect(h.result.current.retry).toBeNull();
    expect(h.result.current.reconnectStopReason).toBeNull();
    act(() => latest().drop());
    expect(h.result.current.retry).toEqual({ attempt: 1, max: 5, exhausted: false });
    h.unmount();
  });

  it('ends an interrupted turn on eviction and clears watchdog, heartbeat and automatic reconnection', () => {
    const h = renderHook(() => useAmadeusWS(params));
    const socket = latest();
    act(() => socket.open());
    act(() => socket.emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conversation-one' }));
    act(() => h.result.current.sendChat('测试问题'));
    act(() => socket.emit({ type: 'turn.started', turn_id: 'turn-one', generation: 1, conversation_id: 'conversation-one' }));
    expect(h.result.current.isThinking).toBe(true);
    expect(vi.getTimerCount()).toBe(2);

    act(() => socket.drop(4409));
    expect(h.result.current.status).toBe('CLOSED');
    expect(h.result.current.retry).toBeNull();
    expect(h.result.current.isThinking).toBe(false);
    expect(h.result.current.messages.filter((message) => message.systemError)).toEqual([
      expect.objectContaining({ content: '这一问中断了，没有收到回复。请再发一次。' }),
    ]);
    expect(h.result.current.messages.every((message) => !message.streaming)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    const sent = socket.sent.length;
    act(() => vi.advanceTimersByTime(120_000));
    expect(socket.sent).toHaveLength(sent);
    expect(FakeWebSocket.instances).toHaveLength(1);
    h.unmount();
  });

  it('keeps the retry budget when sockets open then close before session.ready, stopping at 5/5', () => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().open());
    act(() => latest().drop());

    [1000, 2000, 4000, 8000, 16000].forEach((delay, index) => {
      expect(h.result.current.retry).toEqual({ attempt: index + 1, max: 5, exhausted: false });
      act(() => vi.advanceTimersByTime(delay));
      expect(FakeWebSocket.instances).toHaveLength(index + 2);
      act(() => latest().open());
      expect(h.result.current.sessionState).toBe('unknown');
      expect(h.result.current.retry).toEqual({ attempt: index + 1, max: 5, exhausted: false });
      act(() => latest().drop());
    });

    expect(h.result.current.retry).toEqual({ attempt: 5, max: 5, exhausted: true });
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(6);
    h.unmount();
  });

  it('retries an ordinary drop from a ready session with delays 1, 2, 4, 8, 16 seconds, then stops', () => {
    const h = renderHook(() => useAmadeusWS(params));
    act(() => latest().open());
    act(() => latest().emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conversation-one' }));
    act(() => latest().drop(1006));

    [1000, 2000, 4000, 8000, 16000].forEach((delay, index) => {
      expect(h.result.current.retry).toEqual({ attempt: index + 1, max: 5, exhausted: false });
      act(() => vi.advanceTimersByTime(delay - 1));
      expect(FakeWebSocket.instances).toHaveLength(index + 1);
      act(() => vi.advanceTimersByTime(1));
      expect(FakeWebSocket.instances).toHaveLength(index + 2);
      fail(latest());
    });

    expect(h.result.current.retry).toEqual({ attempt: 5, max: 5, exhausted: true });
    expect(h.result.current.status).toBe('ERROR');
    act(() => vi.advanceTimersByTime(120_000));
    expect(FakeWebSocket.instances).toHaveLength(6);
    h.unmount();
  });
});
