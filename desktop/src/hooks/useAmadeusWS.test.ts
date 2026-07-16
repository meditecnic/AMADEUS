import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

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

  send(payload: string) { this.sent.push(payload); }
  close() { this.readyState = 3; }
  emit(payload: object) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}


const params: UseAmadeusWSParams = {
  sessionId: 'session-one',
  conversationId: 'conversation-one',
  systemPrompt: 'prompt',
  temperature: 0.8,
  worldline: 'steins_gate',
  enableTts: true,
  sovitsUrl: '',
  model: 'deepseek-v4-flash',
  reasoningEffort: 'high',
};


describe('useAmadeusWS v2', () => {
  afterEach(() => {
    FakeWebSocket.instances = [];
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('negotiates v2, sends chat.send, and ignores late generations', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    const auth = JSON.parse(socket.sent[0]);
    expect(auth).toMatchObject({
      type: 'auth',
      protocol_version: 2,
      conversation_id: 'conversation-one',
    });
    expect(auth).not.toHaveProperty('api_key');

    act(() => result.current.updateConfig({ temperature: 0.5 }));
    expect(JSON.parse(socket.sent[socket.sent.length - 1])).not.toHaveProperty('api_key');

    act(() => result.current.sendChat('hello'));
    expect(JSON.parse(socket.sent[socket.sent.length - 1])).toEqual({ type: 'chat.send', content: 'hello' });

    act(() => {
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 4 });
      socket.emit({ type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 4, segment_id: 1, ja: '二。', zh: '二。', emotion: 'neutral' });
      socket.emit({ type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 4, segment_id: 0, ja: '一。', zh: '一。', emotion: 'neutral' });
      socket.emit({ type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 3, segment_id: 2, ja: '迟到', zh: '迟到', emotion: 'neutral' });
    });

    const assistant = result.current.messages.find((message) => message.turnId === 't');
    expect(assistant?.content).toBe('一。二。');
    expect(assistant?.translation).toBe('一。二。');
    unmount();
  });

  it('sends distinct voice.stop and turn.cancel frames', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];
    act(() => socket.onopen?.());

    act(() => {
      result.current.stopVoice();
      result.current.cancelTurn();
    });

    expect(socket.sent.slice(-2).map((payload) => JSON.parse(payload).type)).toEqual([
      'voice.stop',
      'turn.cancel',
    ]);
    unmount();
  });

  it('does not show synthesis state when TTS is disabled', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, enableTts: false }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('hello');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.emit({
        type: 'segment.ready',
        conversation_id: 'c',
        turn_id: 't',
        generation: 1,
        segment_id: 0,
        ja: '日本語',
        zh: '',
      });
    });

    expect(result.current.isThinking).toBe(false);
    expect(result.current.isSynthesizing).toBe(false);
    unmount();
  });

  it('keeps synthesis stopped when later segments arrive after voice.stop', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('hello');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.emit({
        type: 'segment.ready',
        conversation_id: 'c',
        turn_id: 't',
        generation: 1,
        segment_id: 0,
        ja: '第一句',
        zh: '第一句',
      });
      result.current.stopVoice();
      socket.emit({
        type: 'segment.ready',
        conversation_id: 'c',
        turn_id: 't',
        generation: 1,
        segment_id: 1,
        ja: '第二句',
        zh: '第二句',
      });
    });

    expect(result.current.isSynthesizing).toBe(false);
    unmount();
  });

  it('clears an active stream when the socket closes', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('hello');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.onclose?.();
    });

    expect(result.current.isThinking).toBe(false);
    expect(result.current.isSynthesizing).toBe(false);
    expect(result.current.messages.find((message) => message.turnId === 't')?.streaming).toBe(false);
    unmount();
  });
});
