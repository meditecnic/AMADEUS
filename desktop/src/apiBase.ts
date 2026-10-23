export const isPackagedTauri = !import.meta.env.DEV && '__TAURI_INTERNALS__' in window;

export const apiBase = (
  import.meta.env.VITE_API_BASE ?? (isPackagedTauri ? 'http://127.0.0.1:8000' : '')
).replace(/\/+$/, '');

export const wsBase = (
  import.meta.env.VITE_WS_BASE ?? (isPackagedTauri
    ? 'ws://127.0.0.1:8000'
    : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`)
).replace(/\/+$/, '');

export function apiUrl(path: string): string {
  return `${apiBase}${path}`;
}

export function wsUrl(path: string): string {
  return `${wsBase}${path}`;
}
