import { mockMemoryForTurn } from './chatMemory';
import { createAssetPackStore, handleAssetPack, type AssetPackRequestMeta } from './assetPackMock';
import { createDataStore, dataUnavailable, handleData, type Reply } from './data';
import { fixedTime, model, searchSettings } from './fixtures';
import { createMemoryStore, handleMemory } from './memory';
import { createProviderStore, handleProviders } from './providers';
import { scenarios, selectScenario, type Scenario } from './scenarios';

export type Frame = Record<string, unknown>;
const RECORDED_DELAY_MS = 1500;
type HistoryMessage = { id?: number; turn_id?: string; role: string; content: string; translation?: string };
type Conversation = {
  id: string; title: string; title_source: string; is_default: boolean; is_pinned: boolean;
  provider_id: string; model_id: string; identity_mode: string; last_active_at: string; created_at: string;
  messages: HistoryMessage[];
};
type MockTurn = {
  id: string;
  worldline: 'steins_gate' | 'beta';
  conversation: Conversation;
  content: string;
  queued: boolean;
  generation?: number;
  hidden?: boolean;
  reported?: boolean;
};
const ok = (body: unknown, status = 200): Reply => ({ status, body });
const fail = (status: number, code: string): Reply => ok({ detail: { code } }, status);
const worldlineOf = (value: unknown) => value === 'beta' ? 'beta' : 'steins_gate';
const summary = ({ messages: _messages, ...conversation }: Conversation) => ({ ...conversation });

/** Synthesize a tiny valid silent PCM WAV (mono 16-bit) as base64 for dev-mock replay. */
function makeMockSilentWavBase64(durationSec = 0.8, sampleRate = 16000): string {
  const numSamples = Math.round(sampleRate * durationSec);
  const dataSize = numSamples * 2;
  const buf = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buf);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i += 1) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, dataSize, true);
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
const MOCK_WAV_B64 = makeMockSilentWavBase64(0.8);
const SILENT_WAV_CACHE = new Map<number, string>();
function replayWavFor(durationSec?: number): string {
  if (!durationSec) return MOCK_WAV_B64;
  let cached = SILENT_WAV_CACHE.get(durationSec);
  if (!cached) {
    cached = makeMockSilentWavBase64(durationSec, 8000);
    SILENT_WAV_CACHE.set(durationSec, cached);
  }
  return cached;
}

