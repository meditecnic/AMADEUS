import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Workstation } from './Workstation';


const wsSpies = vi.hoisted(() => ({
  reconnect: vi.fn(),
  switchConversation: vi.fn(),
  switchWorldline: vi.fn(),
  useAmadeusWS: vi.fn(),
}));

const audioSpies = vi.hoisted(() => ({
  isPlaying: false,
  init: vi.fn(),
  playBase64: vi.fn(),
  stopAll: vi.fn(),
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
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: audioSpies.isPlaying }),
  audioPlayer: {
    init: (...args: unknown[]) => audioSpies.init(...args),
    playBase64: (...args: unknown[]) => audioSpies.playBase64(...args),
    stopAll: (...args: unknown[]) => audioSpies.stopAll(...args),
  },
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
vi.mock('./SettingsModal', () => ({
  SettingsModal: () => null,
  CHAT_TEMPERATURE_MIN: 0,
  CHAT_TEMPERATURE_MAX: 1.2,
  CHAT_TEMPERATURE_STEP: 0.1,
  CHAT_TEMPERATURE_DEFAULT: 0.0,
  DEFAULT_IDENTITY_MODE: 'okabe',
  IDENTITY_MODE_STORAGE_KEY: 'amadeus_pc_identity_mode',
  SELF_NAME_STORAGE_KEY: 'amadeus_pc_self_name',
  clampChatTemperature: (value: number) => {
    if (!Number.isFinite(value)) return 0.0;
    return Math.max(0, Math.min(1.2, value));
  },
  normalizeIdentityMode: (value: unknown) => (value === 'self' ? 'self' : 'okabe'),
  loadStoredIdentityMode: () => 'okabe' as const,
  persistIdentityMode: () => {},
  loadStoredSelfName: () => {
    try {
      return localStorage.getItem('amadeus_pc_self_name') || '';
    } catch {
      return '';
    }
  },
  persistSelfName: (name: string) => {
    try {
      localStorage.setItem('amadeus_pc_self_name', name);
    } catch {
      /* ignore */
    }
  },
}));
vi.mock('./SoundMixerRail', () => ({ SoundMixerRail: () => null }));
vi.mock('./HistorySidebar', () => ({
  HistorySidebar: (props: {
    conversations: Array<{ id: string; title: string }>;
    onNew?: () => void;
  }) => (
    <div data-testid="history-rows">
      {props.conversations.map((row) => <span key={row.id}>{row.title}</span>)}
      <button type="button" onClick={props.onNew}>新建会话</button>
    </div>
  ),
}));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./HudBadge', () => ({ HudBadge: () => null }));
vi.mock('./DivergenceMeter', () => ({ DivergenceMeter: () => null }));
vi.mock('./CRTOverlay', () => ({ CRTOverlay: () => null }));
vi.mock('./BilingualMessage', () => ({ BilingualMessage: () => null }));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));


