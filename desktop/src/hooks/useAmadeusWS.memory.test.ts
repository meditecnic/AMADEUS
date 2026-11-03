import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAmadeusWS, type UseAmadeusWSParams } from './useAmadeusWS';

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.OPEN;
  sent: string[] = [];
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
    this.readyState = 3;
  }
  emit(payload: object) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

const params: UseAmadeusWSParams = {
  sessionId: 'session-memory',
  conversationId: 'c',
  systemPrompt: 'prompt',
  temperature: 0.8,
  worldline: 'steins_gate',
  enableTts: false,
  sovitsUrl: '',
  providerId: 'deepseek',
  model: 'deepseek-v4-flash',
  reasoningEffort: 'high',
};

const AT = '2026-10-12T08:01:22.123456+00:00';
const ref = (id: string, extra: object = {}) => ({ kind: 'fact', id, via: 'supplement', label: `label ${id}`, created_at: AT, ...extra });
const change = (id: string, extra: object = {}) => ({ kind: 'fact', id, change: 'create', label: `label ${id}`, created_at: AT, ...extra });
const referenced = (extra: object = {}) => ({
  type: 'memory.referenced', conversation_id: 'c', turn_id: 't1', generation: 1, items: [ref('f1'), ref('f2', { kind: 'experience', via: 'episodic' })], ...extra,
});
const recorded = (extra: object = {}) => ({
  type: 'memory.recorded', conversation_id: 'c', turn_id: 't1', source_message_id: 7, changes: [change('f1')], ...extra,
});

function setup() {
  const hook = renderHook(() => useAmadeusWS(params));
  const socket = FakeWebSocket.instances[0];
  act(() => socket.onopen?.());
  act(() => { hook.result.current.sendChat('记住我喜欢茶'); });
  act(() => {
    socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't1', generation: 1 });
    socket.emit({
      type: 'segment.ready', conversation_id: 'c', turn_id: 't1', generation: 1,
      segment_id: 0, ja: 'はい。', zh: '好的。', emotion: 'neutral',
    });
  });
  return { ...hook, socket };
}

const assistant = (messages: ReturnType<typeof setup>['result']['current']['messages']) => messages.find((m) => m.role === 'assistant');
const user = (messages: ReturnType<typeof setup>['result']['current']['messages']) => messages.find((m) => m.role === 'user');

describe('useAmadeusWS memory markers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });
  afterEach(() => {
    FakeWebSocket.instances = [];
    vi.unstubAllGlobals();
    vi.useRealTimers();
    try { localStorage.clear(); } catch {}
  });

  it('hangs memory.referenced on the assistant message of the turn', () => {
    const { result, socket, unmount } = setup();
    act(() => socket.emit(referenced()));
    const refs = assistant(result.current.messages)?.memoryRefs;
    expect(refs?.items.map((item) => [item.kind, item.id, item.via])).toEqual([['fact', 'f1', 'supplement'], ['experience', 'f2', 'episodic']]);
    expect(user(result.current.messages)?.memoryRefs).toBeUndefined();
    unmount();
  });

  it('hangs memory.recorded on the user line before the turn, before and after turn.completed', () => {
    const { result, socket, unmount } = setup();
    act(() => socket.emit(recorded()));
    expect(user(result.current.messages)?.memoryRecorded?.map((c) => c.id)).toEqual(['f1']);
    act(() => socket.emit({ type: 'turn.completed', conversation_id: 'c', turn_id: 't1', generation: 1 }));
    act(() => socket.emit(recorded({ changes: [change('f9', { kind: 'experience' })] })));
    expect(user(result.current.messages)?.memoryRecorded?.map((c) => c.id)).toEqual(['f1', 'f9']);
    expect(assistant(result.current.messages)?.memoryRecorded).toBeUndefined();
    unmount();
  });

  it('attaches memory.referenced on both sides of turn.completed', () => {
    const { result, socket, unmount } = setup();
    act(() => socket.emit({ type: 'turn.completed', conversation_id: 'c', turn_id: 't1', generation: 1 }));
    act(() => socket.emit(referenced()));
    expect(assistant(result.current.messages)?.memoryRefs?.items).toHaveLength(2);
    unmount();
  });

  it('drops frames for another conversation, another generation or another turn', () => {
    const { result, socket, unmount } = setup();
    act(() => {
      socket.emit(referenced({ conversation_id: 'other' }));
      socket.emit(referenced({ generation: 2 }));
      socket.emit(referenced({ turn_id: 'old' }));
      socket.emit(recorded({ conversation_id: 'other' }));
      socket.emit(recorded({ turn_id: 'gone' }));
    });
    expect(assistant(result.current.messages)?.memoryRefs).toBeUndefined();
    expect(user(result.current.messages)?.memoryRecorded).toBeUndefined();
    unmount();
  });

  it('drops malformed frames without disturbing the conversation', () => {
    const { result, socket, unmount } = setup();
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    act(() => {
      socket.emit(referenced({ items: [] }));
      socket.emit(referenced({ items: [ref('f1'), ref('f2', { via: 'guess' })] }));
      socket.emit(referenced({ items: [ref('f1', { created_at: 'yesterday' })] }));
      socket.emit(referenced({ items: [ref('f1', { label: 3 })] }));
      socket.emit(referenced({ generation: '1' }));
      socket.emit(recorded({ changes: [change('f1', { change: 'attach' })] }));
      socket.emit(recorded({ changes: [change('f1', { kind: 'topic' })] }));
      socket.emit(recorded({ source_message_id: 'x' }));
      socket.emit(recorded({ changes: [] }));
      socket.emit({ type: 'memory.recorded' });
    });
    expect(assistant(result.current.messages)?.memoryRefs).toBeUndefined();
    expect(user(result.current.messages)?.memoryRecorded).toBeUndefined();
    expect(result.current.messages.some((m) => m.systemError)).toBe(false);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
    unmount();
  });

  it('merges several memory.recorded frames of one turn by id, the later one winning', () => {
    const { result, socket, unmount } = setup();
    act(() => socket.emit(recorded({ changes: [change('f1'), change('f2')] })));
    act(() => socket.emit(recorded({ changes: [change('f1', { change: 'supersede', label: '改口以后' })] })));
    expect(user(result.current.messages)?.memoryRecorded).toEqual([
      expect.objectContaining({ id: 'f1', change: 'supersede', label: '改口以后' }),
      expect.objectContaining({ id: 'f2', change: 'create' }),
    ]);
    unmount();
  });

  it('keeps the first of a repeated record and caps references at 32', () => {
    const { result, socket, unmount } = setup();
    const many = Array.from({ length: 40 }, (_, i) => ref(`f${i}`));
    act(() => socket.emit(referenced({ items: [ref('f0', { via: 'recall' }), ...many] })));
    const items = assistant(result.current.messages)?.memoryRefs?.items ?? [];
    expect(items).toHaveLength(32);
    expect(items[0]).toMatchObject({ id: 'f0', via: 'recall' });
    unmount();
  });

  it('drops late references after the turn was cancelled', () => {
    const { result, socket, unmount } = setup();
    act(() => result.current.cancelTurn());
    act(() => socket.emit(referenced()));
    expect(assistant(result.current.messages)?.memoryRefs).toBeUndefined();
    unmount();
  });
});
