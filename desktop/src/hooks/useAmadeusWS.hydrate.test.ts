import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { resolveTruncateTarget } from '../workstation/MessageMenu';

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.OPEN;
  sent: Array<Record<string, unknown>> = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string, public protocols?: string[]) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
  }
  receive(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

describe('useAmadeusWS message ID hydration & rejection safety (Item 2 & Item 5)', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    FakeSocket.instances = [];
  });

  it('binds dbIds strictly by turn_id after a rejected optimistic send and disables unconfirmed bubbles', async () => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ version: 1, http_origin: '', ws_origin: '', token: '' }),
    }));
    vi.stubGlobal('WebSocket', FakeSocket);
    const { initializeLocalBackend } = await import('../apiBase');
    await initializeLocalBackend();
    const onSendRejected = vi.fn();
    const onTurnSettled = vi.fn();
    const { useAmadeusWS } = await import('./useAmadeusWS');

    const hook = renderHook(() => useAmadeusWS({
      sessionId: 'hydrate-1',
      conversationId: 'conv-1',
      systemPrompt: '',
      worldline: 'steins_gate',
      enableTts: false,
      sovitsUrl: '',
      model: 'mock',
      reasoningEffort: null,
      onSendRejected,
      onTurnSettled,
    }));

    const ws = FakeSocket.instances[0];
    act(() => {
      ws.onopen?.();
      ws.receive({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'conv-1', worldline: 'steins_gate', generation: 1 });
    });

    // 1) Optimistic send rejected by backend before turn.started (B's exact probe).
    act(() => {
      expect(hook.result.current.sendChat('rejected-before-turn')).toBe(true);
    });
    act(() => {
      ws.receive({ type: 'error', code: 'message_too_long', recoverable: true, message: 'too long' });
    });
    expect(onSendRejected).toHaveBeenCalledWith('message_too_long');

    // 2) Turn 1 succeeds.
    act(() => {
      expect(hook.result.current.sendChat('turn-1-user')).toBe(true);
    });
    act(() => {
      ws.receive({ type: 'turn.started', turn_id: 't1', generation: 1, conversation_id: 'conv-1' });
      ws.receive({ type: 'segment.ready', turn_id: 't1', generation: 1, segment_id: 0, ja: '一回目よ。', zh: '第一轮。', emotion: 'neutral' });
      ws.receive({ type: 'turn.completed', turn_id: 't1', generation: 1, conversation_id: 'conv-1' });
    });
    act(() => {
      hook.result.current.hydrateMessageIds?.([
        { id: 2, role: 'user', content: 'turn-1-user', turn_id: 't1' },
        { id: 3, role: 'assistant', content: '[EMO:neutral] 一回目よ。', turn_id: 't1' },
      ]);
    });

    // 3) Turn 2 succeeds.
    act(() => {
      expect(hook.result.current.sendChat('turn-2-user')).toBe(true);
    });
    act(() => {
      ws.receive({ type: 'turn.started', turn_id: 't2', generation: 1, conversation_id: 'conv-1' });
      ws.receive({ type: 'segment.ready', turn_id: 't2', generation: 1, segment_id: 0, ja: '二回目よ。', zh: '第二轮。', emotion: 'neutral' });
      ws.receive({ type: 'turn.completed', turn_id: 't2', generation: 1, conversation_id: 'conv-1' });
    });
    act(() => {
      hook.result.current.hydrateMessageIds?.([
        { id: 2, role: 'user', content: 'turn-1-user', turn_id: 't1' },
        { id: 3, role: 'assistant', content: '[EMO:neutral] 一回目よ。', turn_id: 't1' },
        { id: 4, role: 'user', content: 'turn-2-user', turn_id: 't2' },
        { id: 5, role: 'assistant', content: '[EMO:neutral] 二回目よ。', turn_id: 't2' },
      ]);
    });

    const msgs = hook.result.current.messages;
    const rejectedBubble = msgs.find((m) => m.content === 'rejected-before-turn')!;
    const turn1User = msgs.find((m) => m.content === 'turn-1-user')!;
    const turn2User = msgs.find((m) => m.content === 'turn-2-user')!;
    expect(rejectedBubble.dbId).toBeUndefined();
    expect(turn1User.dbId).toBe(2);
    expect(turn2User.dbId).toBe(4);
    // Unconfirmed rejected bubble has both edit and rollback disabled.
    expect(resolveTruncateTarget(msgs, rejectedBubble.id, 'edit')).toBeNull();
    expect(resolveTruncateTarget(msgs, rejectedBubble.id, 'rollback')).toBeNull();
    // Confirmed turn 1 edit targets ID 2 with expectedLastMessageId 5 (zero shift!).
    expect(resolveTruncateTarget(msgs, turn1User.id, 'edit')).toMatchObject({
      mode: 'edit',
      fromMessageId: 2,
      expectedLastMessageId: 5,
      deletedTurns: 2,
    });

    // 4) Turn 3 cancelled -> status idle/cancelled fires onTurnSettled and binds dbId via turn_id.
    act(() => {
      expect(hook.result.current.sendChat('turn-3-cancelled')).toBe(true);
    });
    act(() => {
      ws.receive({ type: 'turn.started', turn_id: 't3', generation: 1, conversation_id: 'conv-1' });
      hook.result.current.cancelTurn();
      ws.receive({ type: 'status', dsk: 'idle', state: 'cancelled', tts: 'idle' });
    });
    expect(onTurnSettled).toHaveBeenCalledWith('cancelled');
    act(() => {
      hook.result.current.hydrateMessageIds?.([
        { id: 2, role: 'user', content: 'turn-1-user', turn_id: 't1' },
        { id: 3, role: 'assistant', content: '[EMO:neutral] 一回目よ。', turn_id: 't1' },
        { id: 4, role: 'user', content: 'turn-2-user', turn_id: 't2' },
        { id: 5, role: 'assistant', content: '[EMO:neutral] 二回目よ。', turn_id: 't2' },
        { id: 6, role: 'user', content: 'turn-3-cancelled', turn_id: 't3' },
        { id: 7, role: 'assistant', content: '[EMO:disappointed] 中断しました。', turn_id: 't3' },
      ]);
    });
    const turn3User = hook.result.current.messages.find((m) => m.content === 'turn-3-cancelled')!;
    expect(turn3User.dbId).toBe(6);
    expect(resolveTruncateTarget(hook.result.current.messages, turn3User.id, 'edit')).toMatchObject({
      fromMessageId: 6,
      expectedLastMessageId: 7,
      deletedTurns: 1,
    });
    hook.unmount();
  });
});