describe('Workstation provider runtime changes', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    audioSpies.isPlaying = false;
    audioSpies.init.mockClear();
    audioSpies.playBase64.mockClear();
    audioSpies.stopAll.mockClear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockReset();

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
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  it('shows a visible error when the selected model cannot create a conversation', async () => {
    const initialFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/conversations' && init?.method === 'POST') {
        return Promise.resolve({
          ok: false,
          status: 422,
          json: async () => ({ detail: { code: 'model_unavailable' } }),
        } as Response);
      }
      return initialFetch(input, init);
    }));

    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('当前模型不可用');
  });

  it('keeps a real provider/model selection while the workstation is still a draft', async () => {
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    const providerTrigger = await waitFor(() => {
      const trigger = screen.getByRole('button', { name: '模型供应商' });
      expect(trigger).not.toBeDisabled();
      return trigger;
    });
    fireEvent.click(providerTrigger);
    fireEvent.click(screen.getByRole('option', { name: 'OpenAI-compatible' }));

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

  it('replaces a removed stored Kimi draft selection with DeepSeek', async () => {
    localStorage.setItem('amadeus_pc_provider', 'kimi');
    localStorage.setItem('amadeus_pc_model', 'kimi-k2.6');

    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      const calls = wsSpies.useAmadeusWS.mock.calls;
      const latest = calls[calls.length - 1]?.[0] as {
        providerId?: string;
        model?: string;
      };
      expect(latest.providerId).toBe('deepseek');
      expect(latest.model).toBe('deepseek-v4-flash');
    });
    expect(localStorage.getItem('amadeus_pc_provider')).toBe('deepseek');
    expect(localStorage.getItem('amadeus_pc_model')).toBe('deepseek-v4-flash');
  });

  it('repairs a stored model that does not belong to the stored provider', async () => {
    localStorage.setItem('amadeus_pc_provider', 'custom');
    localStorage.setItem('amadeus_pc_model', 'deepseek-v4-flash');
    const initialFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/providers') {
        const rows = await (await initialFetch(input, init)).json();
        return {
          ok: true,
          status: 200,
          json: async () => rows.map((row: { id: string; default_model: string }) => ({
            ...row,
            catalog: [{ id: row.default_model, display_name: row.default_model, callable: true }],
          })),
        } as Response;
      }
      return initialFetch(input, init);
    }));

    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);

    await waitFor(() => expect(localStorage.getItem('amadeus_pc_model')).toBe('local-model'));
    expect(localStorage.getItem('amadeus_pc_provider')).toBe('custom');
  });

  it('does not let a late old-provider model response overwrite the active catalog', async () => {
    let resolveDeepSeekModels!: (response: Response) => void;
    const deepSeekModels = new Promise<Response>((resolve) => {
      resolveDeepSeekModels = resolve;
    });
    const capability = (id: string) => ({
      id,
      display_name: id,
      lifecycle: 'stable' as const,
      callable: true,
      source: 'manifest' as const,
      thinking_control: null,
    });
    const providers = [
      {
        id: 'deepseek',
        display_name: 'DeepSeek',
        default_model: 'deepseek-v4-flash',
        default_model_capability: capability('deepseek-v4-flash'),
        catalog_version: 1,
        configured: true,
        credential_required: true,
        model_discovery: true,
      },
      {
        id: 'custom',
        display_name: 'OpenAI-compatible',
        default_model: 'custom-model',
        default_model_capability: capability('custom-model'),
        catalog_version: 1,
        configured: true,
        credential_required: false,
        model_discovery: true,
      },
    ];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => providers } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      if (url.startsWith('/api/providers/deepseek/models')) {
        return deepSeekModels;
      }
      if (url.startsWith('/api/providers/custom/models')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ catalog: [capability('custom-model')] }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));

    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const providerTrigger = await waitFor(() => screen.getByRole('button', { name: '模型供应商' }));
    fireEvent.click(providerTrigger);
    fireEvent.click(screen.getByRole('option', { name: 'OpenAI-compatible' }));

    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: '模型' }));
      expect(screen.getByRole('option', { name: 'custom-model' })).toBeInTheDocument();
      // Close menu so a later open re-reads options.
      fireEvent.keyDown(screen.getByRole('listbox', { name: '模型' }), { key: 'Escape' });
    });

    resolveDeepSeekModels({
      ok: true,
      status: 200,
      json: async () => ({ catalog: [capability('deepseek-late')] }),
    } as Response);
    await act(async () => {
      await deepSeekModels;
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole('button', { name: '模型' }));
    expect(screen.getByRole('option', { name: 'custom-model' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'deepseek-late' })).toBeNull();
  });

  it('keeps a disabled custom provider selected instead of auto-switching to DeepSeek', async () => {
    // SettingsModal is mocked null in this suite; assert draft selection + disabled
    // catalog semantics directly from the provider list payload.
    const capability = (id: string, callable = true) => ({
      id,
      display_name: id,
      lifecycle: callable ? 'stable' as const : 'unavailable' as const,
      callable,
      source: 'dynamic' as const,
      thinking_control: null,
    });
    const providersPayload = [
      {
        id: 'deepseek',
        display_name: 'DeepSeek',
        default_model: 'deepseek-v4-flash',
        default_model_capability: capability('deepseek-v4-flash'),
        catalog_version: 1,
        configured: true,
        credential_required: true,
        model_discovery: true,
        source: 'builtin',
        enabled: true,
      },
      {
        id: 'custom:keep-me',
        display_name: 'Keep Me',
        default_model: 'local-keep',
        default_model_capability: capability('local-keep', false),
        catalog_version: 1,
        configured: false,
        credential_required: false,
        model_discovery: false,
        base_url_configurable: true,
        base_url: 'http://127.0.0.1:9/v1',
        source: 'user_config',
        enabled: false,
      },
    ];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => providersPayload } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));

    localStorage.setItem('amadeus_pc_provider', 'custom:keep-me');
    localStorage.setItem('amadeus_pc_model', 'local-keep');
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent(
        'Keep Me（已停用/不可用）',
      );
    });
    // Must not auto-switch draft provider to DeepSeek.
    expect(localStorage.getItem('amadeus_pc_provider')).toBe('custom:keep-me');
    fireEvent.click(screen.getByRole('button', { name: '模型' }));
    expect(screen.getByRole('option', { name: 'local-keep（不可用）' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('keeps the initial workstation in draft mode without activating the selected history row', async () => {
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => expect(wsSpies.useAmadeusWS).toHaveBeenCalled());
    await waitFor(() => expect(wsSpies.useAmadeusWS.mock.calls.length).toBeGreaterThan(1));
    expect(wsSpies.useAmadeusWS.mock.calls.every(([params]) => (
      (params as { conversationId?: string | null }).conversationId == null
    ))).toBe(true);
  });

  it('requests an in-place worldline draft switch when the parent worldline changes', async () => {
    const onWorldlineChange = vi.fn();
    const { rerender } = render(<Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );

    rerender(
      <Workstation worldline="beta" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );

    await waitFor(() => expect(wsSpies.switchWorldline).toHaveBeenCalledWith('beta'));
  });

  it('keeps each worldline’s unsent draft on its own line', async () => {
    const onWorldlineChange = vi.fn();
    const { rerender } = render(<Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
    );
    const box = () => screen.getByLabelText('输入消息') as HTMLTextAreaElement;
    fireEvent.change(box(), { target: { value: 'SG 草稿' } });

    rerender(<Workstation worldline="beta" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />);
    await waitFor(() => expect(box().value).toBe(''));
    fireEvent.change(box(), { target: { value: 'β 草稿' } });

    rerender(<Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />);
    await waitFor(() => expect(box().value).toBe('SG 草稿'));
  });

  it('rolls the parent worldline back after a correlated switch failure without resending', async () => {
    const onWorldlineChange = vi.fn();
    const { rerender } = render(<Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />,
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

    const { queryByText, rerender } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
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
    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
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


function mockStreamingAssistant(overrides: {
  isThinking?: boolean;
  isSynthesizing?: boolean;
  activeTool?: string | null;
  activeNotice?: string | null;
  streaming?: boolean;
} = {}) {
  const {
    isThinking = false,
    isSynthesizing = false,
    activeTool = null,
    activeNotice = null,
    streaming = true,
  } = overrides;
  // Return a concrete value so the module mock's `override || default` path uses it.
  wsSpies.useAmadeusWS.mockReturnValue({
    status: 'READY',
    messages: [{
      id: 'assist_turn',
      role: 'assistant',
      content: streaming ? '' : '完了。',
      translation: streaming ? '' : '完成。',
      streaming,
      segments: [],
    }],
    isThinking,
    isSynthesizing,
    activeTool,
    activeNotice,
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
  });
}


describe('Workstation Q14 activity operation labels', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    audioSpies.isPlaying = false;
    audioSpies.init.mockClear();
    audioSpies.playBase64.mockClear();
    audioSpies.stopAll.mockClear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockReset();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  it('renders Title Case Web Search when activeTool is web_search', async () => {
    mockStreamingAssistant({ isThinking: true, activeTool: 'web_search' });
    const { getByTestId, queryByText } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(getByTestId('activity-operation-label')).toHaveTextContent('Web Search');
    });
    expect(queryByText(/^Thinking/)).toBeNull();
  });

  it('falls back to Thinking when there is no activeTool', async () => {
    mockStreamingAssistant({ isThinking: true, activeTool: null });
    const { getByTestId } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(getByTestId('activity-operation-label')).toHaveTextContent('Thinking');
    });
  });

  it('keeps the synthesizing voice label when synthesizing without thinking', async () => {
    mockStreamingAssistant({ isThinking: false, isSynthesizing: true, activeTool: null });
    const { getAllByText, queryByTestId } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(getAllByText('正在合成语音…').length).toBeGreaterThan(0);
    });
    expect(queryByTestId('activity-operation-label')).toBeNull();
  });

  it('hides the activity label after the turn is no longer streaming', async () => {
    mockStreamingAssistant({
      isThinking: false,
      isSynthesizing: false,
      activeTool: null,
      streaming: false,
    });
    const { queryByTestId, queryAllByText } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(queryByTestId('activity-operation-label')).toBeNull();
      expect(queryAllByText('正在合成语音…')).toHaveLength(0);
    });
  });
});


