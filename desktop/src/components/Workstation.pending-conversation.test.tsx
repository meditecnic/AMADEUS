import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  loadStoredIdentityMode: () => localStorage.getItem('amadeus_pc_identity_mode') || 'okabe', persistIdentityMode: () => {},
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
  static autoReady = true;
  static autoSwitch = true;
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
  ready(overrides: Frame = {}) {
    const auth = this.sent.find(f => f.type === 'auth')!;
    this.emit({ type: 'session.ready', conversation_id: this.conversationId,
      conversation_mode: auth.conversation_mode, worldline: auth.worldline, generation: 1, ...overrides });
  }
  send(raw: string) {
    const frame = JSON.parse(raw) as Frame;
    this.sent.push(frame);
    if (frame.type === 'auth') {
      this.conversationId = frame.conversation_id ?? null;
      if (FakeWebSocket.autoReady) queueMicrotask(() => this.ready());
    }
    if (frame.type === 'conversation.switch') {
      this.conversationId = frame.conversation_id;
      if (FakeWebSocket.autoSwitch) queueMicrotask(() => this.emit({
        type: 'conversation.switched', request_id: frame.request_id,
        conversation_id: this.conversationId, worldline: 'steins_gate', generation: 2,
      }));
    }
    if (frame.type === 'worldline.switch') queueMicrotask(() => this.emit({
      type: 'worldline.switched', request_id: frame.request_id, worldline: frame.target_worldline, generation: 3,
    }));
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
let listQueryCount: number;
let afterPost: (() => void) | null;
const missingHistory = new Set<string>();

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'] });
  localStorage.clear(); sessionStorage.clear();
  localStorage.setItem('amadeus_pc_session_id', 'issue46');
  localStorage.setItem('amadeus_pc_model', model);
  localStorage.setItem('amadeus_pc_enable_tts', 'false');
  localStorage.setItem('amadeus_pc_history_collapsed', 'false');
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
  FakeWebSocket.instances = []; rows = [oldRow]; httpDown = false; postCount = 0; historyCount = 0; patchCount = 0; deleteCount = 0;
  FakeWebSocket.autoReady = true; FakeWebSocket.autoSwitch = true; afterPost = null; missingHistory.clear();
  listQueryCount = 0;
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
      const created = { ...newRow, id: postCount === 1 ? 'new' : `new-${postCount}`, ...JSON.parse(String(init.body)) };
      rows = [created, ...rows]; afterPost?.(); return json(created, 201);
    }
    if (httpDown) throw new TypeError('Failed to fetch');
    if (url.startsWith('/api/conversations/') && init?.method === 'DELETE') {
      const id = url.split('/')[3].split('?')[0];
      deleteCount++; rows = rows.filter(r => r.id !== id); return json(null, 204);
    }
    if (url.startsWith('/api/conversations/') && init?.method === 'PATCH') {
      patchCount++;
      const id = url.split('/')[3].split('?')[0];
      const patch = JSON.parse(String(init.body));
      const updated = { ...rows.find(r => r.id === id)!, ...patch,
        ...(patch.provider_id === 'local' ? { model_id: 'local-model' } : {}) };
      rows = rows.map(r => r.id === id ? updated : r);
      return json(updated);
    }
    if (url === '/api/providers') return json([{ id: 'deepseek', display_name: 'DeepSeek',
      default_model: model, default_model_capability: cap, catalog_version: 1,
      configured: true, credential_required: false, model_discovery: true, enabled: true },
      { id: 'local', display_name: 'Local', default_model: 'local-model',
        default_model_capability: { ...cap, id: 'local-model', display_name: 'local-model' },
        catalog_version: 1, configured: true, credential_required: false, model_discovery: true, enabled: true },
      { id: 'missing-key-provider', display_name: 'Missing Key', default_model: model,
        default_model_capability: cap, catalog_version: 1, configured: false,
        credential_required: true, model_discovery: true, enabled: true },
      { id: 'removed', display_name: 'Removed', default_model: 'gone-model',
        default_model_capability: { ...cap, id: 'gone-model', display_name: 'gone-model', callable: false, lifecycle: 'unavailable' },
        catalog_version: 1, configured: false, credential_required: false, model_discovery: true, enabled: false }]);
    if (url.startsWith('/api/providers/local/models')) return json({ catalog: [{ ...cap, id: 'local-model', display_name: 'local-model' }] });
    if (url.startsWith('/api/providers/removed/models')) return json({ catalog: [] });
    if (url.startsWith('/api/providers/deepseek/models')) return json({ catalog: [cap, { ...cap, id: 'deepseek-v4-pro', display_name: 'deepseek-v4-pro' }] });
    if (url.startsWith('/api/conversations?')) {
      listQueryCount++;
      return json(url.includes('worldline=beta') ? [] : rows);
    }
    if (url.includes('/messages?')) {
      historyCount++;
      await new Promise(resolve => setTimeout(resolve, 5));
      const id = url.split('/')[3];
      if (missingHistory.has(id)) return json({}, 404);
      return json([{ role: 'user', content: id === 'old' ? '旧对话内容' : `历史 ${id}` }]);
    }
    if (url === '/api/search/settings') return json({ provider: 'tavily', configured: false });
    throw new Error(`unexpected fetch: ${url}`);
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const active = () => document.querySelector('.conversation-item.active')?.getAttribute('data-conversation-id');
const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
async function startOld(drop = true) {
  const onWorldlineChange = vi.fn();
  const view = render(<Workstation worldline="steins_gate" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />);
  await act(async () => { FakeWebSocket.instances[0].open(); });
  await flush();
  expect(screen.getByText('旧对话', { selector: '.conversation-title' })).toBeInTheDocument();
  fireEvent.click(document.querySelector('[data-conversation-id="old"] .conversation-select')!);
  await flush(100);
  // Works on both sides of the fix: an online ACK used to enter immediately.
  if (FakeWebSocket.instances.at(-1)!.readyState === 0) await act(async () => { FakeWebSocket.instances.at(-1)!.open(); });
  expect(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  if (drop) {
    act(() => FakeWebSocket.instances.at(-1)!.drop());
    expect(screen.getByLabelText('输入消息')).toBeDisabled();
  }
  return { onWorldlineChange, view };
}
async function recover() {
  await flush(1000);
  const socket = FakeWebSocket.instances.at(-1)!;
  await act(async () => { socket.open(); });
  if (FakeWebSocket.autoReady) expect(screen.getByLabelText('输入消息')).toBeEnabled();
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
  expect(active()).toBe('new');
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

const latest = () => FakeWebSocket.instances.at(-1)!;
const newButton = () => screen.getByRole('button', { name: /新建会话/ });
const sourceStillShown = (targetId = 'new') => {
  expect(active()).toBe(targetId);
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  expect(screen.getByLabelText('输入消息')).toBeDisabled();
};
async function clickHistory(id: string) {
  fireEvent.click(document.querySelector(`[data-conversation-id="${id}"] .conversation-select`)!);
  await flush(20); await flush(100);
}

it('retains a target when the socket closes after POST succeeds', async () => {
  await startOld(false);
  afterPost = () => latest().drop();
  fireEvent.click(newButton()); await flush(300);
  sourceStillShown();
  const socket = await recover();
  expect(socket.sent.find(f => f.type === 'auth')).toMatchObject({ conversation_id: 'new', conversation_mode: 'history' });
  expect(active()).toBe('new');
  expect(screen.queryByText('旧对话内容')).toBeNull();
});

it('retains an already dispatched switch through a close before ACK and repeated reconnects', async () => {
  rows = [newRow, oldRow];
  await startOld(false);
  FakeWebSocket.autoSwitch = false;
  const sourceSocket = latest();
  await clickHistory('new');
  const switchRequest = sourceSocket.sent.find(f => f.type === 'conversation.switch' && f.conversation_id === 'new');
  expect(switchRequest).toBeDefined();
  sourceStillShown();
  FakeWebSocket.autoReady = false;
  act(() => sourceSocket.drop()); await flush();
  // The first reconnect is requested only after the target params commit.
  act(() => latest().open());
  for (const delay of [1000, 2000]) {
    expect(latest().sent[0]).toMatchObject({ conversation_id: 'new' });
    act(() => latest().drop()); await flush(delay); act(() => latest().open());
    sourceStillShown();
  }
  await act(async () => latest().ready());
  expect(active()).toBe('new');
  expect(screen.getByText('历史 new')).toBeInTheDocument();
  expect(screen.getByLabelText('输入消息')).toBeEnabled();
});

it.each([{ conversation_id: 'old' }, { worldline: 'beta' }, { conversation_mode: 'draft' }])(
  'only commits transcript, selection and runtime for a matching ready: %j', async mismatch => {
    rows = [{ ...newRow, model_id: 'deepseek-v4-pro' }, oldRow];
    await startOld(); FakeWebSocket.autoReady = false;
    await clickHistory('new'); await recover();
    act(() => latest().ready(mismatch));
    sourceStillShown();
    expect(screen.getByRole('button', { name: '模型' })).toHaveTextContent(model);
    await act(async () => latest().ready());
    expect(active()).toBe('new');
    expect(screen.getByText('历史 new')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '模型' })).toHaveTextContent('deepseek-v4-pro');
    expect(screen.getByLabelText('输入消息')).toBeEnabled();
  },
);

