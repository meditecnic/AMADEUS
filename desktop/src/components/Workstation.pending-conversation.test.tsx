import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Workstation } from './Workstation';

// Keep the real Workstation, HistorySidebar and useAmadeusWS; replace peripherals only.
vi.mock('../hooks/useAudioPlayer', () => ({
  useAudioPlayer: () => ({ currentVolume: 0, isPlaying: false }),
  audioPlayer: { init: vi.fn(), playBase64: vi.fn(), stopAll: vi.fn() },
}));
vi.mock('../services/AudioService', () => ({
  audioService: {
    setEnableBGM: vi.fn(), setBGMVolume: vi.fn(), setEnableSFX: vi.fn(),
    setSFXVolume: vi.fn(), playBGMForWorldline: vi.fn(), stopBGM: vi.fn(), playSFX: vi.fn(),
  },
}));
vi.mock('./SettingsModal', () => ({
  SettingsModal: () => null,
  CHAT_TEMPERATURE_MIN: 0, CHAT_TEMPERATURE_MAX: 1.2, CHAT_TEMPERATURE_STEP: 0.1,
  CHAT_TEMPERATURE_DEFAULT: 0, DEFAULT_IDENTITY_MODE: 'okabe',
  clampChatTemperature: (v: number) => Number.isFinite(v) ? Math.max(0, Math.min(1.2, v)) : 0,
  normalizeIdentityMode: (v: unknown) => v === 'self' ? 'self' : 'okabe',
  loadStoredIdentityMode: () => 'okabe', persistIdentityMode: () => {},
  loadStoredSelfName: () => '', persistSelfName: () => {},
}));
vi.mock('./TtsDebugPanel', () => ({ TtsDebugPanel: () => null }));
vi.mock('./AvatarViewer', () => ({ default: () => null }));
vi.mock('./SpriteAnimator', () => ({ default: () => null }));
vi.mock('./BilingualMessage', () => ({ BilingualMessage: () => null }));
vi.mock('./ForgetConversationModal', () => ({ ForgetConversationModal: () => null }));
vi.mock('../memory-space/MemorySpace', () => ({ MemorySpace: () => null }));

type Frame = Record<string, any>;
class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: Frame[] = [];
  chats: { content: string; conversationId: string | null }[] = [];
  conversationId: string | null = null;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string) { FakeWebSocket.instances.push(this); }
  emit(frame: Frame) { this.onmessage?.({ data: JSON.stringify(frame) }); }
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }
  close() { this.readyState = 3; }
  drop() { this.readyState = 3; this.onclose?.({ code: 1006 }); }
  send(raw: string) {
    const frame = JSON.parse(raw) as Frame;
    this.sent.push(frame);
    if (frame.type === 'auth') {
      this.conversationId = frame.conversation_id ?? null;
      queueMicrotask(() => this.emit({
        type: 'session.ready', conversation_id: this.conversationId,
        conversation_mode: frame.conversation_mode, worldline: 'steins_gate', generation: 1,
      }));
    }
    if (frame.type === 'conversation.switch') {
      this.conversationId = frame.conversation_id;
      queueMicrotask(() => this.emit({
        type: 'conversation.switched', request_id: frame.request_id,
        conversation_id: this.conversationId, worldline: 'steins_gate', generation: 2,
      }));
    }
    if (frame.type === 'chat.send') {
      this.chats.push({ content: frame.content, conversationId: this.conversationId });
    }
  }
}
const model = 'deepseek-v4-flash';
const cap = { id: model, display_name: model, lifecycle: 'stable', callable: true,
  source: 'manifest', thinking_control: null };
const oldRow = { id: 'old', title: '旧对话', title_source: 'auto', is_default: false,
  is_pinned: false, is_selected: true, provider_id: 'deepseek', model_id: model,
  identity_mode: 'okabe', last_active_at: '2026-10-23T00:00:00Z' };
const newRow = { ...oldRow, id: 'new', title: 'New Conversation', is_selected: false };
const json = (body: unknown, status = 200) => ({ ok: status < 400, status,
  json: async () => body } as Response);
