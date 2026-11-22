import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAmadeusWS, type Message, type PendingConversationTarget, type UseAmadeusWSParams } from './useAmadeusWS';
import { parseLeadingEmotion } from '../workstation/emotion';

class Socket {
  static OPEN = 1;
  static all: Socket[] = [];
  readyState = 0;
  frames: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  constructor(public url: string) { Socket.all.push(this); }
  send(s: string) { this.frames.push(JSON.parse(s)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  emit(frame: object) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}

const latest = () => Socket.all.at(-1)!;
const base: UseAmadeusWSParams = {
  conversationId: null,
  worldline: 'steins_gate',
  sessionId: 'emo-272-unit',
  systemPrompt: '',
  enableTts: false,
  sovitsUrl: '',
  model: 'deepseek-chat',
  providerId: 'deepseek',
  reasoningEffort: null,
};

const histA: Message[] = [
  { id: 'a1', role: 'user', content: 'hi' },
  { id: 'a2', role: 'assistant', content: 'かなしい。', emotion: 'sad' },
];
const histB: Message[] = [
  { id: 'b1', role: 'assistant', content: 'うれしい！', emotion: 'happy' },
  { id: 'b2', role: 'user', content: 'よかった' },
];

beforeEach(() => {
  Socket.all = [];
  vi.stubGlobal('WebSocket', Socket);
  vi.spyOn(console, 'log').mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('per-conversation emotion in useAmadeusWS', () => {
  it('updates portrait emotion when switching between A (sad), B (happy), empty, and draft', () => {
    const h = renderHook((p: UseAmadeusWSParams) => useAmadeusWS(p), { initialProps: base });
    act(() => {
      latest().open();
      latest().emit({ type: 'session.ready', conversation_mode: 'draft', conversation_id: null, worldline: 'steins_gate', emotion: 'neutral' });
    });
    expect(h.result.current.emotion).toBe('neutral');

    // Switch live to A (sad) via wire ack emotion
    act(() => { h.result.current.switchConversation('conv-a', histA); });
    const reqA = latest().frames.at(-1)!;
    act(() => latest().emit({ type: 'conversation.switched', request_id: reqA.request_id, conversation_id: 'conv-a', worldline: 'steins_gate', emotion: 'sad' }));
    expect(h.result.current.emotion).toBe('sad');

    // Switch live to B (happy) without wire emotion (fallback to history's last assistant emotion)
    act(() => { h.result.current.switchConversation('conv-b', histB); });
    const reqB = latest().frames.at(-1)!;
    act(() => latest().emit({ type: 'conversation.switched', request_id: reqB.request_id, conversation_id: 'conv-b', worldline: 'steins_gate' }));
    expect(h.result.current.emotion).toBe('happy');

    // Switch back to A via pendingConversation reconnect (session.ready must not reset to neutral)
    const pendingA: PendingConversationTarget = {
      conversationId: 'conv-a', worldline: 'steins_gate', requestEpoch: 1,
      history: histA, providerId: 'deepseek', model: 'deepseek-chat',
    };
    h.rerender({ ...base, pendingConversation: pendingA });
    act(() => h.result.current.reconnect());
    act(() => {
      latest().open();
      latest().emit({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conv-a', worldline: 'steins_gate', emotion: 'sad' });
    });
    expect(h.result.current.emotion).toBe('sad');

    // Switch to empty conversation -> neutral
    h.rerender({ ...base, pendingConversation: null });
    act(() => { h.result.current.switchConversation('conv-empty', []); });
    const reqEmpty = latest().frames.at(-1)!;
    act(() => latest().emit({ type: 'conversation.switched', request_id: reqEmpty.request_id, conversation_id: 'conv-empty', worldline: 'steins_gate', emotion: 'neutral' }));
    expect(h.result.current.emotion).toBe('neutral');

    // Move to happy, then switch worldline to draft -> neutral
    act(() => { h.result.current.switchConversation('conv-b', histB); });
    const reqB2 = latest().frames.at(-1)!;
    act(() => latest().emit({ type: 'conversation.switched', request_id: reqB2.request_id, conversation_id: 'conv-b', worldline: 'steins_gate', emotion: 'happy' }));
    expect(h.result.current.emotion).toBe('happy');
    act(() => { h.result.current.switchWorldline('beta', 'wl-1'); });
    act(() => latest().emit({ type: 'worldline.switched', request_id: 'wl-1', worldline: 'beta', emotion: 'neutral' }));
    expect(h.result.current.emotion).toBe('neutral');
    h.unmount();
  });

  it('parses leading EMO tags and rejects unknown tags to neutral', () => {
    expect(parseLeadingEmotion('[EMO:sad] かなしい。')).toBe('sad');
    expect(parseLeadingEmotion('  [EMO:HAPPY] うれしい！')).toBe('happy');
    expect(parseLeadingEmotion('[EMO:unknown] だれ？')).toBeUndefined();
    expect(parseLeadingEmotion('タグなし。')).toBeUndefined();
  });
});
