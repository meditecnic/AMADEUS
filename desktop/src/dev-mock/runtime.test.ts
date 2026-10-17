// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createServer } from 'node:http';
import { createMockRuntime } from './runtime';
import { scenarios, selectScenario } from './scenarios';
import { devMockPlugin } from './vite-plugin-mock';

const url = (path: string) => new URL(path, 'http://localhost:1420');
const scope = '?session_id=test-session&worldline=steins_gate';

afterEach(() => vi.useRealTimers());

describe('dev mock REST contracts', () => {
  it('matches the actual login route, passphrase and catalog-defined HTTP failures', () => {
    const mock = createMockRuntime();
    expect(mock.rest(url('/api/auth/salieri'), 'POST', { password: 'Gott würfelt nicht' }).status).toBe(200);
    expect(mock.rest(url('/api/auth/salieri'), 'POST', { password: 'wrong' })).toEqual({
      status: 401, body: { detail: scenarios.streaming.login.failureMessage },
    });
    expect(mock.rest(url('/api/auth/salieri?scenario=login-failure'), 'POST', { password: 'Gott würfelt nicht' }).status).toBe(401);
  });

  it('serves usable provider/model shapes and safe absent-asset response', () => {
    const mock = createMockRuntime();
    expect(mock.rest(url('/api/providers'), 'GET').body).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'deepseek', configured: true, default_model: 'deepseek-chat' }),
    ]));
    expect(mock.rest(url('/api/providers/deepseek/models'), 'GET').body).toMatchObject({
      catalog: [expect.objectContaining({ id: 'deepseek-chat', callable: true })], id_only: false,
    });
    expect(mock.rest(url('/api/asset-pack/manifest.json'), 'GET').status).toBe(404);
    expect(mock.rest(url('/api/unimplemented'), 'GET').status).toBe(404);
  });

  it('keeps conversation mutations isolated by client, session, worldline and scenario', () => {
    const mock = createMockRuntime();
    const created = mock.rest(url('/api/conversations'), 'POST', {
      session_id: 'test-session', worldline: 'steins_gate', identity_mode: 'self',
      provider_id: 'deepseek', model_id: 'deepseek-chat',
    }, 'browser-a').body as { id: string };
    expect(mock.rest(url(`/api/conversations/${created.id}${scope}`), 'PATCH', { title: 'Renamed' }, 'browser-a').body)
      .toMatchObject({ title: 'Renamed', identity_mode: 'self' });
    const list = (query: string, client = 'browser-a') => mock.rest(url(`/api/conversations${query}`), 'GET', {}, client).body;
    expect(list(scope)).toEqual(expect.arrayContaining([expect.objectContaining({ id: created.id, title: 'Renamed' })]));
    expect(list(scope, 'browser-b')).not.toEqual(expect.arrayContaining([expect.objectContaining({ id: created.id })]));
    expect(list(scope.replace('test-session', 'another'))).not.toEqual(expect.arrayContaining([expect.objectContaining({ id: created.id })]));
    expect(list(scope.replace('steins_gate', 'beta'))).not.toEqual(expect.arrayContaining([expect.objectContaining({ id: created.id })]));
    expect(list(`${scope}&scenario=empty`)).toEqual([]);
    expect(mock.rest(url(`/api/conversations/${created.id}${scope}`), 'DELETE', {}, 'browser-a').status).toBe(200);
  });

  it('switches scenarios from query/cookies and defines empty/error/shift hooks', () => {
    expect(selectScenario(url('/?scenario=empty'), 'amadeus_mock_scenario=error')).toBe('empty');
    expect(selectScenario(url('/'), 'amadeus_mock_scenario=error')).toBe('error');
    expect(selectScenario(url('/?scenario=unknown'), '')).toBe('streaming');
    const mock = createMockRuntime();
    expect(mock.rest(url('/api/conversations?scenario=error'), 'GET').status).toBe(503);
    expect(scenarios['shift-failure'].shift.fail).toBe(true);
    expect(scenarios['shift-slow'].shift.delayMs).toBeGreaterThan(1000);
    expect(devMockPlugin().apply).toBe('serve');
  });

  it('removes its upgrade listener when Vite closes the plugin container', () => {
    const server = createServer(); // No listening port is opened.
    const plugin = devMockPlugin();
    const configure = plugin.configureServer as (server: unknown) => void;
    configure({ httpServer: server, middlewares: { use: vi.fn() } });
    expect(server.listenerCount('upgrade')).toBe(1);
    const close = plugin.closeBundle as () => void;
    expect(close).toBeTypeOf('function');
    close();
    expect(server.listenerCount('upgrade')).toBe(0);
    close(); // Cleanup is safe on both closeBundle and HTTP close.
  });
});

