import { mockMemoryForTurn } from './chatMemory';
import { createDataStore, dataUnavailable, handleData, type Reply } from './data';
import { fixedTime, model, searchSettings } from './fixtures';
import { createMemoryStore, handleMemory } from './memory';
import { createProviderStore, handleProviders } from './providers';
import { scenarios, selectScenario, type Scenario } from './scenarios';

export type Frame = Record<string, unknown>;
const RECORDED_DELAY_MS = 1500;
type HistoryMessage = { role: string; content: string; translation?: string };
type Conversation = {
  id: string; title: string; title_source: string; is_default: boolean; is_pinned: boolean;
  provider_id: string; model_id: string; identity_mode: string; last_active_at: string; created_at: string;
  messages: HistoryMessage[];
};
const ok = (body: unknown, status = 200): Reply => ({ status, body });
const fail = (status: number, code: string): Reply => ok({ detail: { code } }, status);
const worldlineOf = (value: unknown) => value === 'beta' ? 'beta' : 'steins_gate';
const summary = ({ messages: _messages, ...conversation }: Conversation) => ({ ...conversation });

export function createMockRuntime() {
  const clients = new Map<string, {
    memory: ReturnType<typeof createMemoryStore>;
    conversations: Map<string, Conversation[]>;
    search: typeof searchSettings;
    data: ReturnType<typeof createDataStore>;
    providers: ReturnType<typeof createProviderStore>;
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
        // [210] the passcode lives with the client, so setting one on the system page holds at the next login
        data: createDataStore(scenarios[name].data, scenarios[name].login.passcodeSet ? scenarios[name].login.password : null),
      };
      clients.set(key, current);
    }
    return current;
  }

  function conversations(name: string, client: string, session: string, worldline: string) {
    const scopes = state(name, client).conversations;
    const key = JSON.stringify([session, worldline]);
    let rows = scopes.get(key);
    if (!rows) {
      const listed = scenarios[name].conversations.filter(row => row.worldline === worldline);
      rows = listed.map((row, index) => ({
        ...makeConversation(row.identity_mode), id: row.id, title: row.title,
        model_id: row.model_id ?? model.id,
        // listed newest first: the first row was opened last
        created_at: new Date(Date.parse(fixedTime) - (index + 1) * 3_600_000).toISOString(),
        messages: structuredClone(row.messages),
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

  function rest(url: URL, method: string, body: Frame = {}, client = 'default', cookies = ''): Reply {
    const reply = respond(url, method, body, client, cookies);
    const path = decodeURIComponent(url.pathname);
    const delay = scenarios[selectScenario(url, cookies)].delays.find(item => item.method === method && path.startsWith(item.pathPrefix));
    return delay ? { ...reply, delayMs: delay.ms } : reply;
  }

  function respond(url: URL, method: string, body: Frame, client: string, cookies: string): Reply {
    const name = selectScenario(url, cookies);
    const scenario = scenarios[name];
    const path = decodeURIComponent(url.pathname);
    const scriptedError = scenario.errors.find(error => error.method === method && path.startsWith(error.pathPrefix));
    if (scriptedError) return ok(scriptedError.body, scriptedError.status);

    // [210] [211] [212] [216] data, passcode, usage, export, location, diagnostics; a corrupt store closes the rest.
    const data = state(name, client).data;
    const handled = dataUnavailable(data, path) ?? handleData(url, method, body, data);
    if (handled) return handled;
    // [210] passcode login. No passcode by default; `login.passcodeSet` makes the scenario's password the passcode.
    if (path === '/api/auth/login' && method === 'POST') {
      const value = typeof body.passcode === 'string' ? body.passcode : null;
      const egg = value !== null && value.normalize('NFKD').replace(/\p{M}/gu, '').trim().toLowerCase().replace(/\s+/g, ' ') === 'gott wurfelt nicht';
      const accepted = !scenario.login.forceFailure && (data.passcode === null || value === data.passcode);
      return accepted ? ok({ status: 'success', easter_egg: egg }) : ok({ detail: { code: 'invalid_passcode' } }, 401);
    }
    if (path === '/api/auth/salieri' && method === 'POST') {
      const accepted = body.password === scenario.login.password && !scenario.login.forceFailure;
      return accepted ? ok({ ok: true }, scenario.login.successStatus)
        : ok({ detail: scenario.login.failureMessage }, scenario.login.failureStatus);
    }
    // Missing optional artwork is deliberate, preserving the app's original fallback.
    if (path.startsWith('/api/asset-pack/')) return fail(404, 'optional_asset_pack_absent');
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
    const match = path.match(/^\/api\/conversations\/([^/]+)(?:\/(messages|forget))?$/);
    const conversation = rows.find(row => row.id === match?.[1]);
    if (!conversation || !match) return fail(404, 'conversation_not_found');
    if (match[2] === 'messages' && method === 'GET') return ok(structuredClone(conversation.messages));
    if (match[2] === 'forget' && method === 'POST') {
      conversation.messages = [];
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
      return ok({ ok: true });
    }
    return fail(405, 'method_not_allowed');
  }

  function connect(url: URL, send: (frame: Frame) => void, client = 'default', cookies = '', closeTransport?: () => void) {
    const name = selectScenario(url, cookies);
    const scenario: Scenario = scenarios[name];
    const session = url.searchParams.get('session_id') || 'default';
    let worldline = 'steins_gate';
    let conversationId: string | null = null;
    let identityMode = 'okabe';
    let authenticated = false;
    let authPending = false;
    let selectedModel = model.id;
    let closed = false;
    let generation = 0;
    let activeTurn: Frame | null = null;
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
      activeTurn = null;
    };
    const currentRows = () => conversations(name, client, session, worldline);
    const cancel = () => {
      clearTimers();
      if (activeTurn) emit({ type: 'turn.cancelled', ...activeTurn });
      activeTurn = null;
    };

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
        cancel();
        const target = currentRows().find(row => row.id === frame.conversation_id);
        if (!target) {
          emit({ type: 'conversation.switch_error', request_id: frame.request_id, conversation_id: frame.conversation_id, code: 'conversation_not_found' });
        } else {
          conversationId = target.id;
          emit({ type: 'conversation.switched', request_id: frame.request_id, conversation_id: conversationId, worldline, generation: ++generation });
        }
      } else if (frame.type === 'worldline.switch') {
        cancel();
        const target = worldlineOf(frame.target_worldline);
        schedule(scenario.shift.delayMs, () => {
          if (scenario.shift.fail) {
            emit({ type: 'worldline.switch_error', request_id: frame.request_id, worldline: target, code: scenario.shift.code, message: scenario.shift.message });
          } else {
            worldline = target;
            conversationId = null;
            emit({ type: 'worldline.switched', request_id: frame.request_id, worldline, generation: ++generation });
          }
        });
      } else if (frame.type === 'turn.cancel') {
        cancel();
      } else if (frame.type === 'clear_history') {
        cancel();
        const conversation = currentRows().find(row => row.id === conversationId);
        if (conversation) conversation.messages = [];
      } else if (frame.type === 'chat.send' && typeof frame.content === 'string' && frame.content.trim()) {
        if (activeTurn || scenario.chatError) {
          emit({ type: 'error', message: scenario.chatError || '上一问仍在生成。', recoverable: true });
          return;
        }
        let conversation = currentRows().find(row => row.id === conversationId);
        if (!conversation) {
          conversation = makeConversation(identityMode);
          conversation.model_id = selectedModel;
          currentRows().unshift(conversation);
          conversationId = conversation.id;
        }
        const assistant: HistoryMessage = { role: 'assistant', content: '', translation: '' };
        conversation.messages.push({ role: 'user', content: frame.content.trim() }, assistant);
        activeTurn = { turn_id: `mock-turn-${++counter}`, generation: ++generation, conversation_id: conversationId };
        const turn = activeTurn;
        emit({ type: 'turn.started', ...turn });
        let elapsed = 0;
        scenario.dialogue.sentences.forEach((sentence, segmentId) => {
          elapsed += sentence.delayMs;
          schedule(elapsed, () => {
            assistant.content += sentence.ja;
            assistant.translation += sentence.zh;
            emit({ type: 'segment.ready', ...turn, segment_id: segmentId, ja: sentence.ja, zh: sentence.zh, emotion: sentence.emotion });
          });
        });
        const memory = mockMemoryForTurn(frame.content, worldline);
        schedule(elapsed + scenario.dialogue.completionDelayMs, () => {
          if (memory.referenced) emit({ type: 'memory.referenced', ...turn, items: memory.referenced });
          emit({ type: 'turn.completed', ...turn });
          activeTurn = null;
        });
        // Ingestion runs after the reply, like the real job; a cancel or switch before it fires drops it.
        if (memory.recorded) {
          schedule(elapsed + scenario.dialogue.completionDelayMs + RECORDED_DELAY_MS, () => {
            emit({
              type: 'memory.recorded', conversation_id: turn.conversation_id, turn_id: turn.turn_id,
              source_message_id: counter, changes: memory.recorded,
            });
          });
        }
      }
      // voice.stop / tts_debug intentionally emit no audio in this silent mock.
    }
    return { receive, close };
  }

  return { rest, connect, dispose() { clients.clear(); } };
}
