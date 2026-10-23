import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAmadeusWS, type PendingConversationTarget, type UseAmadeusWSParams } from './useAmadeusWS';

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
  drop() { this.readyState = 3; this.onclose?.({ code: 1006 }); }
  emit(frame: object) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}
const latest = () => Socket.all.at(-1)!;
const source = [{ id: 'old-message', role: 'user' as const, content: 'source transcript' }];
const target = (epoch = 1): PendingConversationTarget => ({
  conversationId: 'new', worldline: 'steins_gate', requestEpoch: epoch,
  history: [{ id: 'new-message', role: 'user', content: 'target transcript' }],
  providerId: 'local', model: 'target-model',
});
const base: UseAmadeusWSParams = {
  conversationId: 'old', worldline: 'steins_gate', sessionId: 'pending-unit',
  systemPrompt: '', temperature: 0, enableTts: false, sovitsUrl: '',
  model: 'source-model', providerId: 'deepseek', reasoningEffort: null,
};
const ready = { type: 'session.ready', conversation_mode: 'history', conversation_id: 'new', worldline: 'steins_gate' };
function setup(overrides: Partial<UseAmadeusWSParams> = {}) {
  const h = renderHook((p: UseAmadeusWSParams) => useAmadeusWS(p), { initialProps: { ...base, ...overrides } });
  act(() => {
    latest().open();
    latest().emit({ ...ready, conversation_id: 'old' });
    h.result.current.switchConversation('old', source);
    const req = latest().frames.at(-1)!;
    latest().emit({ type: 'conversation.switched', request_id: req.request_id, conversation_id: 'old' });
  });
  const stage = (pending = target()) => {
    h.rerender({ ...base, ...overrides, pendingConversation: pending });
    act(() => h.result.current.reconnect());
    act(() => latest().open());
  };
  return { ...h, stage };
}
beforeEach(() => {
  vi.useFakeTimers(); Socket.all = [];
  vi.stubGlobal('WebSocket', Socket);
  vi.spyOn(console, 'log').mockImplementation(() => {});
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear(); });