describe('dev mock protocol v2', () => {
  it('authenticates a draft, materializes on send, streams paired sentences in order and persists history', () => {
    vi.useFakeTimers();
    const mock = createMockRuntime();
    const frames: Record<string, unknown>[] = [];
    const socket = mock.connect(url('/ws/chat?session_id=test-session'), frame => frames.push(frame));
    socket.receive({ type: 'auth', worldline: 'steins_gate', conversation_mode: 'draft' });
    expect(frames[0]).toMatchObject({ type: 'session.ready', conversation_mode: 'draft', conversation_id: null });
    socket.receive({ type: 'chat.send', content: 'こんにちは' });
    expect(frames.map(frame => frame.type)).toEqual(['session.ready', 'turn.started']);
    const firstDelay = scenarios.streaming.dialogue.sentences[0].delayMs;
    vi.advanceTimersByTime(firstDelay - 1);
    expect(frames).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(frames[2]).toMatchObject({ type: 'segment.ready', segment_id: 0, ja: expect.any(String), zh: expect.any(String) });
    vi.runAllTimers();
    expect(frames.map(frame => frame.type)).toEqual([
      'session.ready', 'turn.started', ...scenarios.streaming.dialogue.sentences.map(() => 'segment.ready'), 'turn.completed',
    ]);
    const turn = frames[1];
    expect(frames.slice(2).every(frame => frame.turn_id === turn.turn_id && frame.generation === turn.generation)).toBe(true);
    const history = mock.rest(url(`/api/conversations/${turn.conversation_id}/messages${scope}`), 'GET').body;
    expect(history).toEqual([
      { role: 'user', content: 'こんにちは' },
      expect.objectContaining({ role: 'assistant', content: scenarios.streaming.dialogue.sentences.map(s => s.ja).join('') }),
    ]);
    socket.close();
  });

  it('correlates delayed/failed shifts and cancels pending stream timers without reconnecting', () => {
    vi.useFakeTimers();
    const mock = createMockRuntime();
    const frames: Record<string, unknown>[] = [];
    const failed = mock.connect(url('/ws/chat?scenario=shift-failure'), frame => frames.push(frame));
    failed.receive({ type: 'auth', worldline: 'steins_gate' });
    failed.receive({ type: 'worldline.switch', request_id: 'fail-1', target_worldline: 'beta' });
    vi.runAllTimers();
    expect(frames.at(-1)).toMatchObject({ type: 'worldline.switch_error', request_id: 'fail-1', worldline: 'beta' });
    failed.close();
    const slow = mock.connect(url('/ws?scenario=shift-slow'), frame => frames.push(frame));
    slow.receive({ type: 'auth', worldline: 'steins_gate' });
    slow.receive({ type: 'worldline.switch', request_id: 'slow-1', target_worldline: 'beta' });
    vi.advanceTimersByTime(scenarios['shift-slow'].shift.delayMs - 1);
    expect(frames.at(-1)?.type).toBe('session.ready');
    vi.advanceTimersByTime(1);
    expect(frames.at(-1)).toMatchObject({ type: 'worldline.switched', request_id: 'slow-1', worldline: 'beta' });
    slow.receive({ type: 'chat.send', content: 'test' });
    slow.receive({ type: 'turn.cancel' });
    const length = frames.length;
    vi.runAllTimers();
    expect(frames).toHaveLength(length);
    expect(frames.at(-1)?.type).toBe('turn.cancelled');
    slow.receive({ type: 'ping' });
    expect(frames.at(-1)?.type).toBe('pong');
    slow.close();
  });

  it('cleans up closed sockets and rejects cross-worldline history switches', () => {
    vi.useFakeTimers();
    const mock = createMockRuntime();
    const frames: Record<string, unknown>[] = [];
    const socket = mock.connect(url('/ws/chat?session_id=test-session'), frame => frames.push(frame));
    socket.receive({ type: 'auth', worldline: 'steins_gate' });
    socket.receive({ type: 'conversation.switch', request_id: 'switch-1', conversation_id: 'missing' });
    expect(frames.at(-1)).toMatchObject({ type: 'conversation.switch_error', request_id: 'switch-1' });
    socket.receive({ type: 'chat.send', content: 'test' });
    socket.close();
    const length = frames.length;
    vi.runAllTimers();
    expect(frames).toHaveLength(length);
    expect(vi.getTimerCount()).toBe(0);
  });
});