let rows: typeof oldRow[];
let httpDown: boolean;
let postCount: number;
let historyCount: number;
let patchCount: number;
let deleteCount: number;

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  localStorage.setItem('amadeus_pc_session_id', 'issue46');
  localStorage.setItem('amadeus_pc_model', model);
  localStorage.setItem('amadeus_pc_enable_tts', 'false');
  localStorage.setItem('amadeus_pc_history_collapsed', 'false');
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
  FakeWebSocket.instances = []; rows = [oldRow]; httpDown = false; postCount = 0; historyCount = 0; patchCount = 0; deleteCount = 0;
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => window.setTimeout(() => cb(performance.now()), 16));
  vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id));
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('prefers-reduced-motion'),
    media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/conversations' && init?.method === 'POST') {
      postCount++;
      if (httpDown) throw new TypeError('Failed to fetch');
      rows = [newRow, oldRow]; return json(newRow, 201);
    }
    if (httpDown) throw new TypeError('Failed to fetch');
    if (url.startsWith('/api/conversations/old?') && init?.method === 'DELETE') {
      deleteCount++; rows = rows.filter(r => r.id !== 'old'); return json(null, 204);
    }
    if (url.startsWith('/api/conversations/old?') && init?.method === 'PATCH') {
      patchCount++;
      const patch = JSON.parse(String(init.body));
      const updated = { ...rows.find(r => r.id === 'old')!, ...patch,
        ...(patch.provider_id === 'local' ? { model_id: 'local-model' } : {}) };
      rows = rows.map(r => r.id === 'old' ? updated : r);
      return json(updated);
    }
    if (url === '/api/providers') return json([{ id: 'deepseek', display_name: 'DeepSeek',
      default_model: model, default_model_capability: cap, catalog_version: 1,
      configured: true, credential_required: false, model_discovery: true, enabled: true },
      { id: 'local', display_name: 'Local', default_model: 'local-model',
        default_model_capability: { ...cap, id: 'local-model', display_name: 'local-model' },
        catalog_version: 1, configured: true, credential_required: false, model_discovery: true, enabled: true }]);
    if (url.startsWith('/api/providers/local/models')) return json({ catalog: [{ ...cap, id: 'local-model', display_name: 'local-model' }] });
    if (url.startsWith('/api/providers/deepseek/models')) return json({ catalog: [cap, { ...cap, id: 'deepseek-v4-pro', display_name: 'deepseek-v4-pro' }] });
    if (url.startsWith('/api/conversations?')) return json(url.includes('worldline=beta') ? [] : rows);
    if (url.includes('/messages?')) {
      historyCount++;
      await new Promise(resolve => setTimeout(resolve, 5));
      return json(url.includes('/old/') ? [{ role: 'user', content: '旧对话内容' }] : []);
    }
    if (url === '/api/search/settings') return json({ provider: 'tavily', configured: false });
    throw new Error(`unexpected fetch: ${url}`);
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const active = () => document.querySelector('.conversation-item.active')?.getAttribute('data-conversation-id');
const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
async function startOld() {
  const onWorldlineChange = vi.fn();
  const view = render(<Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />);
  await act(async () => { FakeWebSocket.instances[0].open(); });
  await screen.findByText('旧对话', { selector: '.conversation-title' });
  fireEvent.click(document.querySelector('[data-conversation-id="old"] .conversation-select')!);
  await waitFor(() => expect(active()).toBe('old'));
  expect(await screen.findByText('旧对话内容')).toBeInTheDocument();
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
  act(() => FakeWebSocket.instances[0].drop());
  expect(screen.getByLabelText('输入消息')).toBeDisabled();
  return { onWorldlineChange, view };
}
async function recover() {
  await flush(1000);
  const socket = FakeWebSocket.instances.at(-1)!;
  await act(async () => { socket.open(); });
  expect(screen.getByLabelText('输入消息')).toBeEnabled();
  return socket;
}

it('A regression: a successful offline create must become the target after reconnect', async () => {
  await startOld();
  expect(screen.getByRole('button', { name: /新建会话/ })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
  await flush(300);
  expect(postCount).toBe(1);
  expect(rows.map(r => r.id)).toEqual(['new', 'old']);
  expect(document.querySelector('[data-conversation-id="new"]')).toBeInTheDocument();
  expect(active()).toBe('old');
  const socket = await recover();
  const auth = socket.sent.find(f => f.type === 'auth');
  fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '恢复后下一句' } });
  fireEvent.submit(document.querySelector('form.ws2-composer')!);
  expect.soft(auth?.conversation_id, 'reconnect must remember the newly created target').toBe('new');
  expect.soft(active(), 'sidebar must select the created conversation after reconnect').toBe('new');
  expect.soft(socket.chats, 'next chat must use the created conversation').toEqual([
    { content: '恢复后下一句', conversationId: 'new' },
  ]);
});
