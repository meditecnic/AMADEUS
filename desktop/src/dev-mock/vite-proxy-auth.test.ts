// @vitest-environment node
import { createServer as createHTTPServer, request } from 'node:http';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { expect, it, vi } from 'vitest';
import { createServer } from 'vite';
import { WebSocket, WebSocketServer } from 'ws';

it('forwards HTTP authorization and both WS subprotocols through the real Vite proxy', async () => {
  const token = 'synthetic-proxy-token';
  const upstream = createHTTPServer((request, response) => {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ authenticated: request.headers.authorization === `Bearer ${token}` }));
  });
  const wss = new WebSocketServer({ server: upstream, handleProtocols: () => 'amadeus.local.v1' });
  let protocols = '';
  wss.on('connection', (ws, request) => {
    protocols = request.headers['sec-websocket-protocol'] || '';
    ws.send('proxy-authenticated');
  });
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  const port = (upstream.address() as { port: number }).port;
  vi.stubEnv('AMADEUS_DEV_MOCK', '0');
  vi.stubEnv('AMADEUS_DEV_LOCAL_API_TOKEN', token);
  vi.stubEnv('AMADEUS_BACKEND_ORIGIN', `http://127.0.0.1:${port}`);
  vi.stubEnv('TAURI_DEV_HOST', '');
  const vite = await createServer({
    configFile: fileURLToPath(new URL('../../vite.config.ts', import.meta.url)),
    server: { port: 0, strictPort: false }, logLevel: 'silent',
  });
  let socket: WebSocket | undefined;
  try {
    await vite.listen();
    const origin = `http://127.0.0.1:${(vite.httpServer!.address() as { port: number }).port}`;
    const bootstrap = await fetch(`${origin}/__amadeus/bootstrap`, { method: 'POST', headers: { 'X-Amadeus-Bootstrap': '1' } });
    expect(await bootstrap.json()).toEqual({ version: 1, http_origin: '', ws_origin: '', token });
    const response = await fetch(`${origin}/api/providers`, { headers: { Authorization: `Bearer ${token}` } });
    expect(await response.json()).toEqual({ authenticated: true });
    socket = new WebSocket(`${origin.replace('http:', 'ws:')}/ws/chat`, ['amadeus.local.v1', `amadeus.token.${token}`]);
    const [message] = await once(socket, 'message');
    expect(message.toString()).toBe('proxy-authenticated');
    expect(protocols.split(',').map(value => value.trim())).toEqual(['amadeus.local.v1', `amadeus.token.${token}`]);
    expect(socket.protocol).toBe('amadeus.local.v1');
    socket.close();
    await once(socket, 'close');
  } finally {
    socket?.terminate();
    await vite.close();
    wss.clients.forEach(ws => ws.terminate());
    wss.close();
    await new Promise<void>(resolve => upstream.close(() => resolve()));
    vi.unstubAllEnvs();
  }
});

it('supports a manual real-backend bootstrap with an empty token on localhost', async () => {
  vi.stubEnv('AMADEUS_DEV_MOCK', '0');
  vi.stubEnv('AMADEUS_DEV_LOCAL_API_TOKEN', '');
  const { localBootstrapPlugin } = await import('./vite-plugin-bootstrap');
  const vite = await createServer({ configFile: false, plugins: [localBootstrapPlugin()],
    server: { host: '127.0.0.1', port: 0 }, logLevel: 'silent' });
  try {
    await vite.listen();
    const port = (vite.httpServer!.address() as { port: number }).port;
    // Native HTTP is required: Node 24 fetch ignores a supplied Host header.
    const response = await new Promise<Response>((resolve, reject) => {
      const req = request(`http://127.0.0.1:${port}/__amadeus/bootstrap`, { method: 'POST',
        headers: { Host: `localhost:${port}`, 'X-Amadeus-Bootstrap': '1' } }, res => {
        const chunks: Buffer[] = [];
        res.on('data', chunk => chunks.push(chunk));
        res.on('end', () => resolve(new Response(Buffer.concat(chunks).toString(), {
          status: res.statusCode, headers: res.headers as Record<string, string>,
        })));
      });
      req.on('error', reject);
      req.end();
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ version: 1, http_origin: '', ws_origin: '', token: '' });
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.get('Access-Control-Allow-Origin')).toBeNull();
  } finally { await vite.close(); vi.unstubAllEnvs(); }
});