describe('pending conversation auth ownership', () => {
  it.each([
    { conversation_id: 'old' }, { worldline: 'beta' }, { conversation_mode: 'draft' },
  ])('keeps source and blocks send for a mismatched ready: %j', mismatch => {
    const h = setup(); h.stage();
    act(() => { latest().emit({ ...ready, ...mismatch }); h.result.current.sendChat('blocked'); });
    expect(h.result.current.sessionState).toBe('unknown');
    expect(h.result.current.messages).toEqual(source);
    expect(latest().frames.filter(f => f.type === 'chat.send')).toEqual([]);
    act(() => latest().emit(ready));
    expect(h.result.current.messages).toEqual(target().history);
    expect(h.result.current.sessionState).toBe('ready');
    h.unmount();
  });

  it('rejects the same ID from an older request epoch and a retired socket', () => {
    const onSessionReady = vi.fn();
    const h = setup({ onSessionReady }); h.stage(target(1));
    const retired = latest().onmessage!;
    h.rerender({ ...base, onSessionReady, pendingConversation: target(2) });
    act(() => latest().emit(ready));
    expect(h.result.current.sessionState).toBe('unknown');
    expect(h.result.current.messages).toEqual(source);
    act(() => h.result.current.reconnect());
    act(() => { latest().open(); retired({ data: JSON.stringify(ready) }); });
    expect(h.result.current.sessionState).toBe('unknown');
    act(() => latest().emit(ready));
    expect(onSessionReady.mock.lastCall?.[0]).toMatchObject({ requestEpoch: 2, conversationId: 'new' });
    expect(h.result.current.messages).toEqual(target().history);
    h.unmount();
  });

  it('preserves intent through repeated automatic disconnect/connect cycles without wire fields', () => {
    const h = setup(); h.stage();
    for (const delay of [1000, 2000, 4000]) {
      act(() => latest().drop());
      act(() => vi.advanceTimersByTime(delay));
      act(() => latest().open());
      expect(latest().frames[0]).toMatchObject({
        type: 'auth', conversation_id: 'new', conversation_mode: 'history', provider_id: 'local', model: 'target-model',
      });
      expect(latest().frames[0]).not.toHaveProperty('requestEpoch');
      expect(latest().frames[0]).not.toHaveProperty('history');
      expect(h.result.current.messages).toEqual(source);
    }
    act(() => latest().emit(ready));
    expect(h.result.current.sessionState).toBe('ready');
    h.unmount();
  });

  it('does not publish a switch ACK or late source turn before target session.ready', () => {
    const onConversationSwitched = vi.fn();
    const h = setup({ onConversationSwitched });
    h.rerender({ ...base, onConversationSwitched, pendingConversation: target() });
    act(() => h.result.current.switchConversation('new', target().history, 1));
    const req = latest().frames.at(-1)!;
    act(() => {
      latest().emit({ type: 'turn.started', turn_id: 'late-source', conversation_id: 'old', generation: 3 });
      latest().emit({ type: 'conversation.switched', request_id: req.request_id, conversation_id: 'new', worldline: 'steins_gate' });
    });
    expect(h.result.current.messages).toEqual(source);
    expect(h.result.current.sessionState).toBe('unknown');
    expect(onConversationSwitched.mock.lastCall?.[0]).toMatchObject({ conversationId: 'new', requestEpoch: 1 });
    act(() => h.result.current.reconnect()); act(() => latest().open()); act(() => latest().emit(ready));
    expect(h.result.current.messages).toEqual(target().history);
    h.unmount();
  });

  it('allows the owner to reject ready before its newer intent has committed', () => {
    let accept = true;
    const h = setup({ onSessionReady: () => accept }); h.stage(); accept = false;
    act(() => latest().emit(ready));
    expect(h.result.current.messages).toEqual(source);
    expect(h.result.current.sessionState).toBe('unknown');
    h.unmount();
  });

  it('ignores a late switch ACK after a newer request replaced it on the same socket', () => {
    const onConversationSwitched = vi.fn();
    const h = setup({ onConversationSwitched });
    onConversationSwitched.mockClear();
    h.rerender({ ...base, onConversationSwitched, pendingConversation: target(1) });
    act(() => h.result.current.switchConversation('new', target().history, 1));
    const first = latest().frames.at(-1)!;
    const next = { ...target(2), conversationId: 'other' };
    h.rerender({ ...base, onConversationSwitched, pendingConversation: next });
    act(() => h.result.current.switchConversation('other', next.history, 2));
    const second = latest().frames.at(-1)!;
    act(() => latest().emit({ type: 'conversation.switched', request_id: first.request_id, conversation_id: 'new' }));
    expect(onConversationSwitched).not.toHaveBeenCalled();
    expect(h.result.current.messages).toEqual(source);
    act(() => latest().emit({ type: 'conversation.switched', request_id: second.request_id, conversation_id: 'other' }));
    expect(onConversationSwitched).toHaveBeenCalledWith(expect.objectContaining({ conversationId: 'other', requestEpoch: 2 }));
    expect(h.result.current.messages).toEqual(source);
    h.unmount();
  });

  it('reports a missing auth target with its local epoch without changing the source transcript', () => {
    const onConversationSwitchError = vi.fn();
    const h = setup({ onConversationSwitchError }); h.stage();
    act(() => latest().emit({ type: 'error', message: '请求的会话不存在或不属于当前世界线。' }));
    expect(onConversationSwitchError).toHaveBeenCalledWith({ conversationId: 'new', requestEpoch: 1, code: 'conversation_not_found' });
    expect(h.result.current.messages).toEqual(source);
    expect(h.result.current.sessionState).toBe('rejected');
    h.unmount();
  });
});