it('lets a newer history selection supersede a pending target and rejects a retired ready/ACK', async () => {
  rows = [newRow, { ...newRow, id: 'other', title: '另一段' }, oldRow];
  await startOld(); FakeWebSocket.autoReady = false;
  await clickHistory('new'); await recover();
  const retired = latest().onmessage!;
  await clickHistory('other');
  act(() => retired({ data: JSON.stringify({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'new', worldline: 'steins_gate' }) }));
  sourceStillShown('other');
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'other' });
  await act(async () => latest().ready());
  expect(active()).toBe('other');
  expect(screen.getByText('历史 other')).toBeInTheDocument();
});

it('lets a second create replace the first pending request without creating a send window', async () => {
  await startOld(); FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300); await recover();
  const retired = latest().onmessage!;
  fireEvent.click(newButton()); await flush(300);
  sourceStillShown('new-2');
  act(() => retired({ data: JSON.stringify({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'new', worldline: 'steins_gate' }) }));
  sourceStillShown('new-2');
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'new-2' });
  await act(async () => latest().ready());
  expect(active()).toBe('new-2');
});

it('clicking the source explicitly cancels pending entry and restores selection, transcript and auth together', async () => {
  await startOld(); FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300); await recover();
  const retired = latest().onmessage!;
  fireEvent.click(document.querySelector('[data-conversation-id="old"] .conversation-select')!);
  await flush(100);
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'old' });
  await act(async () => latest().ready());
  act(() => retired({ data: JSON.stringify({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'new', worldline: 'steins_gate' }) }));
  expect(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  expect(screen.getByLabelText('输入消息')).toBeEnabled();
});

