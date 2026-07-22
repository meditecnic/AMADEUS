import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Workstation } from './Workstation';


const wsSpies = vi.hoisted(() => ({
  reconnect: vi.fn(),
  switchConversation: vi.fn(),
  switchWorldline: vi.fn(),
  useAmadeusWS: vi.fn(),
}));

vi.mock('../hooks/useAmadeusWS', () => ({
  getOrCreateAmadeusSessionId: () => 'session-one',
  useAmadeusWS: (params: unknown) => {
    const override = wsSpies.useAmadeusWS(params);
    return override || {
      status: 'ERROR',
      messages: [],
      isThinking: false,
      isSynthesizing: false,
      emotion: 'neutral',
      sendChat: vi.fn(),
      stopVoice: vi.fn(),
      cancelTurn: vi.fn(),
      sendDebugTts: vi.fn(),
      updateConfig: vi.fn(),
      reconnect: wsSpies.reconnect,
      switchConversation: wsSpies.switchConversation,
      switchWorldline: wsSpies.switchWorldline,
      wsRef: { current: null },
    };
  },
}));

vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: false }),
  audioPlayer: { init: vi.fn(), playBase64: vi.fn(), stopAll: vi.fn() },
}));

vi.mock('../services/AudioService', () => ({
  audioService: {
    setEnableBGM: vi.fn(),
    setBGMVolume: vi.fn(),
    setEnableSFX: vi.fn(),
    setSFXVolume: vi.fn(),
    playBGMForWorldline: vi.fn(),
    stopBGM: vi.fn(),
    playSFX: vi.fn(),
  },
}));

vi.mock('./StatusBar', () => ({ StatusBar: () => null }));
vi.mock('./SettingsModal', () => ({ SettingsModal: () => null }));
vi.mock('./SoundPanel', () => ({ SoundPanel: () => null }));
vi.mock('./HistorySidebar', () => ({
  HistorySidebar: (props: { conversations: Array<{ id: string; title: string }> }) => (
    <div data-testid="history-rows">
      {props.conversations.map((row) => <span key={row.id}>{row.title}</span>)}
    </div>
  ),
}));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./HudBadge', () => ({ HudBadge: () => null }));
vi.mock('./DivergenceMeter', () => ({ DivergenceMeter: () => null }));
vi.mock('./CRTOverlay', () => ({ CRTOverlay: () => null }));
vi.mock('./CircuitTrace', () => ({ CircuitTrace: () => null }));
vi.mock('./BilingualMessage', () => ({ BilingualMessage: () => null }));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));


