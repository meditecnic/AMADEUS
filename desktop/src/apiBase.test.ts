import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/core', () => ({ invoke }));
const payload = (token = 'synthetic-local-token') => ({
  version: 1, http_origin: 'http://127.0.0.1:8000', ws_origin: 'ws://127.0.0.1:8000', token,
});

beforeEach(() => { vi.resetModules(); invoke.mockReset(); });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

it('blocks HTTP and WS before bootstrap and shares a single pending initialization', async () => {
  let finish!: (value: unknown) => void;
  const fetch = vi.fn(() => new Promise(resolve => { finish = resolve; }));
  vi.stubGlobal('fetch', fetch);
  const api = await import('./apiBase');
  expect(() => api.apiFetch('/health')).toThrow('local_backend_not_initialized');
  expect(() => api.wsUrl('/ws/chat')).toThrow('local_backend_not_initialized');
  const first = api.initializeLocalBackend();
  expect(api.initializeLocalBackend()).toBe(first);
  expect(() => api.apiFetch('/health')).toThrow('local_backend_not_initialized');
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/__amadeus/bootstrap', {
    method: 'POST', headers: { 'X-Amadeus-Bootstrap': '1' }, cache: 'no-store',
  });
  finish({ ok: true, json: async () => payload() });
  await first;
  await api.initializeLocalBackend();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('routes packaged HTTP/assets and WS via official Tauri IPC with in-memory authentication', async () => {
  vi.stubEnv('DEV', false);
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  invoke.mockResolvedValue(payload());
  const fetch = vi.fn().mockResolvedValue({ ok: true });
  vi.stubGlobal('fetch', fetch);
  const store = vi.spyOn(Storage.prototype, 'setItem');
  const log = vi.spyOn(console, 'log');
  const error = vi.spyOn(console, 'error');
  const api = await import('./apiBase');
  await api.initializeLocalBackend();
  expect(invoke).toHaveBeenCalledExactlyOnceWith('get_local_backend');
  const controller = new AbortController();
  const init = { method: 'POST', body: '{}', signal: controller.signal, headers: { 'Content-Type': 'application/json' } };
  await api.apiFetch('/api/auth/salieri', init);
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe('http://127.0.0.1:8000/api/auth/salieri');
  expect(options).toMatchObject({ method: 'POST', body: '{}', signal: controller.signal });
  expect(new Headers(options.headers).get('Authorization')).toBe(`Bearer ${payload().token}`);
  expect(new Headers(options.headers).get('Content-Type')).toBe('application/json');
  expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
  expect(api.apiUrl('/api/asset-pack/manifest.json')).toBe('http://127.0.0.1:8000/api/asset-pack/manifest.json');
  expect(api.wsUrl('/ws/chat?session_id=test')).toBe('ws://127.0.0.1:8000/ws/chat?session_id=test');
  expect(api.wsProtocols()).toEqual(['amadeus.local.v1', `amadeus.token.${payload().token}`]);
  expect(url).not.toContain(payload().token);
  expect(api.wsUrl('/ws/chat')).not.toContain(payload().token);
  expect(store).not.toHaveBeenCalled();
  expect(log).not.toHaveBeenCalled();
  expect(error).not.toHaveBeenCalled();
});

it('permits an empty manual-development token and uses relative HTTP and same-origin WS', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...payload(''), http_origin: '', ws_origin: '' }) });
  vi.stubGlobal('fetch', fetch);
  const api = await import('./apiBase');
  await api.initializeLocalBackend();
  await api.apiFetch('/health');
  expect(fetch).toHaveBeenLastCalledWith('/health', undefined);
  expect(api.wsProtocols()).toEqual(['amadeus.local.v1']);
  expect(api.wsUrl('/ws/chat')).toBe(`ws://${window.location.host}/ws/chat`);
});

it('rejects an empty packaged token and retries a failed bootstrap without an address fallback', async () => {
  vi.stubEnv('DEV', false);
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  invoke.mockResolvedValueOnce(payload('')).mockRejectedValueOnce(new Error(payload().token)).mockResolvedValue(payload());
  const api = await import('./apiBase');
  await expect(api.initializeLocalBackend()).rejects.toThrow('无法连接本地服务');
  expect(() => api.apiUrl('/health')).toThrow('local_backend_not_initialized');
  await expect(api.initializeLocalBackend()).rejects.toThrow('无法连接本地服务');
  await api.initializeLocalBackend();
  expect(api.wsProtocols()).toHaveLength(2);
});