it('abandons a 404 auth target and reauthenticates the source without replacing its transcript', async () => {
  await startOld(); FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300); await recover();
  rows = [oldRow];
  act(() => latest().emit({ type: 'error', message: '请求的会话不存在或不属于当前世界线。' }));
  await flush(100);
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'old' });
  await act(async () => latest().ready());
  expect(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  expect(screen.getByTestId('session-error-toast')).toHaveTextContent('会话同步失败');
});

it('deleting the pending target restores the still-open source', async () => {
  await startOld(); FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300); await recover();
  fireEvent.keyDown(screen.getByRole('button', { name: '会话操作 New Conversation' }), { key: 'Enter' });
  fireEvent.click(screen.getByRole('menuitem', { name: '删除 New Conversation' }));
  fireEvent.click(screen.getByRole('button', { name: '删除会话' })); await flush(100);
  expect(deleteCount).toBe(1);
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'old' });
  await act(async () => latest().ready());
  expect(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
});

it('does not remember a history target when its HTTP history request returns 404', async () => {
  rows = [newRow, oldRow]; await startOld(); missingHistory.add('new');
  await clickHistory('new');
  expect(screen.queryByTestId('session-error-toast')).toBeNull();
  expect(active()).toBe('old');
  const socket = await recover();
  expect(socket.sent[0]).toMatchObject({ conversation_id: 'old' });
});

