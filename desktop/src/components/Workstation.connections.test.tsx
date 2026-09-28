import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Workstation } from './Workstation';

/* Failure modes guarded here (slice 3):
 * 1. A newly created connection whose first chat-check fails must stay out of the chat selector
 *    and must not replace the current selection.
 * 2. A passing check admits and selects it.
 * 3. A model chosen mid-turn whose update fails after the turn must say so and keep the old model. */

const hoisted = vi.hoisted(() => ({
  reconnect: vi.fn(),
  useAmadeusWS: vi.fn(),
  settingsProps: { current: null as null | Record<string, unknown> },
}));

vi.mock('../hooks/useAmadeusWS', () => ({
  getOrCreateAmadeusSessionId: () => 'session-one',
  useAmadeusWS: (params: unknown) => hoisted.useAmadeusWS(params),
}));
vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: false }),
  audioPlayer: { init: vi.fn(), playBase64: vi.fn(), stopAll: vi.fn() },
}));
vi.mock('../services/AudioService', () => ({
  audioService: {
    setEnableBGM: vi.fn(), setBGMVolume: vi.fn(), setEnableSFX: vi.fn(), setSFXVolume: vi.fn(),
    playBGMForWorldline: vi.fn(), stopBGM: vi.fn(), playSFX: vi.fn(),
  },
}));
vi.mock('./SettingsModal', () => ({
  SettingsModal: (props: Record<string, unknown>) => { hoisted.settingsProps.current = props; return null; },
  CHAT_TEMPERATURE_MIN: 0,
  CHAT_TEMPERATURE_MAX: 1.2,
  CHAT_TEMPERATURE_STEP: 0.1,
  CHAT_TEMPERATURE_DEFAULT: 0.0,
  DEFAULT_IDENTITY_MODE: 'okabe',
  clampChatTemperature: (value: number) => (Number.isFinite(value) ? Math.max(0, Math.min(1.2, value)) : 0),
  normalizeIdentityMode: (value: unknown) => (value === 'self' ? 'self' : 'okabe'),
  loadStoredIdentityMode: () => 'okabe' as const,
  persistIdentityMode: () => {},
  loadStoredSelfName: () => '',
  persistSelfName: () => {},
}));
vi.mock('./SoundMixerRail', () => ({ SoundMixerRail: () => null }));
vi.mock('./HistorySidebar', () => ({ HistorySidebar: () => null }));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./BilingualMessage', () => ({ BilingualMessage: () => null }));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));
vi.mock('../memory-space/MemorySpace', () => ({ MemorySpace: () => null }));

const capability = (id: string) => ({
  id, display_name: id, lifecycle: 'stable' as const, callable: true, source: 'manifest' as const, thinking_control: null,
});
const provider = (id: string, name: string, model: string, extra: Record<string, unknown> = {}) => ({
  id, display_name: name, default_model: model, default_model_capability: capability(model),
  catalog_version: 1, configured: true, credential_required: false, model_discovery: true, enabled: true, ...extra,
});

let providers: ReturnType<typeof provider>[];
let chatCheck: () => Response;
let patchResponse: () => Response;
const json = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body } as Response);

