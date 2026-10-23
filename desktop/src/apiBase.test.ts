import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

it('routes packaged Tauri REST, login, assets and WebSocket to the local backend', async () => {
  vi.stubEnv('DEV', false);
  vi.stubEnv('VITE_API_BASE', undefined);
  vi.stubEnv('VITE_WS_BASE', undefined);
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  const { apiUrl, wsUrl } = await import('./apiBase');

  expect({
    rest: apiUrl('/api/providers'),
    login: apiUrl('/api/auth/salieri'),
    asset: apiUrl('/api/asset-pack/manifest.json'),
    ws: wsUrl('/ws/chat?session_id=test'),
  }).toEqual({
    rest: 'http://127.0.0.1:8000/api/providers',
    login: 'http://127.0.0.1:8000/api/auth/salieri',
    asset: 'http://127.0.0.1:8000/api/asset-pack/manifest.json',
    ws: 'ws://127.0.0.1:8000/ws/chat?session_id=test',
  });
});