export function createMockRuntime(options: { assetPackMovable?: boolean } = {}) {
  const clients = new Map<string, {
    memory: ReturnType<typeof createMemoryStore>;
    conversations: Map<string, Conversation[]>;
    search: typeof searchSettings;
    data: ReturnType<typeof createDataStore>;
    providers: ReturnType<typeof createProviderStore>;
    assetPack: ReturnType<typeof createAssetPackStore>;
    busyConversations: Set<string>;
    replayCache: Map<string, 'original' | 'synthesized'>;
    nextMessageId: number;
  }>();
  let counter = 0;

  function state(name: string, client: string) {
    const key = JSON.stringify([client, name]);
    let current = clients.get(key);
    if (!current) {
      current = {
        memory: createMemoryStore(scenarios[name].memory),
        conversations: new Map(),
        search: structuredClone(searchSettings),
        providers: createProviderStore(),
        assetPack: createAssetPackStore(options.assetPackMovable ?? true),
        // [210] the passcode lives with the client, so setting one on the system page holds at the next login
        data: createDataStore(scenarios[name].data, scenarios[name].login.passcodeSet ? scenarios[name].login.password : null),
        busyConversations: new Set(),
        replayCache: new Map(),
        nextMessageId: 100,
      };
      clients.set(key, current);
    }
    return current;
  }

  function conversations(name: string, client: string, session: string, worldline: string) {
    const clientState = state(name, client);
    const scopes = clientState.conversations;
    const key = JSON.stringify([session, worldline]);
    let rows = scopes.get(key);
    if (!rows) {
      const listed = scenarios[name].conversations.filter(row => row.worldline === worldline);
      rows = listed.map((row, index) => ({
        ...makeConversation(row.identity_mode), id: row.id, title: row.title,
        model_id: row.model_id ?? model.id,
        // listed newest first: the first row was opened last
        created_at: new Date(Date.parse(fixedTime) - (index + 1) * 3_600_000).toISOString(),
        messages: structuredClone(row.messages).map((msg: HistoryMessage) => ({
          ...msg,
          id: msg.id ?? ++clientState.nextMessageId,
        })),
      }));
      scopes.set(key, rows);
    }
    return rows;
  }

  function makeConversation(identityMode = 'okabe'): Conversation {
    return {
      id: `mock-conversation-${++counter}`, title: '新会话', title_source: 'auto',
      is_default: false, is_pinned: false, provider_id: 'deepseek', model_id: model.id,
      identity_mode: identityMode, last_active_at: fixedTime, created_at: new Date().toISOString(), messages: [],
    };
  }

  function rest(
    url: URL,
    method: string,
    body: Frame = {},
    client = 'default',
    cookies = '',
    meta?: AssetPackRequestMeta,
  ): Reply {
    const reply = respond(url, method, body, client, cookies, meta);
    const path = decodeURIComponent(url.pathname);
    const delay = scenarios[selectScenario(url, cookies)].delays.find(item => item.method === method && path.startsWith(item.pathPrefix));
    return delay ? { ...reply, delayMs: delay.ms } : reply;
  }

  function respond(
    url: URL,
    method: string,
    body: Frame,
    client: string,
    cookies: string,
    meta?: AssetPackRequestMeta,
  ): Reply {
    const name = selectScenario(url, cookies);
    const scenario = scenarios[name];
    const path = decodeURIComponent(url.pathname);
    const scriptedError = scenario.errors.find(error => error.method === method && path.startsWith(error.pathPrefix));
    if (scriptedError) return ok(scriptedError.body, scriptedError.status);

    // [210] [211] [212] [216] data, passcode, usage, export, location, diagnostics; a corrupt store closes the rest.
    const clientState = state(name, client);
    const data = clientState.data;
    const unavail = dataUnavailable(data, path);
    if (unavail) return unavail;
    if (
      data.status === 'corrupt' &&
      ((path === '/api/data/asset-pack/install' && method === 'POST') ||
        (path === '/api/data/asset-pack' && method === 'DELETE'))
    ) {
      return fail(409, 'data_unavailable');
    }
    const handled = handleAssetPack(url, method, body, clientState.assetPack, meta) ?? handleData(url, method, body, data);
    if (handled) return handled;
    // [210] passcode login. No passcode by default; `login.passcodeSet` makes the scenario's password the passcode.
    if (path === '/api/auth/login' && method === 'POST') {
      const value = typeof body.passcode === 'string' ? body.passcode : null;
      const egg = value !== null && value.normalize('NFKD').replace(/\p{M}/gu, '').trim().toLowerCase().replace(/\s+/g, ' ') === 'gott wurfelt nicht';
      const accepted = !scenario.login.forceFailure && (data.passcode === null || value === data.passcode);
      return accepted ? ok({ status: 'success', easter_egg: egg }) : ok({ detail: { code: 'invalid_passcode' } }, 401);
    }
    if (path === '/health') return ok({ ok: true, mode: 'dev-mock' });
    if (path === '/health/dependencies') return ok({ dependencies: { tts: { ok: false } } });
    if (path.startsWith('/api/providers')) {
      const reply = handleProviders(path, method, body, state(name, client).providers, scenario.models);
      if (reply) return reply;
    }
    if (path === '/api/search/settings') {
      const search = state(name, client).search;
      if (method === 'PATCH' && (body.active_provider === 'tavily' || body.active_provider === 'firecrawl')) {
        search.active_provider = body.active_provider;
      }
      return method === 'GET' || method === 'PATCH' ? ok(structuredClone(search)) : fail(405, 'method_not_allowed');
    }
    // Credentials are never retained, echoed, or sent to a real service.
    if (path === '/api/search/credential' && ['PUT', 'DELETE'].includes(method)) {
      return ok(structuredClone(state(name, client).search));
    }
    if (path === '/api/worldlines/summary') {
      if (method !== 'GET') return fail(405, 'method_not_allowed');
      const sessionId = url.searchParams.get('session_id')?.trim();
      if (!sessionId) return fail(422, 'session_id_required');
      const result: Record<string, { conversations: number; memories: number; last_active_at: string | null }> = {};
      for (const wl of ['steins_gate', 'beta'] as const) {
        const convRows = conversations(name, client, sessionId, wl);
        let memories = 0;
        for (const mode of ['self', 'okabe'] as const) {
          const memScope = clientState.memory.scope(JSON.stringify([sessionId, wl, mode]), wl);
          memories += memScope.facts.length + memScope.experiences.length;
        }
        const lastActiveAt = convRows.map(row => row.last_active_at || '').filter(Boolean).sort().pop() ?? null;
        result[wl] = {
          conversations: convRows.length,
          memories,
          last_active_at: lastActiveAt,
        };
      }
      return ok(result);
    }
    if (path.startsWith('/api/memory/')) return handleMemory(url, method, body, state(name, client).memory)!;
    if (!path.startsWith('/api/conversations')) return fail(404, 'mock_route_not_found');

    const session = String(body.session_id ?? url.searchParams.get('session_id') ?? 'default');
    const worldline = worldlineOf(body.worldline ?? url.searchParams.get('worldline'));
    const rows = conversations(name, client, session, worldline);
    if (path === '/api/conversations') {
      if (method === 'GET') return ok(rows.map(summary));
      if (method === 'POST') {
        const conversation = makeConversation(body.identity_mode === 'self' ? 'self' : 'okabe');
        conversation.provider_id = String(body.provider_id ?? 'deepseek');
        conversation.model_id = String(body.model_id ?? model.id);
        rows.unshift(conversation);
        return ok(summary(conversation), 201);
      }
      return fail(405, 'method_not_allowed');
    }
    const replayMatch = path.match(/^\/api\/conversations\/([^/]+)\/messages\/(\d+)\/replay$/);
    if (replayMatch && method === 'POST') {
      const conversation = rows.find(row => row.id === replayMatch[1]);
      if (!conversation) return fail(404, 'conversation_not_found');
      const msgId = Number(replayMatch[2]);
      const targetMsg = conversation.messages.find((msg) => msg.id === msgId);
      if (!targetMsg || targetMsg.role !== 'assistant') return fail(404, 'message_not_found');
      const replayWav = replayWavFor(scenario.replayDurationSec);
      const cacheKey = `${conversation.id}:${msgId}`;
      const existing = clientState.replayCache.get(cacheKey);
      if (existing) {
        return ok({ message_id: msgId, source: existing, cached: true, segments: [replayWav] });
      }
      clientState.replayCache.set(cacheKey, 'synthesized');
      return { ...ok({ message_id: msgId, source: 'synthesized', cached: false, segments: [replayWav] }), delayMs: 120 };
    }
    const match = path.match(/^\/api\/conversations\/([^/]+)(?:\/(messages|forget|truncate))?$/);
    const conversation = rows.find(row => row.id === match?.[1]);
    if (!conversation || !match) return fail(404, 'conversation_not_found');
    if (match[2] === 'messages' && method === 'GET') return ok(structuredClone(conversation.messages));
    if (match[2] === 'truncate' && method === 'POST') {
      if (clientState.busyConversations.has(conversation.id)) return fail(409, 'conversation_busy');
      const fromId = Number(body.from_message_id);
      const expectedLastId = Number(body.expected_last_message_id);
      const lastId = conversation.messages.length ? Number(conversation.messages[conversation.messages.length - 1].id ?? 0) : 0;
      if (lastId !== expectedLastId) return fail(409, 'conversation_changed');
      const cutIndex = conversation.messages.findIndex((msg) => msg.id === fromId);
      if (cutIndex < 0 || conversation.messages[cutIndex].role !== 'user') return fail(422, 'invalid_truncate_point');
      const removed = conversation.messages.splice(cutIndex);
      removed.forEach((msg) => { if (msg.id) clientState.replayCache.delete(`${conversation.id}:${msg.id}`); });
      return ok({ messages_deleted: removed.length, summaries_deleted: 0, core_facts_deleted: 0, episodic_deleted: 0 });
    }
    const eraseConversationDiaries = (conversationId: string) => {
      for (const mode of ['self', 'okabe'] as const) {
        const memScope = clientState.memory.scope(JSON.stringify([session, worldline, mode]), worldline);
        for (const diary of memScope.diaries) {
          if (diary.conversation_id === conversationId && !diary.deleted) {
            diary.deleted = true;
            diary.body_ja = null;
            diary.body_zh = null;
            diary.verified_phrases_json = null;
          }
        }
      }
    };
    if (match[2] === 'forget' && method === 'POST') {
      conversation.messages = [];
      if (body.forget_long_term === true) eraseConversationDiaries(conversation.id);
      return ok({ ok: true });
    }
    if (!match[2] && method === 'PATCH') {
      for (const key of ['title', 'provider_id', 'model_id'] as const) {
        if (typeof body[key] === 'string') conversation[key] = body[key];
      }
      if (typeof body.is_pinned === 'boolean') conversation.is_pinned = body.is_pinned;
      return ok(summary(conversation));
    }
    if (!match[2] && method === 'DELETE') {
      rows.splice(rows.indexOf(conversation), 1);
      eraseConversationDiaries(conversation.id);
      // Optional query flag (backend default false); echoed so the delete dialog's choice is visible in the mock.
      return ok({ ok: true, forget_long_term: url.searchParams.get('forget_long_term') === 'true' });
    }
    return fail(405, 'method_not_allowed');
  }

  function connect(url: URL, send: (frame: Frame) => void, client = 'default', cookies = '', closeTransport?: () => void) {
    const name = selectScenario(url, cookies);
    const scenario: Scenario = scenarios[name];
    const session = url.searchParams.get('session_id') || 'default';
    let worldline: MockTurn['worldline'] = 'steins_gate';
    let conversationId: string | null = null;
    let identityMode = 'okabe';
    let authenticated = false;
    let authPending = false;
    let selectedModel = model.id;
    let closed = false;
    let generation = 0;
    const lanes = new Map<MockTurn['worldline'], MockTurn[]>();
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const lifecycleTimers = new Set<ReturnType<typeof setTimeout>>();
    const emit = (frame: Frame) => { if (!closed) send(frame); };
    const clearTimers = () => { timers.forEach(clearTimeout); timers.clear(); };
    const schedule = (delay: number, callback: () => void, lifecycle = false) => {
      const queue = lifecycle ? lifecycleTimers : timers;
      const timer = setTimeout(() => { queue.delete(timer); if (!closed) callback(); }, delay);
      queue.add(timer);
    };
    const close = () => {
      closed = true;
      clearTimers();
      lifecycleTimers.forEach(clearTimeout);
      lifecycleTimers.clear();
      for (const lane of lanes.values()) {
        for (const turn of lane) state(name, client).busyConversations.delete(turn.conversation.id);
      }
      lanes.clear();
    };
    const currentRows = () => conversations(name, client, session, worldline);
    const scope = (turn: MockTurn) => ({
      conversation_id: turn.conversation.id, turn_id: turn.id, generation: turn.generation,
    });
    const active = () => {
      const turn = lanes.get(worldline)?.[0];
      return turn?.generation !== undefined && turn.conversation.id === conversationId ? turn : undefined;
    };
    const hide = () => {
      const turn = active();
      if (turn && !turn.hidden) {
        turn.hidden = true;
        publish(turn);
      }
    };
    const publish = (turn: MockTurn) => {
      const matching = (lanes.get(turn.worldline) ?? []).filter(item => item.conversation === turn.conversation);
      const running = matching.some(item => item.generation !== undefined);
      const queued = matching.filter(item => item.generation === undefined).map(item => item.id);
      const id = turn.conversation.id;
      const busy = state(name, client).busyConversations;
      if (running || queued.length) busy.add(id);
      else busy.delete(id);
      if (turn.queued || turn.hidden || turn.reported) {
        matching.forEach(item => { item.reported = true; });
        emit({ type: 'conversation.turn_state', worldline: turn.worldline, conversation_id: id, running, queued });
      }
    };
    const remove = (turn: MockTurn) => {
      const lane = lanes.get(turn.worldline);
      if (!lane?.includes(turn)) return;
      lane.splice(lane.indexOf(turn), 1);
      publish(turn);
      const next = lane[0];
      if (next && next.generation === undefined) start(next);
    };

    function start(turn: MockTurn) {
      turn.generation = generation;
      turn.hidden = worldline !== turn.worldline || conversationId !== turn.conversation.id;
      publish(turn);
      const clientState = state(name, client);
      const userId = ++clientState.nextMessageId;
      const assistant: HistoryMessage = { turn_id: turn.id, role: 'assistant', content: '', translation: '' };
      turn.conversation.messages.push({ id: userId, turn_id: turn.id, role: 'user', content: turn.content });
      if (!turn.hidden) emit({ type: 'turn.started', ...scope(turn) });
      const running = () => lanes.get(turn.worldline)?.[0] === turn;
      let elapsed = 0;
      scenario.dialogue.sentences.forEach((sentence, segmentId) => {
        elapsed += sentence.delayMs;
        schedule(elapsed, () => {
          if (!running()) return;
          assistant.content += sentence.ja;
          assistant.translation += sentence.zh;
          if (!turn.hidden) {
            emit({ type: 'segment.ready', ...scope(turn), segment_id: segmentId, ja: sentence.ja, zh: sentence.zh, emotion: sentence.emotion });
          }
        });
      });
      const memory = mockMemoryForTurn(turn.content, turn.worldline);
      schedule(elapsed + scenario.dialogue.completionDelayMs, () => {
        if (!running()) return;
        assistant.id = ++clientState.nextMessageId;
        turn.conversation.messages.push(assistant);
        turn.conversation.last_active_at = new Date().toISOString();
        if (turn.hidden) {
          emit({ type: 'turn.history_saved', ...scope(turn) });
        } else {
          if (memory.referenced) emit({ type: 'memory.referenced', ...scope(turn), items: memory.referenced });
          clientState.replayCache.set(`${turn.conversation.id}:${assistant.id}`, 'original');
          emit({ type: 'turn.completed', ...scope(turn) });
        }
        remove(turn);
        if (memory.recorded) {
          schedule(RECORDED_DELAY_MS, () => {
            if (conversationId !== turn.conversation.id || worldline !== turn.worldline) return;
            emit({
              type: 'memory.recorded', conversation_id: turn.conversation.id, turn_id: turn.id,
              source_message_id: userId, changes: memory.recorded,
            });
          });
        }
      });
    }

    function receive(frame: Frame) {
      if (closed) return;
      if (frame.type === 'ping') { emit({ type: 'pong' }); return; }
      if (frame.type === 'auth') {
        if (authPending || authenticated) return;
        if (scenario.authError) { emit({ type: 'error', message: scenario.authError, recoverable: false }); return; }
        worldline = worldlineOf(frame.worldline);
        identityMode = frame.default_identity_mode === 'self' ? 'self' : 'okabe';
        conversationId = typeof frame.conversation_id === 'string' ? frame.conversation_id : null;
        selectedModel = typeof frame.model === 'string' ? frame.model : model.id;
        if (conversationId && !currentRows().some(row => row.id === conversationId)) {
          emit({ type: 'error', message: '会话不存在。', recoverable: false });
          return;
        }
        authPending = true;
        const ready = () => {
          authPending = false;
          authenticated = true;
          emit({
            type: 'session.ready', conversation_mode: conversationId ? 'history' : 'draft',
            conversation_id: conversationId, worldline, generation: ++generation,
          });
          if (scenario.transport.closeAfterReadyMs !== null) schedule(scenario.transport.closeAfterReadyMs, () => {
            close();
            closeTransport?.();
          }, true);
        };
        if (scenario.transport.readyDelayMs) schedule(scenario.transport.readyDelayMs, ready, true);
        else ready();
        return;
      }
      if (!authenticated) { emit({ type: 'error', message: '请先发送 auth。', recoverable: false }); return; }
      if (frame.type === 'config_update') {
        if (frame.default_identity_mode === 'self' || frame.default_identity_mode === 'okabe') identityMode = frame.default_identity_mode;
        emit({ type: 'config_applied', reasoning_effort: frame.reasoning_effort ?? null });
      } else if (frame.type === 'conversation.switch') {
        const target = currentRows().find(row => row.id === frame.conversation_id);
        if (!target) {
          emit({ type: 'conversation.switch_error', request_id: frame.request_id, conversation_id: frame.conversation_id, code: 'conversation_not_found' });
        } else {
          hide();
          conversationId = target.id;
          emit({ type: 'conversation.switched', request_id: frame.request_id, conversation_id: conversationId, worldline, generation: ++generation });
        }
      } else if (frame.type === 'worldline.switch') {
        const target = worldlineOf(frame.target_worldline);
        schedule(scenario.shift.delayMs, () => {
          if (scenario.shift.fail) {
            emit({ type: 'worldline.switch_error', request_id: frame.request_id, worldline: target, code: scenario.shift.code, message: scenario.shift.message });
          } else {
            hide();
            worldline = target;
            conversationId = null;
            emit({ type: 'worldline.switched', request_id: frame.request_id, worldline, generation: ++generation });
          }
        });
      } else if (frame.type === 'turn.cancel') {
        const turn = active();
        if (!turn) return;
        turn.conversation.messages.push({
          id: ++state(name, client).nextMessageId, turn_id: turn.id, role: 'assistant',
          content: '[EMO:disappointed] この質問は途中で止まった。もう一度送って。',
          translation: '这一问中断了，没有收到回复。请再发一次。',
        });
        turn.conversation.last_active_at = new Date().toISOString();
        emit({ type: 'turn.cancelled', ...scope(turn) });
        emit({ type: 'turn.history_saved', ...scope(turn) });
        remove(turn);
      } else if (frame.type === 'turn.withdraw') {
        for (const lane of lanes.values()) {
          const turn = lane.find(item => item.id === frame.task_id && item.generation === undefined);
          if (turn) remove(turn);
        }
      } else if (frame.type === 'chat.send' && typeof frame.content === 'string' && frame.content.trim()) {
        const trimmed = frame.content.trim();
        if (scenario.chatError && (!scenario.chatErrorTrigger || trimmed.includes(scenario.chatErrorTrigger))) {
          emit({ type: 'error', message: scenario.chatError, recoverable: true });
          return;
        }
        let conversation = currentRows().find(row => row.id === conversationId);
        if (!conversation) {
          conversation = makeConversation(identityMode);
          conversation.model_id = selectedModel;
          currentRows().unshift(conversation);
          conversationId = conversation.id;
        }
        const lane = lanes.get(worldline) ?? [];
        lanes.set(worldline, lane);
        const queued = lane.length > 0;
        const turn = { id: `mock-turn-${++counter}`, worldline, conversation, content: frame.content.trim(), queued };
        lane.push(turn);
        if (queued) {
          emit({ type: 'turn.accepted', task_id: turn.id, conversation_id: conversation.id, worldline, queued });
          publish(turn);
        } else {
          start(turn);
        }
      }
      // voice.stop / tts_debug intentionally emit no audio in this silent mock.
    }
    return { receive, close };
  }

  return { rest, connect, dispose() { clients.clear(); } };
}
