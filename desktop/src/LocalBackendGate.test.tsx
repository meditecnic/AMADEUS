import { StrictMode, useEffect } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it('holds business components until bootstrap succeeds, shows an error and retries', async () => {
  vi.resetModules();
  const fetch = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValueOnce({
    ok: true, json: async () => ({ version: 1, http_origin: '', ws_origin: '', token: 'synthetic-gate-token' }),
  }).mockResolvedValue({ ok: true });
  vi.stubGlobal('fetch', fetch);
  const api = await import('./apiBase');
  const { LocalBackendGate } = await import('./LocalBackendGate');
  function Business() {
    useEffect(() => { void api.apiFetch('/health'); }, []);
    return <p>业务已启动</p>;
  }
  render(<StrictMode><LocalBackendGate><Business /></LocalBackendGate></StrictMode>);
  expect(screen.queryByText('业务已启动')).not.toBeInTheDocument();
  expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('业务已启动')).toBeInTheDocument();
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/health')).toBe(true));
  expect(fetch.mock.calls.filter(([url]) => url === '/__amadeus/bootstrap')).toHaveLength(2);
});

it('renders a recoverable error when packaged IPC supplies an empty token', async () => {
  vi.resetModules();
  vi.stubEnv('DEV', false);
  vi.stubGlobal('__TAURI_INTERNALS__', { invoke: vi.fn().mockResolvedValue({
    version: 1, http_origin: 'http://127.0.0.1:8000', ws_origin: 'ws://127.0.0.1:8000', token: '',
  }) });
  const { LocalBackendGate } = await import('./LocalBackendGate');
  render(<LocalBackendGate><p>业务已启动</p></LocalBackendGate>);
  expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
  expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  expect(screen.queryByText('业务已启动')).not.toBeInTheDocument();
});