it.each([false, true])('self mode waits for confirmation before its first line (socket drops after POST=%s)', async dropAfterPost => {
  localStorage.setItem('amadeus_pc_identity_mode', 'self');
  render(<Workstation worldline="steins_gate" onWorldlineChange={vi.fn()} onLogout={vi.fn()} />);
  await act(async () => latest().open());
  await flush();
  expect(screen.getByLabelText('输入消息')).toBeEnabled();
  FakeWebSocket.autoReady = false;
  if (dropAfterPost) afterPost = () => latest().drop();
  else FakeWebSocket.autoSwitch = false;
  fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: 'self 第一行' } });
  fireEvent.submit(document.querySelector('form.ws2-composer')!);
  await flush(300);
  if (dropAfterPost) await recover();
  expect(FakeWebSocket.instances.flatMap(s => s.chats)).toEqual([]);
  expect(screen.getByLabelText('输入消息')).toBeDisabled();
  await act(async () => { if (dropAfterPost) latest().ready(); else switchAck(); });
  expect(latest().chats).toEqual([{ content: 'self 第一行', conversationId: 'new' }]);
});

it('a committed worldline shift discards an unconfirmed conversation target', async () => {
  const { view, onWorldlineChange } = await startOld();
  FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300); await recover();
  const retired = latest().onmessage!;
  fireEvent.click(screen.getByRole('button', { name: '系统设置' }));
  fireEvent.click(screen.getByRole('radio', { name: 'β 世界线' }));
  fireEvent.click(screen.getByRole('button', { name: '跃迁至 β' }));
  await flush(600);
  expect(onWorldlineChange).toHaveBeenCalledWith('beta');
  view.rerender(<Workstation worldline="beta" onWorldlineChange={onWorldlineChange} onLogout={vi.fn()} />);
  await flush(100);
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_mode: 'draft', worldline: 'beta' });
  expect(latest().sent[0]).not.toHaveProperty('conversation_id');
  act(() => retired({ data: JSON.stringify({ type: 'session.ready', conversation_mode: 'history', conversation_id: 'new', worldline: 'steins_gate' }) }));
  expect(document.querySelector('[data-conversation-id="new"].active')).toBeNull();
});

it('a failed worldline shift after pending entry reauthenticates the full source context', async () => {
  const { onWorldlineChange } = await startOld(); FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300);
  // The target socket has not opened; the worldline transaction must fail in place.
  fireEvent.click(screen.getByRole('button', { name: '系统设置' }));
  fireEvent.click(screen.getByRole('radio', { name: 'β 世界线' }));
  fireEvent.click(screen.getByRole('button', { name: '跃迁至 β' })); await flush(600);
  expect(onWorldlineChange).not.toHaveBeenCalled();
  expect(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  act(() => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'old', worldline: 'steins_gate' });
  await act(async () => latest().ready());
  expect(screen.getByLabelText('输入消息')).toBeEnabled();
});

const authCount = () => FakeWebSocket.instances.flatMap(s => s.sent).filter(f => f.type === 'auth').length;
const switchAck = (socket = latest()) => {
  const req = socket.sent.filter(f => f.type === 'conversation.switch').at(-1)!;
  socket.emit({ type: 'conversation.switched', request_id: req.request_id,
    conversation_id: req.conversation_id, worldline: 'steins_gate', generation: 5 });
};
function sendLine(content: string) {
  fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: content } });
  fireEvent.submit(document.querySelector('form.ws2-composer')!);
}

it.each(['conversation_switch_failed', 'conversation_not_found', 'socket_not_ready'])(
  'REWORK M1: live switch_error %s abandons target without reconnect and can send to source', async code => {
    rows = [newRow, oldRow]; await startOld(false); FakeWebSocket.autoSwitch = false;
    const socket = latest(); const sockets = FakeWebSocket.instances.length; const auths = authCount();
    await clickHistory('new');
    const req = socket.sent.filter(f => f.type === 'conversation.switch').at(-1)!;
    const queries = listQueryCount;
    // The backend has already rolled back before emitting switch_error.
    socket.conversationId = 'old';
    act(() => socket.emit({ type: 'conversation.switch_error', request_id: req.request_id, code }));
    await flush(100);
    expect.soft(FakeWebSocket.instances).toHaveLength(sockets);
    expect.soft(authCount()).toBe(auths);
    expect.soft(active()).toBe('old');
    expect.soft(screen.getByText('旧对话内容')).toBeInTheDocument();
    expect.soft(screen.getByLabelText('输入消息')).toBeEnabled();
    expect.soft(document.querySelector('.ws2-online')).toHaveTextContent('ONLINE');
    expect.soft(document.querySelector('.ws2-presence')).not.toHaveAttribute('data-presence', 'connecting');
    expect(screen.getByTestId('session-error-toast')).toHaveTextContent(code);
    expect(listQueryCount).toBe(queries + (code === 'conversation_not_found' ? 1 : 0));
    sendLine('失败后留在原对话');
    expect.soft(socket.chats).toEqual([{ content: '失败后留在原对话', conversationId: 'old' }]);
  },
);