function mockReadyChat(overrides: {
  isThinking?: boolean;
  isSynthesizing?: boolean;
  sendChat?: ReturnType<typeof vi.fn>;
  stopVoice?: ReturnType<typeof vi.fn>;
} = {}) {
  const sendChat = overrides.sendChat ?? vi.fn();
  const stopVoice = overrides.stopVoice ?? vi.fn();
  wsSpies.useAmadeusWS.mockReturnValue({
    status: 'READY',
    messages: [{
      id: 'assist_done',
      role: 'assistant',
      content: '完了。',
      translation: '完成。',
      streaming: false,
      segments: [],
    }],
    isThinking: overrides.isThinking ?? false,
    isSynthesizing: overrides.isSynthesizing ?? false,
    activeTool: null,
    activeNotice: null,
    emotion: 'neutral',
    sendChat,
    stopVoice,
    cancelTurn: vi.fn(),
    sendDebugTts: vi.fn(),
    updateConfig: vi.fn(),
    reconnect: wsSpies.reconnect,
    switchConversation: wsSpies.switchConversation,
    switchWorldline: wsSpies.switchWorldline,
    wsRef: { current: null },
  });
  return { sendChat, stopVoice };
}


describe('Workstation UX-PLAYBACK-SEND-01 send while voice plays', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    audioSpies.isPlaying = false;
    audioSpies.init.mockClear();
    audioSpies.playBase64.mockClear();
    audioSpies.stopAll.mockClear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockReset();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  it('keeps input and SEND enabled when only previous voice is still playing', async () => {
    audioSpies.isPlaying = true;
    mockReadyChat();
    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    const input = await waitFor(() => {
      const element = container.querySelector<HTMLInputElement>('.chat-input-field');
      expect(element).not.toBeNull();
      return element as HTMLInputElement;
    });
    const send = container.querySelector<HTMLButtonElement>('button[aria-label="发送"]');
    expect(input.disabled).toBe(false);
    expect(send?.disabled).toBe(true); // empty input still blocks send

    fireEvent.change(input, { target: { value: '下一句' } });
    expect(send?.disabled).toBe(false);
  });

  it('stops client playback and voice then sends when submitting while playing', async () => {
    audioSpies.isPlaying = true;
    const order: string[] = [];
    const sendChat = vi.fn(() => { order.push('sendChat'); });
    const stopVoice = vi.fn(() => { order.push('stopVoice'); });
    audioSpies.stopAll.mockImplementation(() => { order.push('stopAll'); });
    mockReadyChat({ sendChat, stopVoice });

    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const input = await waitFor(() => {
      const element = container.querySelector<HTMLInputElement>('.chat-input-field');
      expect(element).not.toBeNull();
      return element as HTMLInputElement;
    });
    fireEvent.change(input, { target: { value: '新回合' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    expect(order).toEqual(['stopAll', 'stopVoice', 'sendChat']);
    expect(sendChat).toHaveBeenCalledWith('新回合');
  });

  it('does not stop voice or send on blank submit while playing', async () => {
    audioSpies.isPlaying = true;
    const { sendChat, stopVoice } = mockReadyChat();
    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const input = await waitFor(() => {
      const element = container.querySelector<HTMLInputElement>('.chat-input-field');
      expect(element).not.toBeNull();
      return element as HTMLInputElement;
    });
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    expect(audioSpies.stopAll).not.toHaveBeenCalled();
    expect(stopVoice).not.toHaveBeenCalled();
    expect(sendChat).not.toHaveBeenCalled();
  });

  it('keeps send disabled while thinking even if voice is not playing', async () => {
    audioSpies.isPlaying = false;
    mockReadyChat({ isThinking: true });
    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const input = await waitFor(() => {
      const element = container.querySelector<HTMLInputElement>('.chat-input-field');
      expect(element).not.toBeNull();
      return element as HTMLInputElement;
    });
    expect(input.disabled).toBe(true);
    fireEvent.change(input, { target: { value: '被挡住' } });
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="发送"]')?.disabled).toBe(true);
  });

  it('keeps send disabled while synthesizing', async () => {
    audioSpies.isPlaying = false;
    mockReadyChat({ isSynthesizing: true });
    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const input = await waitFor(() => {
      const element = container.querySelector<HTMLInputElement>('.chat-input-field');
      expect(element).not.toBeNull();
      return element as HTMLInputElement;
    });
    expect(input.disabled).toBe(true);
    fireEvent.change(input, { target: { value: '合成中' } });
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="发送"]')?.disabled).toBe(true);
  });

  it('sends normally without stop when nothing is playing', async () => {
    audioSpies.isPlaying = false;
    const { sendChat, stopVoice } = mockReadyChat();
    const { container } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const input = await waitFor(() => {
      const element = container.querySelector<HTMLInputElement>('.chat-input-field');
      expect(element).not.toBeNull();
      return element as HTMLInputElement;
    });
    fireEvent.change(input, { target: { value: '普通发送' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    expect(audioSpies.stopAll).not.toHaveBeenCalled();
    expect(stopVoice).not.toHaveBeenCalled();
    expect(sendChat).toHaveBeenCalledWith('普通发送');
  });
});


describe('Workstation UX-ERROR-SURFACE-01 switch vs TTS errors', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    audioSpies.isPlaying = false;
    audioSpies.init.mockClear();
    audioSpies.playBase64.mockClear();
    audioSpies.stopAll.mockClear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockReset();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  function mountWithReadyCallbacks() {
    let params: {
      onConversationSwitchError?: (details: { conversationId: string; code: string }) => void;
      onWorldlineSwitchError?: (details: { worldline: 'steins_gate' | 'beta'; code: string }) => void;
      onAudioDegraded?: (reason: string) => void;
    } = {};
    wsSpies.useAmadeusWS.mockImplementation((p: unknown) => {
      params = p as typeof params;
      return {
        status: 'READY',
        messages: [],
        isThinking: false,
        isSynthesizing: false,
        activeTool: null,
        activeNotice: null,
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
    });
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    return { ...view, getParams: () => params };
  }

  it('shows a Session label without TTS prefix on conversation switch error', async () => {
    const { getByTestId, queryByText, getParams } = mountWithReadyCallbacks();
    await waitFor(() => expect(getParams().onConversationSwitchError).toBeTypeOf('function'));

    act(() => {
      getParams().onConversationSwitchError?.({
        conversationId: 'missing',
        code: 'conversation_not_found',
      });
    });

    await waitFor(() => {
      expect(getByTestId('session-error-toast')).toHaveTextContent('会话同步失败');
      expect(getByTestId('session-error-toast')).toHaveTextContent('conversation_not_found');
    });
    expect(queryByText(/TTS ·/)).toBeNull();
    expect(queryByText(/> TTS /)).toBeNull();
  });

  it('shows a Worldline label without TTS prefix on worldline switch error', async () => {
    const { getByTestId, queryByText, getParams } = mountWithReadyCallbacks();
    await waitFor(() => expect(getParams().onWorldlineSwitchError).toBeTypeOf('function'));

    act(() => {
      getParams().onWorldlineSwitchError?.({
        worldline: 'beta',
        code: 'switch_failed',
      });
    });

    await waitFor(() => {
      expect(getByTestId('session-error-toast')).toHaveTextContent('世界线切换失败');
      expect(getByTestId('session-error-toast')).toHaveTextContent('switch_failed');
    });
    expect(queryByText(/TTS ·/)).toBeNull();
  });

  it('still shows TTS prefix for real audio degradation', async () => {
    const { getByTestId, queryByTestId, getParams } = mountWithReadyCallbacks();
    await waitFor(() => expect(getParams().onAudioDegraded).toBeTypeOf('function'));

    act(() => {
      getParams().onAudioDegraded?.('service_error');
    });

    await waitFor(() => {
      expect(getByTestId('audio-error-toast')).toHaveTextContent('语音服务暂时不可用');
    });
    expect(queryByTestId('session-error-toast')).toBeNull();
  });

  it('does not write switch errors into the TTS audioError surface', async () => {
    const { getByTestId, queryByTestId, getParams } = mountWithReadyCallbacks();
    await waitFor(() => expect(getParams().onAudioDegraded).toBeTypeOf('function'));

    act(() => {
      getParams().onAudioDegraded?.('service_error');
    });
    await waitFor(() => {
      expect(getByTestId('audio-error-toast')).toBeTruthy();
    });

    act(() => {
      getParams().onConversationSwitchError?.({
        conversationId: 'x',
        code: 'conversation_not_found',
      });
    });

    await waitFor(() => {
      expect(getByTestId('session-error-toast')).toHaveTextContent('conversation_not_found');
    });
    // Real TTS error remains; switch error did not replace it.
    expect(getByTestId('audio-error-toast')).toHaveTextContent('语音服务暂时不可用');
    expect(queryByTestId('session-error-toast')).not.toBeNull();
  });
});


type ScrollMetrics = {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
};

function attachScrollMetrics(el: HTMLElement, metrics: ScrollMetrics) {
  Object.defineProperty(el, 'scrollHeight', {
    configurable: true,
    get: () => metrics.scrollHeight,
  });
  Object.defineProperty(el, 'clientHeight', {
    configurable: true,
    get: () => metrics.clientHeight,
  });
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => metrics.scrollTop,
    set: (value: number) => {
      metrics.scrollTop = value;
    },
  });
  el.scrollTo = vi.fn((options?: ScrollToOptions | number) => {
    if (typeof options === 'number') {
      metrics.scrollTop = options;
      return;
    }
    if (options && typeof options.top === 'number') {
      metrics.scrollTop = options.top;
    }
  }) as typeof el.scrollTo;
}

describe('Workstation UX-TRANSCRIPT-FOLLOW-01 conditional stick + jump', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    localStorage.clear();
    audioSpies.isPlaying = false;
    audioSpies.init.mockClear();
    audioSpies.playBase64.mockClear();
    audioSpies.stopAll.mockClear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockReset();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  function mountFollowHarness(initialMessages: Array<Record<string, unknown>>) {
    const state = {
      messages: initialMessages,
      isThinking: false,
    };
    wsSpies.useAmadeusWS.mockImplementation(() => ({
      status: 'READY',
      messages: state.messages,
      isThinking: state.isThinking,
      isSynthesizing: false,
      activeTool: null,
      activeNotice: null,
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
    }));
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    return { ...view, state };
  }

  it('auto-follows new segments when the user is near the bottom', async () => {
    const { getByTestId, state, rerender } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 600 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);

    state.messages = [
      ...state.messages,
      { id: 'm2', role: 'assistant', content: '二段。', streaming: true, segments: [{ id: 0, ja: '二段。', zh: '二段。' }] },
    ];
    metrics.scrollHeight = 1200;
    rerender(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(metrics.scrollTop).toBe(1200);
    });
  });

  it('does not force-follow when the user has scrolled up', async () => {
    const { getByTestId, state, rerender } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 80 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);

    state.messages = [
      ...state.messages,
      { id: 'm2', role: 'assistant', content: '二段。', streaming: true, segments: [] },
    ];
    metrics.scrollHeight = 1200;
    rerender(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(metrics.scrollTop).toBe(80);
    });
  });

  it('resumes auto-follow after the user returns to the bottom', async () => {
    const { getByTestId, state, rerender } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 40 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);

    metrics.scrollTop = 600;
    fireEvent.scroll(viewport);

    state.messages = [
      ...state.messages,
      { id: 'm2', role: 'assistant', content: '二段。', streaming: true, segments: [] },
    ];
    metrics.scrollHeight = 1100;
    rerender(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );

    await waitFor(() => {
      expect(metrics.scrollTop).toBe(1100);
    });
  });

  it('shows the jump button when scrolled away with overflow, and hides at the bottom', async () => {
    const { getByTestId, queryByTestId } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
      { id: 'm2', role: 'assistant', content: '二段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 40 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);

    await waitFor(() => {
      expect(getByTestId('jump-to-latest')).toBeTruthy();
    });

    metrics.scrollTop = 600;
    fireEvent.scroll(viewport);
    await waitFor(() => {
      expect(queryByTestId('jump-to-latest')).toBeNull();
    });
  });

  it('jumps to latest on click and restores follow for later segments', async () => {
    const { getByTestId, state, rerender } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 20 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);
    const jump = await waitFor(() => getByTestId('jump-to-latest'));
    fireEvent.click(jump);

    await waitFor(() => {
      expect(metrics.scrollTop).toBe(900);
    });

    state.messages = [
      ...state.messages,
      { id: 'm2', role: 'assistant', content: '二段。', streaming: true, segments: [] },
    ];
    metrics.scrollHeight = 1300;
    rerender(
      <Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    await waitFor(() => {
      expect(metrics.scrollTop).toBe(1300);
    });
  });

  it('hides jump control for empty transcripts and non-overflowing content', async () => {
    const empty = mountFollowHarness([]);
    await waitFor(() => empty.getByTestId('transcript-viewport'));
    expect(empty.queryByTestId('jump-to-latest')).toBeNull();
    empty.unmount();

    const short = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '短。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => short.getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 200, clientHeight: 300, scrollTop: 0 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);
    expect(short.queryByTestId('jump-to-latest')).toBeNull();
  });

  it('activates jump-to-latest with Enter and Space', async () => {
    const { getByTestId } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 10 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);
    const jump = await waitFor(() => getByTestId('jump-to-latest'));
    expect(jump).toHaveAttribute('aria-label', '跳到最新');

    metrics.scrollTop = 10;
    fireEvent.keyDown(jump, { key: 'Enter' });
    await waitFor(() => expect(metrics.scrollTop).toBe(900));

    metrics.scrollTop = 10;
    fireEvent.scroll(viewport);
    const jumpAgain = await waitFor(() => getByTestId('jump-to-latest'));
    fireEvent.keyDown(jumpAgain, { key: ' ' });
    await waitFor(() => expect(metrics.scrollTop).toBe(900));
  });

  it('uses immediate scroll under reduced motion instead of smooth behavior', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const { getByTestId } = mountFollowHarness([
      { id: 'm1', role: 'assistant', content: '一段。', streaming: false, segments: [] },
    ]);
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 15 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);
    const jump = await waitFor(() => getByTestId('jump-to-latest'));
    fireEvent.click(jump);

    await waitFor(() => {
      expect(metrics.scrollTop).toBe(900);
    });
    const scrollToMock = viewport.scrollTo as unknown as ReturnType<typeof vi.fn>;
    const smoothCalls = scrollToMock.mock.calls.filter((call) => {
      const arg = call[0] as ScrollToOptions | undefined;
      return arg && arg.behavior === 'smooth';
    });
    expect(smoothCalls).toHaveLength(0);
  });

  it('keeps force-scroll on send so thinking area is revealed', async () => {
    const sendChat = vi.fn();
    wsSpies.useAmadeusWS.mockImplementation(() => ({
      status: 'READY',
      messages: [{ id: 'old', role: 'assistant', content: '旧消息', streaming: false, segments: [] }],
      isThinking: false,
      isSynthesizing: false,
      activeTool: null,
      activeNotice: null,
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
    }));
    const { container, getByTestId } = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    const viewport = await waitFor(() => getByTestId('transcript-viewport'));
    const metrics: ScrollMetrics = { scrollHeight: 900, clientHeight: 300, scrollTop: 100 };
    attachScrollMetrics(viewport, metrics);
    fireEvent.scroll(viewport);

    const input = container.querySelector<HTMLInputElement>('.chat-input-field');
    expect(input).not.toBeNull();
    fireEvent.change(input as HTMLInputElement, { target: { value: '新消息' } });
    fireEvent.submit((input as HTMLInputElement).closest('form') as HTMLFormElement);

    expect(sendChat).toHaveBeenCalledWith('新消息');
    expect(metrics.scrollTop).toBe(900);
  });
});


