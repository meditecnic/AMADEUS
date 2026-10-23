import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Workstation } from './Workstation';

/* Failure modes guarded here (slice 3):
 * 1. A newly created connection whose first chat-check fails must stay out of the chat selector
 *    and must not replace the current selection.
 * 2. A passing check admits and selects it (admission is written to the backend).
 * 2b. A pass for a configuration that changed meanwhile (409) must not admit or select it.
 * 3. A model chosen mid-turn whose update fails after the turn must say so and keep the old model. */

const hoisted = vi.hoisted(() => ({
  reconnect: vi.fn(),
  useAmadeusWS: vi.fn(),
  settingsProps: { current: null as null | Record<string, unknown> },
  sidebarProps: { current: null as null | Record<string, unknown> },
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
vi.mock('./HistorySidebar', () => ({
  HistorySidebar: (props: Record<string, unknown>) => { hoisted.sidebarProps.current = props; return null; },
}));
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
      providers = [...providers, provider('custom:new', 'New One', 'm1', {
        source: 'user_config', selector_admission: 'pending', configuration_version: 'v1',
      })];
      return json(200, { provider_id: 'custom:new', default_model: 'm1', enabled: true, selector_admission: 'pending', configuration_version: 'v1' });
    }
    if (decodeURIComponent(url) === '/api/providers/custom-profiles/custom:new' && init?.method === 'PATCH') {
      const body = JSON.parse(String(init.body)) as { selector_admission: string; expected_configuration_version: string };
      const row = providers.find((p) => p.id === 'custom:new')!;
      if (body.expected_configuration_version !== (row as { configuration_version?: string }).configuration_version) {
        return json(409, { detail: { code: 'configuration_changed' } });
      }
      providers = providers.map((p) => (p.id === 'custom:new' ? { ...p, selector_admission: body.selector_admission } : p));
      return json(200, { provider_id: 'custom:new', selector_admission: body.selector_admission });
    }
    if (url.endsWith('/chat-check')) return chatCheck();
    if (url.startsWith('/api/providers/deepseek/models')) {
      return json(200, { catalog: [capability('deepseek-v4-flash'), capability('deepseek-v4-pro')] });
    }
    if (decodeURIComponent(url).startsWith('/api/providers/custom:new/models')) return json(200, { catalog: [capability('m1')] });
    if (url.startsWith('/api/providers/local/models')) return json(200, { catalog: [capability('l1')] });
    if (url.startsWith('/api/conversations?')) {
      return json(200, [{
        id: 'conversation-one', title: 'One', title_source: 'auto', is_default: false, is_pinned: false,
        provider_id: 'deepseek', model_id: 'deepseek-v4-flash', last_active_at: '2026-09-28T00:00:00Z',
      }]);
    }
    if (url.startsWith('/api/conversations/conversation-one') && init?.method === 'PATCH') return patchResponse();
    if (url.startsWith('/api/conversations/conversation-one/messages')) return json(200, []);
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

describe('Workstation stopped reconnection', () => {
  it.each([
    ['forbidden', '后端拒绝了连接，已停止自动重连。', 'steins_gate', true],
    ['replaced', '连接已被另一个窗口接管，已停止自动重连。', 'beta', true],
    ['forbidden', '后端拒绝了连接，已停止自动重连。', 'steins_gate', false],
    ['replaced', '连接已被另一个窗口接管，已停止自动重连。', 'beta', false],
  ] as const)('shows %s in empty and populated conversations: %s (%s, configured=%s)', async (reason, message, worldline, configured) => {
    providers = [provider('deepseek', 'DeepSeek', 'deepseek-v4-flash', { credential_required: true, configured })];
    let state = wsState({ status: 'CLOSED', sessionState: 'unknown', reconnectStopReason: reason });
    hoisted.useAmadeusWS.mockImplementation(() => state);
    const view = render(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(await screen.findByRole('button', { name: '重新连接' })).toBeInTheDocument();
    if (!configured) await screen.findByRole('button', { name: '填写密钥' });
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText('OFFLINE · 自动重连已停止')).toBeInTheDocument();
    expect(screen.getByLabelText('输入消息')).toBeDisabled();
    const before = hoisted.reconnect.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '重新连接' }));
    expect(hoisted.reconnect).toHaveBeenCalledTimes(before + 1);

    state = wsState({
      status: 'CLOSED', sessionState: 'unknown', reconnectStopReason: reason,
      messages: [{ id: 'user-one', role: 'user', content: '已发送的消息' }],
    });
    view.rerender(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('已发送的消息')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-link-state')).toHaveTextContent(message);
    expect(screen.queryByText(/系统将尝试重新连接|自动重连没有成功|正在自动重连/)).toBeNull();
    expect(screen.getByRole('button', { name: '重新连接' })).toBeInTheDocument();
  });

  it('keeps n/5 visible after transport open while session.ready is pending', async () => {
    hoisted.useAmadeusWS.mockImplementation(() => wsState({
      status: 'READY', sessionState: 'unknown', retry: { attempt: 3, max: 5, exhausted: false },
    }));
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(await screen.findByText('OFFLINE · 重连 3/5')).toBeInTheDocument();
    expect(screen.getByText('连接中断，正在自动重连（3/5）。')).toBeInTheDocument();
    expect(screen.getByLabelText('输入消息')).toBeDisabled();
    expect(screen.queryByText('ONLINE')).toBeNull();
  });

  it('stops showing automatic reconnection when the open session is rejected', async () => {
    let state = wsState({
      status: 'CLOSED', sessionState: 'unknown', retry: { attempt: 1, max: 5, exhausted: false },
      messages: [{ id: 'user-one', role: 'user', content: '已发送的消息' }],
    });
    hoisted.useAmadeusWS.mockImplementation(() => state);
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(await screen.findByText('连接中断，正在自动重连（1/5）。')).toBeInTheDocument();
    state = { ...state, status: 'READY' };
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 重连 1/5')).toBeInTheDocument();

    state = wsState({
      status: 'ERROR', sessionState: 'rejected', retry: null,
      messages: [{ id: 'user-one', role: 'user', content: '已发送的消息' }],
    });
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 会话被拒绝')).toBeInTheDocument();
    expect(screen.queryByText(/正在自动重连/)).toBeNull();
    expect(view.container.querySelector('.ws2-link-state')).toBeNull();
  });

  it('keeps the missing-key action available in stopped empty and populated conversations', async () => {
    providers = [provider('deepseek', 'DeepSeek', 'deepseek-v4-flash', { credential_required: true, configured: false })];
    let state = wsState({ status: 'CLOSED', sessionState: 'unknown', reconnectStopReason: 'forbidden' });
    hoisted.useAmadeusWS.mockImplementation(() => state);
    const view = render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(await screen.findByRole('button', { name: '填写密钥' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '重新连接' })).toBeEnabled();

    state = wsState({
      status: 'CLOSED', sessionState: 'unknown', reconnectStopReason: 'forbidden',
      messages: [{ id: 'user-one', role: 'user', content: '已发送的消息' }],
    });
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('已发送的消息')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '填写密钥' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '重新连接' })).toBeEnabled();
  });
});