it.each(['removed', 'unavailable-model', 'missing-key'])(
  'REWORK M2a: online history with %s opens on ACK without auth and allows a new connection', async kind => {
    const targetModel = kind === 'missing-key' ? model : 'gone-model';
    rows = [{ ...newRow, provider_id: kind === 'removed' ? 'removed' : kind === 'missing-key' ? 'missing-key-provider' : 'deepseek', model_id: targetModel }, oldRow];
    await startOld(false); const socket = latest(); const sockets = FakeWebSocket.instances.length; const auths = authCount();
    await clickHistory('new');
    expect.soft(FakeWebSocket.instances).toHaveLength(sockets);
    expect.soft(authCount()).toBe(auths);
    expect.soft(active()).toBe('new');
    expect.soft(screen.queryByText('历史 new')).toBeInTheDocument();
    expect.soft(screen.getByRole('button', { name: '模型' })).toHaveTextContent(targetModel);
    expect.soft(screen.getByRole('button', { name: '模型供应商' })).toBeEnabled();
    // Acknowledge the regression without turning its disabled controls into a test timeout.
    if (screen.getByRole('button', { name: '模型供应商' }).hasAttribute('disabled')) return;
    if (kind !== 'missing-key') {
      if (kind === 'removed') expect(screen.getByRole('button', { name: '模型供应商' })).toHaveTextContent('已停用/不可用');
      fireEvent.click(screen.getByRole('button', { name: '模型' }));
      expect(screen.getByRole('option', { name: /gone-model.*不可用/ })).toHaveAttribute('aria-disabled', 'true');
      fireEvent.keyDown(screen.getByRole('listbox', { name: '模型' }), { key: 'Escape' });
    }
    fireEvent.click(screen.getByRole('button', { name: '模型供应商' }));
    fireEvent.click(screen.getByRole('option', { name: 'Local' })); await flush(100);
    expect(patchCount).toBe(1);
    expect(latest()).not.toBe(socket); // Runtime PATCH still deliberately reconnects.
    await act(async () => latest().open());
    expect(latest().sent[0]).toMatchObject({ conversation_id: 'new', provider_id: 'local', model: 'local-model' });
  },
);