describe('Workstation identity HUD label (backlog issue-2)', () => {
  // Q21: label follows the active conversation's immutable mode; unknown values fall back to okabe.
  const conversationRows = [
    {
      id: 'conv-okabe', title: 'Okabe 会话', title_source: 'auto', is_default: true,
      is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-v4-flash',
      identity_mode: 'okabe', last_active_at: '1',
    },
    {
      id: 'conv-self', title: 'Self 会话', title_source: 'manual', is_default: false,
      is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-v4-flash',
      identity_mode: 'self', last_active_at: '2',
    },
    {
      id: 'conv-missing', title: '缺失 mode 会话', title_source: 'auto', is_default: false,
      is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-v4-flash',
      last_active_at: '3',
    },
    {
      id: 'conv-unknown', title: '未知 mode 会话', title_source: 'auto', is_default: false,
      is_pinned: false, provider_id: 'deepseek', model_id: 'deepseek-v4-flash',
      identity_mode: 'kurisu', last_active_at: '4',
    },
  ];

  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    localStorage.clear();
    audioSpies.isPlaying = false;
    audioSpies.init.mockClear();
    audioSpies.playBase64.mockClear();
    audioSpies.stopAll.mockClear();
    wsSpies.reconnect.mockClear();
    wsSpies.switchConversation.mockClear();
    wsSpies.switchWorldline.mockClear();
    wsSpies.switchConversation.mockReturnValue(true);
    wsSpies.switchWorldline.mockReturnValue(true);
    wsSpies.useAmadeusWS.mockReset();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/providers') {
        return { ok: true, status: 200, json: async () => [] } as Response;
      }
      if (url.startsWith('/api/conversations?')) {
        return { ok: true, status: 200, json: async () => conversationRows } as Response;
      }
      if (url === '/api/search/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ provider: 'tavily', configured: false, fallback: 'duckduckgo' }),
        } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }));
  });

  function mountIdentityHarness() {
    let params: {
      onConversationSwitched?: (details: { conversationId: string }) => void;
    } = {};
    wsSpies.useAmadeusWS.mockImplementation((p: unknown) => {
      params = p as typeof params;
      return {
        status: 'READY',
        messages: [
          { id: 'user_turn', role: 'user', content: '身份标签检查' },
          { id: 'assist_turn', role: 'assistant', content: '完了。', translation: '完成。', streaming: false, segments: [] },
        ],
        isThinking: false,
        isSynthesizing: false,
        activeTool: null,
        activeNotice: null,
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
    });
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />,
    );
    return { ...view, getParams: () => params };
  }

  async function activate(
    view: ReturnType<typeof mountIdentityHarness>,
    conversationId: string,
  ) {
    await waitFor(() => expect(view.getParams().onConversationSwitched).toBeTypeOf('function'));
    act(() => view.getParams().onConversationSwitched?.({ conversationId }));
  }

  it('shows OKABE for an okabe conversation (S4-DS speaker contract)', async () => {
    const view = mountIdentityHarness();
    await activate(view, 'conv-okabe');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('OKABE'));
    expect(view.queryByText(/USER ID/i)).toBeNull();
    expect(view.queryByText('——')).toBeNull();
  });

  it('shows the blank-start placeholder for a self conversation', async () => {
    const view = mountIdentityHarness();
    await activate(view, 'conv-self');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('——'));
    expect(view.queryByText(/USER ID/i)).toBeNull();
    expect(view.queryByText('OKABE')).toBeNull();
  });

  it('falls back to OKABE when identity_mode is missing', async () => {
    const view = mountIdentityHarness();
    await activate(view, 'conv-missing');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('OKABE'));
    expect(view.queryByText('——')).toBeNull();
  });

  it('falls back to OKABE for unknown identity_mode values', async () => {
    const view = mountIdentityHarness();
    await activate(view, 'conv-unknown');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('OKABE'));
    expect(view.queryByText('——')).toBeNull();
  });

  it('updates the label when switching from okabe to self', async () => {
    const view = mountIdentityHarness();
    await activate(view, 'conv-okabe');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('OKABE'));

    await activate(view, 'conv-self');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('——'));
    expect(view.queryByText('OKABE')).toBeNull();
  });

  it('restores the OKABE label when switching back from self', async () => {
    const view = mountIdentityHarness();
    await activate(view, 'conv-self');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('——'));

    await activate(view, 'conv-okabe');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('OKABE'));
    expect(view.queryByText('——')).toBeNull();
  });

  it('shows the stored self name for a self conversation (Slice B)', async () => {
    localStorage.setItem('amadeus_pc_self_name', '阿伟');
    const view = mountIdentityHarness();
    await activate(view, 'conv-self');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('阿伟'));
    expect(view.queryByText('——')).toBeNull();
    expect(view.queryByText('OKABE')).toBeNull();
    expect(view.queryByText(/USER ID/i)).toBeNull();
  });

  it('never shows the self name on an okabe conversation (Slice B isolation)', async () => {
    localStorage.setItem('amadeus_pc_self_name', '阿伟');
    const view = mountIdentityHarness();
    await activate(view, 'conv-okabe');
    await waitFor(() => expect(view.queryByTestId('speaker-user')).toHaveTextContent('OKABE'));
    expect(view.queryByText('阿伟')).toBeNull();
  });

  it('hands the stored self name to the WS config chain on mount (reload retention)', async () => {
    localStorage.setItem('amadeus_pc_self_name', '阿伟');
    mountIdentityHarness();
    await waitFor(() => {
      const calls = wsSpies.useAmadeusWS.mock.calls;
      const latest = calls[calls.length - 1]?.[0] as { selfName?: string };
      expect(latest.selfName).toBe('阿伟');
    });
  });
});
