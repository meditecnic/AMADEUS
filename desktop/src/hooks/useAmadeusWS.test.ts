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
  providerId: 'deepseek',
  model: 'deepseek-v4-flash',
  reasoningEffort: 'high',
};


describe('useAmadeusWS v2', () => {
  afterEach(() => {
    FakeWebSocket.instances = [];
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('authenticates a fresh workstation in draft mode without activating history', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, conversationId: null }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());

    const auth = JSON.parse(socket.sent[0]);
    expect(auth).toMatchObject({
      type: 'auth',
      protocol_version: 2,
      conversation_mode: 'draft',
      provider_id: 'deepseek',
      model: 'deepseek-v4-flash',
    });
    expect(auth).not.toHaveProperty('conversation_id');
    expect(result.current.messages).toEqual([]);
    unmount();
  });

  it('treats a session as ready only after session.ready, and rejected when auth is answered with an error', () => {
    // An open socket is not proof of a usable session.
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, conversationId: null }));
    const socket = FakeWebSocket.instances[0];
    act(() => socket.onopen?.());
    expect(result.current.status).toBe('READY');
    expect(result.current.sessionState).toBe('unknown');
    act(() => socket.emit({ type: 'error', code: 'model_unavailable', message: 'model unavailable' }));
    expect(result.current.sessionState).toBe('rejected');
    unmount();

    FakeWebSocket.instances = [];
    const second = renderHook(() => useAmadeusWS({ ...params, conversationId: null }));
    const socket2 = FakeWebSocket.instances[0];
    act(() => socket2.onopen?.());
    act(() => socket2.emit({ type: 'status', notice: null }));
    expect(second.result.current.sessionState).toBe('unknown');
    act(() => socket2.emit({ type: 'session.ready', conversation_mode: 'draft', conversation_id: null, worldline: 'steins_gate', generation: 1 }));
    expect(second.result.current.sessionState).toBe('ready');
    act(() => socket2.emit({ type: 'error', message: 'later turn failure' }));
    expect(second.result.current.sessionState).toBe('ready');
    second.unmount();
  });

  it('forwards backend-catalog control values without a two-value frontend enum', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      providerId: 'openai',
      model: 'gpt-5.6-luna',
      reasoningEffort: 'max',
      conversationId: null,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    expect(JSON.parse(socket.sent[0])).toMatchObject({
      provider_id: 'openai',
      model: 'gpt-5.6-luna',
      reasoning_effort: 'max',
    });

    act(() => result.current.updateConfig({ reasoningEffort: 'none' }));
    expect(JSON.parse(socket.sent[socket.sent.length - 1])).toMatchObject({
      type: 'config_update',
      reasoning_effort: 'none',
    });
    unmount();
  });

  it('forwards config_applied reasoning_effort to onConfigApplied', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onConfigApplied = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onConfigApplied,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.updateConfig({ reasoningEffort: 'xhigh' });
      socket.emit({
        type: 'config_applied',
        reasoning_effort: 'max',
      });
    });

    expect(onConfigApplied).toHaveBeenCalledWith({ reasoningEffort: 'max' });
    unmount();
  });

  it('discards late config_applied after provider context changes', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onConfigApplied = vi.fn();
    const { result, rerender, unmount } = renderHook(
      (hookParams: UseAmadeusWSParams) => useAmadeusWS(hookParams),
      { initialProps: { ...params, onConfigApplied } },
    );
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.updateConfig({ reasoningEffort: 'max' });
    });

    rerender({
      ...params,
      onConfigApplied,
      providerId: 'openai',
      model: 'gpt-5.6-luna',
    });

    act(() => {
      socket.emit({
        type: 'config_applied',
        reasoning_effort: 'max',
      });
    });

    expect(onConfigApplied).not.toHaveBeenCalled();
    unmount();
  });

  it('rejects stale config_applied after switching away and back to the original context', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onConfigApplied = vi.fn();
    const { result, rerender, unmount } = renderHook(
      (hookParams: UseAmadeusWSParams) => useAmadeusWS(hookParams),
      { initialProps: { ...params, onConfigApplied } },
    );
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.updateConfig({ reasoningEffort: 'max' });
    });

    // Switch away — invalidates pending epoch even if we later restore context.
    rerender({
      ...params,
      onConfigApplied,
      providerId: 'openai',
      model: 'gpt-5.6-luna',
    });
    // Switch back to the original provider/model/conversation.
    rerender({ ...params, onConfigApplied });

    act(() => {
      socket.emit({
        type: 'config_applied',
        reasoning_effort: 'max',
      });
    });

    expect(onConfigApplied).not.toHaveBeenCalled();
    unmount();
  });

  it('consumes config_applied only once for duplicate frames', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onConfigApplied = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onConfigApplied,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.updateConfig({ reasoningEffort: 'xhigh' });
      socket.emit({ type: 'config_applied', reasoning_effort: 'max' });
      socket.emit({ type: 'config_applied', reasoning_effort: 'max' });
    });

    expect(onConfigApplied).toHaveBeenCalledTimes(1);
    expect(onConfigApplied).toHaveBeenCalledWith({ reasoningEffort: 'max' });
    unmount();
  });

  it('applies conversation history only after the matching switch ACK', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, conversationId: null }));
    const socket = FakeWebSocket.instances[0];
    const history = [{ id: 'history-1', role: 'assistant' as const, content: '历史消息', translation: 'history' }];

    act(() => {
      socket.onopen?.();
      result.current.switchConversation('conversation-two', history);
    });

    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(request).toMatchObject({
      type: 'conversation.switch',
      conversation_id: 'conversation-two',
    });
    expect(request.request_id).toEqual(expect.any(String));
    expect(result.current.messages).toEqual([]);

    act(() => {
      socket.emit({
        type: 'conversation.switched',
        request_id: request.request_id,
        conversation_id: 'conversation-two',
        worldline: 'steins_gate',
        generation: 8,
      });
    });

    expect(result.current.messages).toEqual(history);
    unmount();
  });

  it('keeps the current transcript and reports a correlated conversation switch failure', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onConversationSwitchError = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onConversationSwitchError,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('仍在当前会话');
      result.current.switchConversation('missing-conversation', []);
    });
    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(result.current.messages).toHaveLength(1);

    act(() => socket.emit({
      type: 'conversation.switch_error',
      request_id: request.request_id,
      code: 'conversation_not_found',
      message: 'not found',
    }));

    expect(result.current.messages[0].content).toBe('仍在当前会话');
    expect(onConversationSwitchError).toHaveBeenCalledWith({
      conversationId: 'missing-conversation',
      code: 'conversation_not_found',
      message: 'not found',
    });
    unmount();
  });

  it('fails a switch immediately when the socket is not ready', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onWorldlineSwitchError = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onWorldlineSwitchError,
    }));
    const socket = FakeWebSocket.instances[0];
    socket.readyState = 3;

    let started = true;
    act(() => { started = result.current.switchWorldline('beta'); });

    expect(started).toBe(false);
    expect(onWorldlineSwitchError).toHaveBeenCalledWith({
      worldline: 'beta',
      code: 'socket_not_ready',
    });
    unmount();
  });

  it('commits a switch locally when auth was rejected, since the server has no session to switch', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onWorldlineSwitched = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, onWorldlineSwitched }));
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
      socket.emit({ type: 'error', message: '缺失 deepseek API Key。' });
    });
    const sentBefore = socket.sent.length;

    let started = false;
    act(() => { started = result.current.switchWorldline('beta'); });

    expect(started).toBe(true);
    expect(socket.sent.length).toBe(sentBefore);
    expect(onWorldlineSwitched).toHaveBeenCalledWith({ worldline: 'beta' });
    expect(result.current.messages).toEqual([]);
    unmount();
  });

  it('switches worldline in place, invalidates old output, and waits for ACK', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('hello');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 4 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 4,
        segment_id: 0, ja: '旧世界线', zh: 'old', emotion: 'neutral',
      });
      result.current.switchWorldline('beta');
    });

    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(request).toMatchObject({
      type: 'worldline.switch',
      request_id: expect.any(String),
      target_worldline: 'beta',
      conversation_mode: 'draft',
    });
    expect(result.current.messages).toEqual([]);

    act(() => {
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 4,
        segment_id: 1, ja: '迟到', zh: 'late', emotion: 'neutral',
      });
    });
    expect(result.current.messages).toEqual([]);
    unmount();
  });

  it('restores the pre-switch transcript after a correlated worldline switch error', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onWorldlineSwitchError = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onWorldlineSwitchError,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('保留这句');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 2 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 2,
        segment_id: 0, ja: '旧世界线。', zh: '旧世界线。', emotion: 'neutral',
      });
    });
    const beforeSwitch = result.current.messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
    }));
    expect(beforeSwitch.length).toBeGreaterThan(0);

    act(() => {
      result.current.switchWorldline('beta');
    });
    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(result.current.messages).toEqual([]);

    act(() => {
      socket.emit({
        type: 'worldline.switch_error',
        request_id: request.request_id,
        code: 'switch_failed',
        message: 'target unavailable',
      });
    });

    expect(result.current.messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
    }))).toEqual(beforeSwitch);
    expect(onWorldlineSwitchError).toHaveBeenCalledWith({
      worldline: 'beta',
      code: 'switch_failed',
      message: 'target unavailable',
    });
    unmount();
  });

  it('keeps an empty draft after a successful worldline ACK without restoring the snapshot', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onWorldlineSwitched = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onWorldlineSwitched,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('不应恢复');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 1 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't', generation: 1,
        segment_id: 0, ja: 'SG。', zh: 'SG。', emotion: 'neutral',
      });
      result.current.switchWorldline('beta');
    });
    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(result.current.messages).toEqual([]);

    act(() => {
      socket.emit({
        type: 'worldline.switched',
        request_id: request.request_id,
        worldline: 'beta',
        generation: 9,
      });
    });

    expect(result.current.messages).toEqual([]);
    expect(onWorldlineSwitched).toHaveBeenCalledWith({
      worldline: 'beta',
      generation: 9,
    });
    unmount();
  });

  it('ignores a late worldline switch_error from an older request id', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onWorldlineSwitchError = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      onWorldlineSwitchError,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('第一批');
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't1', generation: 1 });
      socket.emit({
        type: 'segment.ready', conversation_id: 'c', turn_id: 't1', generation: 1,
        segment_id: 0, ja: '第一批。', zh: '第一批。', emotion: 'neutral',
      });
      result.current.switchWorldline('beta');
    });
    const firstRequest = JSON.parse(socket.sent[socket.sent.length - 1]);

    act(() => {
      result.current.switchWorldline('steins_gate');
    });
    const secondRequest = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(secondRequest.request_id).not.toBe(firstRequest.request_id);
    expect(result.current.messages).toEqual([]);

    act(() => {
      socket.emit({
        type: 'worldline.switch_error',
        request_id: firstRequest.request_id,
        code: 'stale_switch',
        message: 'late old error',
      });
    });

    expect(result.current.messages).toEqual([]);
    expect(onWorldlineSwitchError).not.toHaveBeenCalled();

    act(() => {
      socket.emit({
        type: 'worldline.switched',
        request_id: secondRequest.request_id,
        worldline: 'steins_gate',
        generation: 3,
      });
    });
    expect(result.current.messages).toEqual([]);
    unmount();
  });

  it('reports the materialized conversation from turn.started', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onTurnStarted = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      conversationId: null,
      onTurnStarted,
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('第一次对话');
      socket.emit({ type: 'turn.started', conversation_id: 'materialized-conversation', turn_id: 't', generation: 1 });
    });

    expect(onTurnStarted).toHaveBeenCalledWith('materialized-conversation');
    unmount();
  });

  it('carries self_name on auth and maps it in config_update (Slice B)', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS({
      ...params,
      selfName: '阿伟',
    }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket.onopen?.());
    const auth = JSON.parse(socket.sent[0]);
    expect(auth.self_name).toBe('阿伟');

    act(() => result.current.updateConfig({ selfName: '新名字' }));
    const frame = JSON.parse(socket.sent[socket.sent.length - 1]);
    expect(frame).toMatchObject({ type: 'config_update', self_name: '新名字' });

    act(() => result.current.updateConfig({ selfName: '' }));
    expect(JSON.parse(socket.sent[socket.sent.length - 1]).self_name).toBe('');
    unmount();
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

  it('drops late segment.audio for the same turn after stopVoice', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onAudioChunk = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, onAudioChunk }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 4 });
      socket.emit({
        type: 'segment.audio',
        conversation_id: 'c',
        turn_id: 't',
        generation: 4,
        segment_id: 0,
        data: 'YXVkaW8tYmVmb3Jl',
      });
      result.current.stopVoice();
      socket.emit({
        type: 'segment.audio',
        conversation_id: 'c',
        turn_id: 't',
        generation: 4,
        segment_id: 1,
        data: 'YXVkaW8tbGF0ZQ==',
      });
    });

    expect(onAudioChunk).toHaveBeenCalledTimes(1);
    expect(onAudioChunk).toHaveBeenCalledWith(0, 'YXVkaW8tYmVmb3Jl');
    expect(onAudioChunk).not.toHaveBeenCalledWith(1, 'YXVkaW8tbGF0ZQ==');
    unmount();
  });

  it('accepts segment.audio again after a new turn starts following stopVoice', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const onAudioChunk = vi.fn();
    const { result, unmount } = renderHook(() => useAmadeusWS({ ...params, onAudioChunk }));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't1', generation: 1 });
      result.current.stopVoice();
      socket.emit({
        type: 'segment.audio',
        conversation_id: 'c',
        turn_id: 't1',
        generation: 1,
        segment_id: 0,
        data: 'bGF0ZS1vbGQ=',
      });
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't2', generation: 2 });
      socket.emit({
        type: 'segment.audio',
        conversation_id: 'c',
        turn_id: 't2',
        generation: 2,
        segment_id: 0,
        data: 'bmV3LXR1cm4=',
      });
    });

    expect(onAudioChunk).toHaveBeenCalledTimes(1);
    expect(onAudioChunk).toHaveBeenCalledWith(0, 'bmV3LXR1cm4=');
    expect(onAudioChunk).not.toHaveBeenCalledWith(0, 'bGF0ZS1vbGQ=');
    unmount();
  });

  it('applies a whole-turn translation repair only to the active generation', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      socket.emit({ type: 'turn.started', conversation_id: 'c', turn_id: 't', generation: 4 });
      socket.emit({
        type: 'segment.ready',
        conversation_id: 'c',
        turn_id: 't',
        generation: 4,
        segment_id: 0,
        ja: '全文。',
        zh: '',
        translation_error: 'translation_timeout',
      });
      socket.emit({
        type: 'turn.translation_repaired',
        conversation_id: 'c',
        turn_id: 't',
        generation: 3,
        content: '迟到译文。',
      });
      socket.emit({
        type: 'turn.translation_repaired',
        conversation_id: 'c',
        turn_id: 't',
        generation: 4,
        content: '完整译文。',
      });
    });

    const assistant = result.current.messages.find((message) => message.turnId === 't');
    expect(assistant?.translation).toBe('完整译文。');
    expect(assistant).toMatchObject({ translationScope: 'turn' });
    expect(assistant?.segments?.[0].zh).toBe('');
    expect(assistant?.segments?.[0].translationError).toBeUndefined();
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

  it('keeps the socket ready after a recoverable turn error', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const { result, unmount } = renderHook(() => useAmadeusWS(params));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onopen?.();
      result.current.sendChat('search the web');
      socket.emit({
        type: 'error',
        code: 'turn_failed',
        recoverable: true,
        message: 'DeepSeek request was rejected',
      });
    });

    expect(result.current.status).toBe('READY');
    expect(result.current.messages[result.current.messages.length - 1]?.content).toContain('DeepSeek request was rejected');

    act(() => result.current.sendChat('try another turn'));
    expect(JSON.parse(socket.sent[socket.sent.length - 1] || '{}')).toMatchObject({
      type: 'chat.send',
      content: 'try another turn',
    });
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