it.each([
  { name: 'model_unavailable', code: 'model_unavailable', message: '请求的模型当前不可用。' },
  { name: 'missing_key_without_code', message: '缺失 deepseek API Key。' },
  { name: 'missing_key_other_source', message: '缺失 deepseek API Key。', source: 'local' },
  { name: 'nonmatching_not_found_text', message: '请求的会话不存在或不属于当前世界线。 ' },
])('REWORK M2b: rejected pending auth $name restores source and preserves SYSTEM ERROR', async failure => {
  rows = [newRow, { ...oldRow, ...(failure.source === 'local' ? { provider_id: 'local', model_id: 'local-model' } : {}) }];
  await startOld(); FakeWebSocket.autoReady = false;
  await clickHistory('new'); await recover();
  const rejected = latest(); const sockets = FakeWebSocket.instances.length;
  act(() => rejected.emit({ type: 'error', code: failure.code, message: failure.message })); await flush(100);
  expect.soft(FakeWebSocket.instances).toHaveLength(sockets + 1);
  if (latest() !== rejected) await act(async () => { latest().open(); latest().ready(); });
  expect.soft(latest().sent[0]).toMatchObject({ conversation_id: 'old' });
  expect.soft(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  expect.soft(document.querySelector('.system-error-content')?.textContent).toBe(`SYSTEM ERROR: ${failure.message}`);
  expect.soft(screen.getByLabelText('输入消息')).toBeEnabled();
  sendLine('认证失败后仍在原对话');
  expect.soft(latest().chats).toEqual([{ content: '认证失败后仍在原对话', conversationId: 'old' }]);
});

it.each(['history', 'create'])(
  'REWORK S1: online %s keeps ONLINE and presence, commits on ACK with one auth', async action => {
    if (action === 'history') rows = [newRow, oldRow];
    await startOld(false); FakeWebSocket.autoSwitch = false;
    const socket = latest(); const sockets = FakeWebSocket.instances.length;
    const presence = document.querySelector('.ws2-presence')?.getAttribute('data-presence');
    if (action === 'history') await clickHistory('new');
    else { fireEvent.click(newButton()); await flush(300); }
    expect.soft(document.querySelector('.ws2-online')).toHaveTextContent('ONLINE');
    expect.soft(document.querySelector('.ws2-presence')).toHaveAttribute('data-presence', presence);
    expect(screen.getByLabelText('输入消息')).toBeDisabled();
    act(() => switchAck(socket)); await flush();
    expect.soft(FakeWebSocket.instances).toHaveLength(sockets);
    expect.soft(authCount()).toBe(1);
    expect.soft(active()).toBe('new');
    expect.soft(screen.getByLabelText('输入消息')).toBeEnabled();
    expect.soft(document.querySelector('.ws2-online')).toHaveTextContent('ONLINE');
    expect.soft(document.querySelector('.ws2-presence')).toHaveAttribute('data-presence', presence);
  },
);

it('REWORK S2: pending highlight stays on target, permits replacement and returns to source on cancel', async () => {
  rows = [newRow, { ...newRow, id: 'other', title: '另一段' }, oldRow];
  await startOld(false); FakeWebSocket.autoSwitch = false;
  await clickHistory('new');
  expect.soft(active()).toBe('new');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  await clickHistory('other'); expect.soft(active()).toBe('other');
  fireEvent.click(document.querySelector('[data-conversation-id="old"] .conversation-select')!); await flush(100);
  if (latest().readyState === 0) await act(async () => latest().open());
  expect(active()).toBe('old');
  expect(screen.getByText('旧对话内容')).toBeInTheDocument();
  expect(latest().sent.find(f => f.type === 'auth')).toMatchObject({ conversation_id: 'old' });
  expect(screen.getByLabelText('输入消息')).toBeEnabled();
});

it('REWORK S3: repeated create while CONNECTING reuses the attempt and authenticates its latest target', async () => {
  await startOld(false); act(() => latest().drop()); await flush(1000);
  const connecting = latest(); const sockets = FakeWebSocket.instances.length;
  FakeWebSocket.autoReady = false;
  fireEvent.click(newButton()); await flush(300);
  expect.soft(FakeWebSocket.instances).toHaveLength(sockets);
  fireEvent.click(newButton()); await flush(300);
  expect.soft(FakeWebSocket.instances).toHaveLength(sockets);
  expect.soft(latest()).toBe(connecting);
  await act(async () => latest().open());
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'new-2' });
  expect(screen.getByLabelText('输入消息')).toBeDisabled();
  await act(async () => latest().ready());
  expect(active()).toBe('new-2'); expect(screen.getByLabelText('输入消息')).toBeEnabled();
});

it('REWORK exact text: missing auth conversation refreshes once and never appends SYSTEM ERROR', async () => {
  rows = [newRow, oldRow]; await startOld(); FakeWebSocket.autoReady = false;
  await clickHistory('new'); await recover();
  const queries = listQueryCount; rows = [oldRow];
  act(() => latest().emit({ type: 'error', message: '请求的会话不存在或不属于当前世界线。' })); await flush(100);
  expect(listQueryCount).toBe(queries + 1);
  expect(document.querySelector('.system-error-content')).toBeNull();
  await act(async () => { latest().open(); latest().ready(); });
  expect(latest().sent[0]).toMatchObject({ conversation_id: 'old' });
  expect(active()).toBe('old'); expect(screen.getByLabelText('输入消息')).toBeEnabled();
});
