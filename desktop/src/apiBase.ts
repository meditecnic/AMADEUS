import { invoke } from '@tauri-apps/api/core';

interface LocalBackend {
  version: 1;
  http_origin: string;
  ws_origin: string;
  token: string;
}

export const isPackagedTauri = !import.meta.env.DEV && '__TAURI_INTERNALS__' in window;
let backend: LocalBackend | undefined;
let initialization: Promise<void> | undefined;

/** One successful bootstrap per page lifetime; a failed attempt can be retried. */
export function initializeLocalBackend(): Promise<void> {
  if (initialization) return initialization;
  initialization = (async () => {
    let value: LocalBackend;
    if ('__TAURI_INTERNALS__' in window) {
      value = await invoke<LocalBackend>('get_local_backend');
    } else {
      const response = await fetch('/__amadeus/bootstrap', {
        method: 'POST', headers: { 'X-Amadeus-Bootstrap': '1' }, cache: 'no-store',
      });
      if (!response.ok) throw new Error('bootstrap_failed');
      value = await response.json();
    }
    if (!value || value.version !== 1 || typeof value.http_origin !== 'string'
      || typeof value.ws_origin !== 'string' || typeof value.token !== 'string'
      || (value.token !== '' && !/^[A-Za-z0-9_-]+$/.test(value.token))
      || (isPackagedTauri && value.token === '')) {
      throw new Error('invalid_local_backend');
    }
    backend = value;
  })().catch(() => {
    initialization = undefined;
    // Never render or log IPC/response data, which may contain the token.
    throw new Error('无法连接本地服务');
  });
  return initialization;
}

function localBackend(): LocalBackend {
  if (!backend) throw new Error('local_backend_not_initialized');
  return backend;
}

export function apiUrl(path: string): string {
  return `${localBackend().http_origin}${path}`;
}

export function apiFetch(path: string, init?: RequestInit, fetcher: typeof fetch = fetch): Promise<Response> {
  const { token } = localBackend();
  if (token) {
    const headers = new Headers(init?.headers);
    headers.set('Authorization', `Bearer ${token}`);
    init = { ...init, headers };
  }
  return fetcher(apiUrl(path), init);
}

export function wsUrl(path: string): string {
  const origin = localBackend().ws_origin
    || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
  return `${origin}${path}`;
}

export function wsProtocols(): string[] {
  const { token } = localBackend();
  return ['amadeus.local.v1', ...(token ? [`amadeus.token.${token}`] : [])];
}