describe('Workstation missing-key reconnection', () => {
  it.each([
    ['steins_gate', false],
    ['steins_gate', true],
    ['beta', false],
    ['beta', true],
  ] as const)('prioritizes link state and restores setup after recovery (%s, populated=%s)', async (worldline, populated) => {
    providers = [provider('deepseek', 'DeepSeek', 'deepseek-v4-flash', { credential_required: true, configured: false })];
    const messages = populated ? [{ id: 'user-one', role: 'user', content: '已发送的消息' }] : [];
    let state = wsState({
      status: 'CLOSED', sessionState: 'unknown', retry: { attempt: 2, max: 5, exhausted: false }, messages,
    });
    hoisted.useAmadeusWS.mockImplementation(() => state);
    const view = render(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await screen.findByRole('button', { name: '填写密钥' });
    expect(screen.getByText('OFFLINE · 重连 2/5')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-online')).toHaveAttribute('data-status', 'CLOSED');
    expect(screen.getByText('连接中断，正在自动重连（2/5）。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新连接' })).toBeNull();
    expect(screen.queryByText('OFFLINE · 待配置')).toBeNull();
    expect(screen.getByLabelText('输入消息')).toBeDisabled();
    if (!populated) expect(screen.getByRole('heading', { name: '正在接通' })).toBeInTheDocument();

    // A transport open without a server session is still the same retry.
    state = { ...state, status: 'READY' };
    view.rerender(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 重连 2/5')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-online')).toHaveAttribute('data-status', 'READY');
    expect(screen.getByText('连接中断，正在自动重连（2/5）。')).toBeInTheDocument();

    state = wsState({ status: 'ERROR', sessionState: 'unknown', retry: { attempt: 5, max: 5, exhausted: true }, messages });
    view.rerender(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 重连失败')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-online')).toHaveAttribute('data-status', 'ERROR');
    expect(screen.getByText(populated ? '自动重连没有成功。' : '自动重连 5 次都没有接通。请确认后端服务已启动，再重新连接。')).toBeInTheDocument();
    if (!populated) expect(screen.getByRole('heading', { name: '暂时无法接通' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新连接' }));
    expect(hoisted.reconnect).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '填写密钥' })).toBeEnabled();

    state = wsState({ status: 'CONNECTING', sessionState: 'unknown', retry: null, messages });
    view.rerender(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 连接中')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-online')).toHaveAttribute('data-status', 'CONNECTING');
    expect(screen.queryByRole('button', { name: '重新连接' })).toBeNull();

    state = wsState({ status: 'CLOSED', sessionState: 'unknown', retry: null, messages });
    view.rerender(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 待配置')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-online')).toHaveAttribute('data-status', 'SETUP');
    if (!populated) expect(screen.getByRole('heading', { name: '尚未接通' })).toBeInTheDocument();

    state = wsState({
      status: 'ERROR', sessionState: 'rejected', retry: null, messages: [
        ...messages, { id: 'auth-error', role: 'assistant', content: 'SYSTEM ERROR: 未认证。请打开设置并配置您的 API Key。', systemError: true },
      ],
    });
    view.rerender(<Workstation worldline={worldline} onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    expect(screen.getByText('OFFLINE · 待配置')).toBeInTheDocument();
    expect(view.container.querySelector('.ws2-online')).toHaveAttribute('data-status', 'SETUP');
    expect(screen.queryByText(/正在自动重连|自动重连没有成功|自动重连 5 次/)).toBeNull();
    expect(view.container.querySelector('.ws2-link-state')).toBeNull();
    expect(screen.queryByRole('button', { name: '重新连接' })).toBeNull();
    expect(screen.queryByText(/SYSTEM ERROR/)).toBeNull();
    if (!populated) expect(screen.getByRole('heading', { name: '尚未接通' })).toBeInTheDocument();
  });
});

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
    expect(providers.find((p) => p.id === 'custom:new')).toMatchObject({ selector_admission: 'pending' });
  });

  it('does not admit a connection whose configuration changed while its check ran', async () => {
    chatCheck = () => {
      // The user saved a new address mid-check; the backend version moved on.
      providers = providers.map((p) => (p.id === 'custom:new' ? { ...p, configuration_version: 'v2' } : p));
      return json(200, { ok: true, provider_id: 'custom:new', model_id: 'm1' });
    };
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('DeepSeek'));

    await createConnection();

    expect(localStorage.getItem('amadeus_pc_provider')).not.toBe('custom:new');
    expect(providers.find((p) => p.id === 'custom:new')).toMatchObject({ selector_admission: 'pending' });
    expect(providerOptions().some((name) => name.includes('New One'))).toBe(false);
  });

  it('admits and selects a new connection once its chat-check passes', async () => {
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('DeepSeek'));

    await createConnection();

    await waitFor(() => expect(localStorage.getItem('amadeus_pc_provider')).toBe('custom:new'));
    expect(hoisted.reconnect).toHaveBeenCalled();
    expect(providers.find((p) => p.id === 'custom:new')).toMatchObject({ selector_admission: 'admitted' });
    expect(providerOptions().some((name) => name.includes('New One'))).toBe(true);
  });

  it('remembers the provider together with the model when a conversation switches connection', async () => {
    providers = [
      provider('deepseek', 'DeepSeek', 'deepseek-v4-flash'),
      provider('local', 'Local', 'l1'),
    ];
    patchResponse = () => json(200, {
      id: 'conversation-one', title: 'One', title_source: 'auto', is_default: false, is_pinned: false,
      provider_id: 'local', model_id: 'l1', last_active_at: '2026-09-28T00:00:00Z',
    });
    let params: { onTurnStarted?: (id: string) => void } = {};
    hoisted.useAmadeusWS.mockImplementation((p: typeof params) => { params = p; return wsState(); });
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).not.toBeDisabled());
    await act(async () => { params.onTurnStarted?.('conversation-one'); });

    fireEvent.click(screen.getByRole('button', { name: '模型供应商' }));
    fireEvent.click(screen.getByRole('option', { name: 'Local' }));

    // A restart starts from these two keys; a mismatched pair is rejected by the backend.
    await waitFor(() => expect(localStorage.getItem('amadeus_pc_model')).toBe('l1'));
    expect(localStorage.getItem('amadeus_pc_provider')).toBe('local');
  });

  it('remembers the provider together with the model when opening a conversation', async () => {
    providers = [
      provider('deepseek', 'DeepSeek', 'deepseek-v4-flash'),
      provider('local', 'Local', 'l1'),
    ];
    localStorage.setItem('amadeus_pc_provider', 'local');
    localStorage.setItem('amadeus_pc_model', 'l1');
    let params: { onConversationSwitched?: (event: { conversationId: string }) => void } = {};
    hoisted.useAmadeusWS.mockImplementation((p: typeof params) => { params = p; return wsState(); });
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect((hoisted.sidebarProps.current?.conversations as unknown[] | undefined)?.length).toBe(1));

    const sidebar = hoisted.sidebarProps.current!;
    await act(async () => {
      (sidebar.onSelect as (row: unknown) => void)((sidebar.conversations as unknown[])[0]);
    });
    await act(async () => { params.onConversationSwitched?.({ conversationId: 'conversation-one' }); });

    // The next draft (new conversation, other worldline, restart) starts from these two keys.
    expect(localStorage.getItem('amadeus_pc_model')).toBe('deepseek-v4-flash');
    expect(localStorage.getItem('amadeus_pc_provider')).toBe('deepseek');
  });

  it('keeps a connection switch when an older conversation list arrives after it', async () => {
    providers = [
      provider('deepseek', 'DeepSeek', 'deepseek-v4-flash'),
      provider('local', 'Local', 'l1'),
    ];
    const row = (providerId: string, modelId: string) => ({
      id: 'conversation-one', title: 'One', title_source: 'auto', is_default: false, is_pinned: false, is_selected: true,
      provider_id: providerId, model_id: modelId, last_active_at: '2026-09-28T00:00:00Z',
    });
    patchResponse = () => json(200, row('local', 'l1'));
    const baseFetch = globalThis.fetch;
    let releaseList: (() => void) | null = null;
    let holdList = false;
    let patched = false;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (holdList && url.startsWith('/api/conversations?')) {
        holdList = false;
        return new Promise<Response>((resolve) => { releaseList = () => resolve(json(200, [row('deepseek', 'deepseek-v4-flash')])); });
      }
      // Lists requested after the write see the stored change, as the backend would.
      if (patched && url.startsWith('/api/conversations?')) return Promise.resolve(json(200, [row('local', 'l1')]));
      if (init?.method === 'PATCH') patched = true;
      return baseFetch(input, init);
    }));
    let params: { onTurnStarted?: (id: string) => void; onTurnCompleted?: () => void } = {};
    hoisted.useAmadeusWS.mockImplementation((p: typeof params) => { params = p; return wsState(); });
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).not.toBeDisabled());
    const listCalls = () => vi.mocked(globalThis.fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/conversations?')).length;
    const listsBeforeTurn = listCalls();
    await act(async () => { params.onTurnStarted?.('conversation-one'); });
    await waitFor(() => expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('DeepSeek'));
    // Wait until the delayed refresh scheduled by turn start has been issued, so it cannot take the held request below.
    await waitFor(() => expect(listCalls()).toBeGreaterThan(listsBeforeTurn));

    // The end of the turn starts a list refresh that is still in flight when the user switches.
    holdList = true;
    await act(async () => { params.onTurnCompleted?.(); });
    await waitFor(() => expect(releaseList).not.toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '模型供应商' }));
    fireEvent.click(screen.getByRole('option', { name: 'Local' }));
    await waitFor(() => expect(localStorage.getItem('amadeus_pc_provider')).toBe('local'));

    await act(async () => { releaseList?.(); });
    expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('Local');
  });

  it('keeps a successful model switch when the provider refresh fails', async () => {
    let patched = false;
    patchResponse = () => {
      patched = true;
      return json(200, {
        id: 'conversation-one', title: 'One', title_source: 'auto', is_default: false, is_pinned: false,
        provider_id: 'deepseek', model_id: 'deepseek-v4-pro', last_active_at: '2026-09-28T00:00:00Z',
      });
    };
    const baseFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => (
      String(input) === '/api/providers' && patched
        ? Promise.resolve(json(500, {}))
        : baseFetch(input, init)
    )));
    let params: { onConversationSwitched?: (payload: { conversationId: string }) => void } = {};
    hoisted.useAmadeusWS.mockImplementation((p: typeof params) => { params = p; return wsState(); });
    render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
    await waitFor(() => expect(hoisted.sidebarProps.current?.conversations).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: 'conversation-one' })]),
    ));
    await act(async () => { params.onConversationSwitched?.({ conversationId: 'conversation-one' }); });
    await waitFor(() => expect(screen.getByRole('button', { name: '模型' })).not.toBeDisabled());

    fireEvent.click(screen.getByRole('button', { name: '模型' }));
    fireEvent.click(await screen.findByRole('option', { name: 'deepseek-v4-pro' }));

    expect(await screen.findByText('刷新失败')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '模型' })).not.toBeDisabled());
    expect(screen.queryByText(/没能切换到/)).toBeNull();
    expect(screen.getByRole('button', { name: '模型' })).toHaveTextContent('deepseek-v4-pro');
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

    fireEvent.click(screen.getByRole('button', { name: '模型' }));
    expect(await screen.findByRole('option', { name: 'deepseek-v4-pro' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: 'deepseek-v4-pro' }));
    expect(await screen.findByText('下一轮：deepseek-v4-pro')).toBeInTheDocument();

    thinking = false;
    view.rerender(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);

    expect(await screen.findByText(/没能切换到 deepseek-v4-pro/)).toBeInTheDocument();
    expect(screen.queryByText('下一轮：deepseek-v4-pro')).toBeNull();
    expect(screen.getByRole('button', { name: '模型' })).toHaveTextContent('deepseek-v4-flash');
  });
});
