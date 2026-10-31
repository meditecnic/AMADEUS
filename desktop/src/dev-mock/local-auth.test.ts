// @vitest-environment node
import { once } from 'node:events';
import { request } from 'node:http';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createServer, type ViteDevServer } from 'vite';
import { WebSocket } from 'ws';
import { localBootstrapPlugin } from './vite-plugin-bootstrap';
import { devMockPlugin } from './vite-plugin-mock';
import { MOCK_LOCAL_TOKEN } from './auth';

let server: ViteDevServer;
let origin: string;
beforeEach(async () => {
  vi.stubEnv('AMADEUS_DEV_MOCK', '1');
  server = await createServer({ configFile: false, plugins: [localBootstrapPlugin(), devMockPlugin()],
    server: { host: '127.0.0.1', port: 0 }, logLevel: 'silent' });
  await server.listen();
  const address = server.httpServer!.address() as { port: number };
  origin = `http://127.0.0.1:${address.port}`;
});
afterEach(async () => { await server?.close(); vi.unstubAllEnvs(); });

it.each([
  ['POST', {}, undefined],
  ['POST', { 'X-Amadeus-Bootstrap': '0' }, undefined],
  ['POST', { 'X-Amadeus-Bootstrap': '1', 'Sec-Fetch-Site': 'cross-site' }, undefined],
  ['POST', { 'X-Amadeus-Bootstrap': '1', 'Sec-Fetch-Site': 'same-site' }, undefined],
  ['POST', { 'X-Amadeus-Bootstrap': '1' }, 'evil.example:1420'],
  ['POST', { 'X-Amadeus-Bootstrap': '1' }, '127.0.0.1.evil.example:1420'],
  ['POST', { 'X-Amadeus-Bootstrap': '1' }, 'localhost'],
  ['GET', { 'X-Amadeus-Bootstrap': '1' }, undefined],
  ['OPTIONS', { Origin: 'https://foreign.example', 'Access-Control-Request-Method': 'POST' }, undefined],
] as const)('rejects bootstrap method=%s headers=%j host=%s without disclosing a credential', async (method, headers, host) => {
  // Node 24 fetch ignores a caller-supplied Host; use the native HTTP client.
  const response = await new Promise<Response>((resolve, reject) => {
    const req = request(`${origin}/__amadeus/bootstrap`, {
      method, headers: { ...headers, ...(host ? { Host: host } : {}) },
    }, res => {
      const chunks: Buffer[] = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve(new Response(Buffer.concat(chunks).toString(), {
        status: res.statusCode, headers: res.headers as Record<string, string>,
      })));
    });
    req.on('error', reject);
    req.end();
  });
  expect(response.status).toBe(403);
  expect(await response.text()).not.toContain(MOCK_LOCAL_TOKEN);
  expect(response.headers.get('Cache-Control')).toBe('no-store');
  expect(response.headers.get('Access-Control-Allow-Origin')).toBeNull();
});

it.each(['same-origin', undefined])('returns a same-origin mock bootstrap without CORS (site=%s)', async site => {
  const response = await fetch(`${origin}/__amadeus/bootstrap`, { method: 'POST', headers: {
    'X-Amadeus-Bootstrap': '1', ...(site ? { 'Sec-Fetch-Site': site } : {}), Origin: origin,
  } });
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ version: 1, http_origin: '', ws_origin: '', token: MOCK_LOCAL_TOKEN });
  expect(response.headers.get('Cache-Control')).toBe('no-store');
  expect(response.headers.get('Access-Control-Allow-Origin')).toBeNull();
});

it('requires the bootstrap token on mock HTTP requests', async () => {
  for (const headers of [{}, { Authorization: 'Bearer wrong' }]) {
    const response = await fetch(`${origin}/api/providers`, { headers });
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: { code: 'local_auth_required' } });
  }
  const response = await fetch(`${origin}/api/providers`, { headers: { Authorization: `Bearer ${MOCK_LOCAL_TOKEN}` } });
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual(expect.any(Array));
});

it.each([[], ['amadeus.local.v1'], ['amadeus.local.v1', 'amadeus.token.wrong']])('closes unauthenticated mock WS with 4401 (protocols=%j)', async protocols => {
  const ws = new WebSocket(`${origin.replace('http:', 'ws:')}/ws/chat`, protocols);
  const [code] = await once(ws, 'close');
  expect(code).toBe(4401);
});

it('authenticates mock WS and only echoes the public subprotocol', async () => {
  const ws = new WebSocket(`${origin.replace('http:', 'ws:')}/ws/chat`, ['amadeus.local.v1', `amadeus.token.${MOCK_LOCAL_TOKEN}`]);
  await once(ws, 'open');
  expect(ws.protocol).toBe('amadeus.local.v1');
  const received = once(ws, 'message');
  ws.send(JSON.stringify({ type: 'auth', worldline: 'steins_gate' }));
  const [raw] = await received;
  expect(JSON.parse(raw.toString()).type).toBe('session.ready');
  ws.close();
  await once(ws, 'close');
});