describe('Workstation provider runtime changes', () => {
  beforeEach(() => {
    localStorage.clear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockClear();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const conversation = {
        id: 'conversation-one',
        title: 'Default Conversation',
        title_source: 'auto',
        is_default: true,
        is_pinned: false,
        is_selected: true,
        provider_id: 'deepseek',
        model_id: 'deepseek-v4-flash',
        last_active_at: '2026-07-16T00:00:00Z',
      };
      const providers = [
        {
          id: 'deepseek', display_name: 'DeepSeek', default_model: 'deepseek-v4-flash',
          configured: false, credential_required: true, model_discovery: true,
        },
        {
          id: 'custom', display_name: 'OpenAI-compatible', default_model: 'local-model',
          configured: true, credential_required: false, model_discovery: true,
        },
      ];

      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => providers } as Response;
      }
      if (url.startsWith('/api/providers/')) {
        return { ok: true, status: 200, json: async () => ({ models: ['local-model'] }) } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [conversation] } as Response;
      }
      if (url.startsWith('/api/conversations/conversation-one') && init?.method === 'PATCH') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ ...conversation, provider_id: 'custom', model_id: 'local-model' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  it('keeps a real provider/model selection while the workstation is still a draft', async () => {
    const { container } = render(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    const providerSelect = await waitFor(() => {
      const select = container.querySelector<HTMLSelectElement>('.provider-select');
      expect(select).not.toBeNull();
      expect(select?.disabled).toBe(false);
      return select as HTMLSelectElement;
    });
    fireEvent.change(providerSelect, { target: { value: 'custom' } });

    await waitFor(() => expect(wsSpies.reconnect).toHaveBeenCalled());
    await waitFor(() => {
      const calls = wsSpies.useAmadeusWS.mock.calls;
      const latest = calls[calls.length - 1]?.[0] as {
        providerId?: string;
        model?: string;
      };
      expect(latest.providerId).toBe('custom');
      expect(latest.model).toBe('local-model');
    });
    expect(wsSpies.switchConversation).not.toHaveBeenCalled();
  });

  it('keeps the initial workstation in draft mode without activating the selected history row', async () => {
    render(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => expect(wsSpies.useAmadeusWS).toHaveBeenCalled());
    await waitFor(() => expect(wsSpies.useAmadeusWS.mock.calls.length).toBeGreaterThan(1));
    expect(wsSpies.useAmadeusWS.mock.calls.every(([params]) => (
      (params as { conversationId?: string | null }).conversationId == null
    ))).toBe(true);
  });

  it('requests an in-place worldline draft switch when the parent worldline changes', async () => {
    const onWorldlineChange = vi.fn();
    const { rerender } = render(
      <Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );

    rerender(
      <Workstation worldline="beta" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );

    await waitFor(() => expect(wsSpies.switchWorldline).toHaveBeenCalledWith('beta'));
  });

  it('rolls the parent worldline back after a correlated switch failure without resending', async () => {
    const onWorldlineChange = vi.fn();
    const { rerender } = render(
      <Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );
    rerender(
      <Workstation worldline="beta" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );
    await waitFor(() => expect(wsSpies.switchWorldline).toHaveBeenCalledTimes(1));
    const calls = wsSpies.useAmadeusWS.mock.calls;
    const latest = calls[calls.length - 1]?.[0] as {
      onWorldlineSwitchError?: (details: { worldline: 'beta'; code: string }) => void;
    };

    act(() => latest.onWorldlineSwitchError?.({ worldline: 'beta', code: 'switch_failed' }));
    expect(onWorldlineChange).toHaveBeenCalledWith('steins_gate');

    rerender(
      <Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );
    expect(wsSpies.switchWorldline).toHaveBeenCalledTimes(1);
  });

  it('does not let a late old-worldline conversation response overwrite the new list', async () => {
    let resolveSg!: (value: Response) => void;
    let resolveBeta!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return Promise.resolve({ ok: true, json: async () => [] } as Response);
      }
      if (url.includes('/api/conversations?') && url.includes('worldline=steins_gate')) {
        return new Promise<Response>((resolve) => { resolveSg = resolve; });
      }
      if (url.includes('/api/conversations?') && url.includes('worldline=beta')) {
        return new Promise<Response>((resolve) => { resolveBeta = resolve; });
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));

    const { queryByText, rerender } = render(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    await waitFor(() => expect(resolveSg).toBeTypeOf('function'));
    rerender(
      <Workstation worldline="beta" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    await waitFor(() => expect(resolveBeta).toBeTypeOf('function'));

    await act(async () => {
      resolveBeta({
        ok: true,
        json: async () => [{ id: 'beta-1', title: 'Beta History' }],
      } as Response);
    });
    await waitFor(() => expect(queryByText('Beta History')).not.toBeNull());

    await act(async () => {
      resolveSg({
        ok: true,
        json: async () => [{ id: 'sg-1', title: 'SG History' }],
      } as Response);
    });
    expect(queryByText('Beta History')).not.toBeNull();
    expect(queryByText('SG History')).toBeNull();
  });

  it('scrolls the transcript to the latest thinking area when sending from history', async () => {
    const sendChat = vi.fn();
    wsSpies.useAmadeusWS.mockImplementation((_params: unknown) => {
      return {
        status: 'READY',
        messages: [{ id: 'old', role: 'assistant', content: '旧消息' }],
        isThinking: false,
        isSynthesizing: false,
        emotion: 'neutral',
        sendChat,
        stopVoice: vi.fn(),
        cancelTurn: vi.fn(),
        sendDebugTts: vi.fn(),
        updateConfig: vi.fn(),
        reconnect: wsSpies.reconnect,
        switchConversation: wsSpies.switchConversation,
        switchWorldline: wsSpies.switchWorldline,
        wsRef: { current: null },
      };
    });
    const { container } = render(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const viewport = await waitFor(() => {
      const element = container.querySelector<HTMLElement>('.terminal-chat-viewport');
      expect(element).not.toBeNull();
      return element as HTMLElement;
    });
    Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: 900 });
    viewport.scrollTop = 100;

    const input = container.querySelector<HTMLInputElement>('.chat-input-field');
    expect(input).not.toBeNull();
    expect(input?.disabled).toBe(false);
    fireEvent.change(input as HTMLInputElement, { target: { value: '新消息' } });
    const form = input?.closest('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    expect(sendChat).toHaveBeenCalledWith('新消息');
    expect(viewport.scrollTop).toBe(900);
  });
});