function wsState(overrides: Record<string, unknown> = {}) {
  return {
    status: 'READY', messages: [], isThinking: false, isSynthesizing: false, emotion: 'neutral',
    sendChat: vi.fn(), stopVoice: vi.fn(), cancelTurn: vi.fn(), sendDebugTts: vi.fn(), updateConfig: vi.fn(),
    reconnect: hoisted.reconnect, switchConversation: vi.fn(() => true), switchWorldline: vi.fn(() => true),
    wsRef: { current: null }, ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('amadeus_pc_model', 'deepseek-v4-flash');
  hoisted.reconnect.mockClear();
  hoisted.settingsProps.current = null;
  hoisted.useAmadeusWS.mockReset();
  hoisted.useAmadeusWS.mockImplementation(() => wsState());
  providers = [provider('deepseek', 'DeepSeek', 'deepseek-v4-flash', { credential_required: true })];
  chatCheck = () => json(200, { ok: true, provider_id: 'custom:new', model_id: 'm1' });
  patchResponse = () => json(500, {});
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/providers') return json(200, providers);
    if (url === '/api/providers/custom-profiles' && init?.method === 'POST') {
      providers = [...providers, provider('custom:new', 'New One', 'm1', { source: 'user_config' })];
      return json(200, { provider_id: 'custom:new', default_model: 'm1', enabled: true });
    }
    if (url.endsWith('/chat-check')) return chatCheck();
    if (url.startsWith('/api/providers/deepseek/models')) {
      return json(200, { catalog: [capability('deepseek-v4-flash'), capability('deepseek-v4-pro')] });
    }
    if (decodeURIComponent(url).startsWith('/api/providers/custom:new/models')) return json(200, { catalog: [capability('m1')] });
    if (url.startsWith('/api/conversations?')) {
      return json(200, [{
        id: 'conversation-one', title: 'One', title_source: 'auto', is_default: false, is_pinned: false,
        provider_id: 'deepseek', model_id: 'deepseek-v4-flash', last_active_at: '2026-09-28T00:00:00Z',
      }]);
    }
    if (url.startsWith('/api/conversations/conversation-one') && init?.method === 'PATCH') return patchResponse();
    if (url === '/api/search/settings') return json(200, { provider: 'tavily', configured: false });
    throw new Error(`unexpected fetch: ${url}`);
  }));
});

afterEach(() => cleanup());

async function createConnection() {
  await waitFor(() => expect(hoisted.settingsProps.current?.onCreateCustomProfile).toBeTypeOf('function'));
  const create = hoisted.settingsProps.current!.onCreateCustomProfile as (input: unknown) => Promise<void>;
  await act(async () => {
    await create({ display_name: 'New One', base_url: 'http://127.0.0.1:9/v1', default_model: 'm1' });
  });
}

function providerOptions() {
  fireEvent.click(screen.getByRole('button', { name: '模型供应商' }));
  const names = screen.queryAllByRole('option').map((option) => option.textContent || '');
  fireEvent.keyDown(screen.getByRole('listbox', { name: '模型供应商' }), { key: 'Escape' });
  return names;
}

describe('Workstation connection admission and next-turn model', () => {
  it('keeps a new connection out of the selector when its first chat-check fails', async () => {
    chatCheck = () => json(502, { detail: { code: 'provider_unavailable' } });
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('DeepSeek'));
    const reconnectsBefore = hoisted.reconnect.mock.calls.length;

    await createConnection();

    expect(localStorage.getItem('amadeus_pc_provider')).not.toBe('custom:new');
    expect(hoisted.reconnect.mock.calls.length).toBe(reconnectsBefore);
    expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('DeepSeek');
    expect(providerOptions().some((name) => name.includes('New One'))).toBe(false);
    expect(JSON.parse(localStorage.getItem('amadeus_pc_pending_providers') || '[]')).toContain('custom:new');
  });

  it('admits and selects a new connection once its chat-check passes', async () => {
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('DeepSeek'));

    await createConnection();

    await waitFor(() => expect(localStorage.getItem('amadeus_pc_provider')).toBe('custom:new'));
    expect(hoisted.reconnect).toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem('amadeus_pc_pending_providers') || '[]')).not.toContain('custom:new');
    expect(providerOptions().some((name) => name.includes('New One'))).toBe(true);
  });

  it('reports a failed next-turn model update and keeps the old model', async () => {
    providers = [provider('deepseek', 'DeepSeek', 'deepseek-v4-flash')];
    let thinking = true;
    let params: { onTurnStarted?: (id: string) => void } = {};
    hoisted.useAmadeusWS.mockImplementation((p: typeof params) => { params = p; return wsState({ isThinking: thinking }); });
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型' })).not.toBeDisabled());
    // The running turn materialises the conversation.
    await act(async () => { params.onTurnStarted?.('conversation-one'); });

    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: '模型' }));
      expect(screen.getByRole('option', { name: 'deepseek-v4-pro' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('option', { name: 'deepseek-v4-pro' }));
    expect(await screen.findByText('下一轮：deepseek-v4-pro')).toBeInTheDocument();

    thinking = false;
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);

    expect(await screen.findByText(/没能切换到 deepseek-v4-pro/)).toBeInTheDocument();
    expect(screen.queryByText('下一轮：deepseek-v4-pro')).toBeNull();
    expect(screen.getByRole('button', { name: '模型' })).toHaveTextContent('deepseek-v4-flash');
  });
});
