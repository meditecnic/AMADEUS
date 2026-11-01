import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage = null;
  constructor(public url: string, public protocols: string[]) { Socket.instances.push(this); }
  send() {}
  close() { this.readyState = 3; }
}

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); Socket.instances = []; });

it.each(['synthetic-ws-token', ''])('constructs the WS with bootstrap subprotocols and never puts credentials in storage/URL (token=%s)', async token => {
  vi.resetModules();
  vi.useFakeTimers();
  const store = vi.spyOn(Storage.prototype, 'setItem');
  const log = vi.spyOn(console, 'log').mockImplementation(() => undefined);
  const errorLog = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ version: 1, http_origin: '', ws_origin: '', token }) });
  vi.stubGlobal('fetch', fetch);
  vi.stubGlobal('WebSocket', Socket);
  const { initializeLocalBackend } = await import('../apiBase');
  await initializeLocalBackend();
  const { useAmadeusWS } = await import('./useAmadeusWS');
  const hook = renderHook(() => useAmadeusWS({
    sessionId: 'auth-test', systemPrompt: '', temperature: 0.8, worldline: 'steins_gate',
    enableTts: false, sovitsUrl: '', model: 'mock', reasoningEffort: null,
  }));
  expect(Socket.instances).toHaveLength(1);
  const socket = Socket.instances[0];
  expect(socket.protocols).toEqual(['amadeus.local.v1', ...(token ? [`amadeus.token.${token}`] : [])]);
  expect(socket.url).toBe(`ws://${window.location.host}/ws/chat?session_id=auth-test`);
  act(() => socket.onerror?.({ currentTarget: { protocols: socket.protocols } }));
  expect(errorLog.mock.calls).toEqual([['WebSocket Error']]);
  act(() => socket.onclose?.({ code: 4401 }));
  expect(hook.result.current.reconnectStopReason).toBe('unauthorized');
  act(() => vi.advanceTimersByTime(120000));
  expect(Socket.instances).toHaveLength(1);
  expect(store).not.toHaveBeenCalled();
  if (token) expect(JSON.stringify(log.mock.calls)).not.toContain(token);
  hook.unmount();
});
