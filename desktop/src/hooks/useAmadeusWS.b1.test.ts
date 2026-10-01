import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAmadeusWS, type UseAmadeusWSParams } from './useAmadeusWS';

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.OPEN;
  sent: string[] = [];
  closeCalled = false;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(payload: string) {
    this.sent.push(payload);
  }
  close() {
    this.closeCalled = true;
    this.readyState = 3;
  }
  emit(payload: object) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

const params: UseAmadeusWSParams = {
  sessionId: 'session-b1',
  conversationId: 'conversation-b1',
  systemPrompt: 'prompt',
  temperature: 0.8,
  worldline: 'steins_gate',
  enableTts: true,
  sovitsUrl: '',
  providerId: 'deepseek',
  model: 'deepseek-v4-flash',
  reasoningEffort: 'high',
};

describe('useAmadeusWS B1 liveness + audio degradation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });

  afterEach(() => {
    FakeWebSocket.instances = [];
    vi.unstubAllGlobals();
    vi.useRealTimers();
    try {
      localStorage.clear();
    } catch {}
  });

  it('keeps an active turn alive past 30s while turn.heartbeat arrives', () => {
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => result.current.sendChat('hello'));
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
    });

    // 10s, 20s, 30s, 35s heartbeats: explicit liveness, no content/progress.
    for (let i = 0; i < 4; i += 1) {
      act(() => {
        vi.advanceTimersByTime(9000);
      });
      act(() => {
        socket.emit({ type: 'turn.heartbeat', conversation_id: 'c', turn_id: 't', generation: 1 });
      });
    }
    act(() => {
      vi.advanceTimersByTime(9000);
    });

    expect(socket.closeCalled).toBe(false);
    expect(result.current.status).not.toBe('ERROR');
    unmount();
  });

  it('shows an interrupted turn after an earlier completed reply', () => {
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => result.current.sendChat('first'));
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 'earlier', generation: 1 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 'earlier', generation: 1,
        segment_id: 0, ja: '前の返事。', zh: '之前的回复。', emotion: 'neutral',
      });
      socket.emit({ type: 'turn.completed', conversation_id: 'c', turn_id: 'earlier', generation: 1 });
    });
    act(() => result.current.sendChat('second'));
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 2 });
    });

    act(() => {
      vi.advanceTimersByTime(31000);
    });

    expect(socket.closeCalled).toBe(true);
    expect(result.current.messages.some((message) => message.content.includes('前の返事'))).toBe(true);
    expect(result.current.messages.some((message) => (
      message.systemError && message.content.includes('这一问中断了')
    ))).toBe(true);
    unmount();
  });

  it('surfaces at most one voice degradation per turn', () => {
    const onAudioDegraded = vi.fn();
    const { result, unmount } = renderHook(() =>
      useAmadeusWS({ ...params, onAudioDegraded }),
    );
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 0, ja: '一。', zh: '一。', emotion: 'neutral',
      });
      socket.emit({
        type: 'segment.audio_error', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 0, reason: 'service_error', emotion: 'neutral',
      });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 1, ja: '二。', zh: '二。', emotion: 'neutral',
      });
      socket.emit({
        type: 'segment.audio_error', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 1, reason: 'timeout', emotion: 'neutral',
      });
    });

    expect(onAudioDegraded).toHaveBeenCalledTimes(1);
    // Text/subtitle still complete despite audio failures.
    const assistant = result.current.messages.find((m) => m.turnId === 't');
    expect(assistant?.content).toBe('一。二。');
    unmount();
  });

  it('cancelTurn ends streaming at once and survives 31s after an ignored ACK', () => {
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => result.current.sendChat('hello'));
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 0, ja: '一。', zh: '一。', emotion: 'neutral',
      });
    });

    act(() => result.current.cancelTurn());
    // Partial content preserved, streaming ended synchronously.
    const afterCancel = result.current.messages.find((m) => m.turnId === 't');
    expect(afterCancel?.streaming).toBe(false);
    expect(afterCancel?.content).toBe('一。');

    // Server ACK arrives after local identity was cleared -> ignored,
    // but must not leave the old watchdog behind.
    act(() => {
      socket.emit({ type: 'turn.cancelled', conversation_id: 'c', turn_id: 't', generation: 1 });
    });
    act(() => {
      vi.advanceTimersByTime(31000);
    });

    expect(socket.closeCalled).toBe(false);
    expect(result.current.status).not.toBe('ERROR');
    expect(result.current.messages.find((m) => m.turnId === 't')?.streaming).toBe(false);
    expect(socket.sent.map((p) => JSON.parse(p).type)).toContain('turn.cancel');
    unmount();
  });

  it('does not re-arm the old turn on a busy resend without turn.started', () => {
    const onAudioDegraded = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, onAudioDegraded }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't1', generation: 1 });
      socket.emit({
        type: 'segment.audio_error', conversation_id: 'c', turn_id: 't1', generation: 1,
        segment_id: 0, reason: 'service_error', emotion: 'neutral',
      });
    });
    expect(onAudioDegraded).toHaveBeenCalledTimes(1);

    // Resend accepted client-side; server busy-rejects so no new turn.started follows.
    act(() => result.current.sendChat('resend while busy'));
    act(() => {
      socket.emit({
        type: 'segment.audio_error', conversation_id: 'c', turn_id: 't1', generation: 1,
        segment_id: 1, reason: 'timeout', emotion: 'neutral',
      });
    });
    expect(onAudioDegraded).toHaveBeenCalledTimes(1);

    // The next real turn re-arms exactly once.
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't2', generation: 2 });
      socket.emit({
        type: 'segment.audio_error', conversation_id: 'c', turn_id: 't2', generation: 2,
        segment_id: 0, reason: 'timeout', emotion: 'neutral',
      });
      socket.emit({
        type: 'segment.audio_error', conversation_id: 'c', turn_id: 't2', generation: 2,
        segment_id: 1, reason: 'timeout', emotion: 'neutral',
      });
    });
    expect(onAudioDegraded).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('keeps per-send degradation behavior for legacy v1 text_chunk', () => {
    const onAudioDegraded = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, onAudioDegraded }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => result.current.sendChat('a'));
    act(() => {
      socket.emit({ type: 'text_chunk', content: 'あ', emotion: 'neutral', audio_degraded: true, degraded_reason: 'service_error' });
    });
    expect(onAudioDegraded).toHaveBeenCalledTimes(1);

    act(() => result.current.sendChat('b'));
    act(() => {
      socket.emit({ type: 'text_chunk', content: 'い', emotion: 'neutral', audio_degraded: true, degraded_reason: 'timeout' });
    });
    expect(onAudioDegraded).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('drops late heartbeat/ready/audio after cancelTurn', () => {
    const onAudioChunk = vi.fn();
    const { result, unmount } = renderHook(() =>
      useAmadeusWS({ ...params, onAudioChunk }),
    );
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      result.current.cancelTurn();
    });
    const countAfterCancel = result.current.messages.length;

    act(() => {
      socket.emit({ type: 'turn.heartbeat', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 0, ja: '迟到', zh: '迟到', emotion: 'neutral',
      });
      socket.emit({
        type: 'segment.audio', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 0, data: 'bGF0ZQ==',
      });
    });

    expect(result.current.messages.length).toBe(countAfterCancel);
    expect(onAudioChunk).not.toHaveBeenCalled();
    unmount();
  });
});
