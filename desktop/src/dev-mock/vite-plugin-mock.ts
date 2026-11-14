import fs from 'node:fs';
import path from 'node:path';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocketServer, WebSocket } from 'ws';
import type { Plugin } from 'vite';
import { MOCK_LOCAL_TOKEN } from './auth';
import { installScenarioBridge } from './client-bridge';
import { createMockRuntime, type Frame } from './runtime';
import type { AssetPackRequestMeta } from './assetPackMock';
import { readCookie, scenarios, selectScenario } from './scenarios';

const PACK_TYPES: Record<string, string> = { '.png': 'image/png', '.ogg': 'audio/ogg', '.json': 'application/json' };
const MAX_JSON_BYTES = 65_536;
const MAX_ZIP_BYTES = 64 * 1024 * 1024;

async function readPayload(
  request: IncomingMessage,
  allowBinaryZip: boolean,
): Promise<{ body: Frame; meta?: AssetPackRequestMeta }> {
  const contentType = String(request.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
  const isBinary = contentType === 'application/zip' || contentType === 'application/octet-stream';
  const maxBytes = allowBinaryZip && isBinary ? MAX_ZIP_BYTES : MAX_JSON_BYTES;
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buf.byteLength;
    if (size > maxBytes) throw new Error('body_too_large');
    chunks.push(buf);
  }
  const raw = Buffer.concat(chunks);
  if (allowBinaryZip && isBinary) {
    return { body: {}, meta: { rawBody: new Uint8Array(raw), contentType } };
  }
  if (allowBinaryZip && contentType && contentType !== 'application/json') {
    return { body: {}, meta: { contentType } };
  }
  if (raw.byteLength === 0) {
    return { body: {}, ...(contentType ? { meta: { contentType } } : {}) };
  }
  const body: unknown = JSON.parse(raw.toString('utf8'));
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('invalid_body');
  return { body: body as Frame, ...(contentType ? { meta: { contentType } } : {}) };
}

export function devMockPlugin(): Plugin {
  let cleanup: (() => void) | undefined;
  return {
    name: 'amadeus-dev-mock',
    apply: 'serve',
    closeBundle() { cleanup?.(); },
    config(config) {
      // Proxy upgrade listeners must not race the same-server /ws handler.
      const proxy = config.server?.proxy;
      if (proxy) for (const route of ['/api', '/ws', '/health']) delete proxy[route];
    },
    transformIndexHtml() {
      return [{
        tag: 'script', injectTo: 'head-prepend',
        children: `;(${installScenarioBridge.toString()})(${JSON.stringify(Object.keys(scenarios))});`,
      }];
    },
    configureServer(server) {
      if (!server.httpServer) throw new Error('dev-mock requires Vite HTTP server mode');
      const runtime = createMockRuntime({ assetPackMovable: !process.env.AMADEUS_ASSET_PACK_DIR });
      const wss = new WebSocketServer({ noServer: true, maxPayload: 65_536,
        handleProtocols: protocols => protocols.has('amadeus.local.v1') ? 'amadeus.local.v1' : false,
      });
      const sockets = new Map<WebSocket, ReturnType<typeof runtime.connect>>();
      const upgrade = (request: IncomingMessage, socket: Duplex, head: Buffer) => {
        const url = new URL(request.url || '/', 'http://localhost');
        if (url.pathname !== '/ws' && url.pathname !== '/ws/chat') return;
        wss.handleUpgrade(request, socket, head, ws => {
          const protocols = (request.headers['sec-websocket-protocol'] || '').split(',').map(value => value.trim());
          if (!protocols.includes('amadeus.local.v1') || !protocols.includes(`amadeus.token.${MOCK_LOCAL_TOKEN}`)) {
            ws.close(4401, 'local auth required');
            return;
          }
          const cookies = request.headers.cookie || '';
          const connection = runtime.connect(url, frame => {
            if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
          }, readCookie(cookies, 'amadeus_mock_client') || 'default', cookies, () => ws.close(1012, 'Mock transport interruption'));
          sockets.set(ws, connection);
          ws.on('message', raw => {
            try {
              const frame: unknown = JSON.parse(raw.toString());
              if (!frame || typeof frame !== 'object' || Array.isArray(frame)) throw new Error('invalid_frame');
              connection.receive(frame as Frame);
            } catch {
              if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'error', message: 'Invalid mock frame', recoverable: true }));
            }
          });
          ws.on('error', () => { connection.close(); });
          ws.on('close', () => { connection.close(); sockets.delete(ws); });
        });
      };
      server.httpServer.on('upgrade', upgrade);
      let disposed = false;
      cleanup = () => {
        if (disposed) return;
        disposed = true;
        server.httpServer?.off('upgrade', upgrade);
        server.httpServer?.off('close', cleanup!);
        sockets.forEach((connection, ws) => { connection.close(); ws.terminate(); });
        sockets.clear();
        wss.close();
        runtime.dispose();
      };
      server.httpServer.once('close', cleanup);
      server.middlewares.use(async (request, response, next) => {
        const url = new URL(request.url || '/', 'http://localhost');
        if (url.pathname === '/__mock/status') {
          try {
            response.setHeader('Content-Type', 'text/html; charset=utf-8');
            response.setHeader('Cache-Control', 'no-store');
            response.end(await server.transformIndexHtml(url.pathname, '<!doctype html><html lang="zh-CN"><head><title>Silent status preview</title></head><body><div id="root"></div><script type="module" src="/src/dev-mock/status-preview.tsx"></script></body></html>'));
          } catch (error) { next(error as Error); }
          return;
        }
        // The update check's stand-in (workstation/updates.ts): the version in tauri.conf.json, and what the scenario offers.
        if (url.pathname === '/__mock/update') {
          const offer = scenarios[selectScenario(url, request.headers.cookie || '')].update;
          let current = '0.0.0';
          try {
            current = JSON.parse(fs.readFileSync(path.resolve(server.config.root, 'src-tauri/tauri.conf.json'), 'utf8')).version ?? current;
          } catch { /* keep the placeholder */ }
          response.setHeader('Content-Type', 'application/json; charset=utf-8');
          response.setHeader('Cache-Control', 'no-store');
          response.end(JSON.stringify({
            current,
            update: offer?.version ? { version: offer.version, date: offer.date ?? null, notes: offer.notes ?? '' } : null,
            fail: offer?.fail ?? null,
            size: offer?.size ?? 48_234_496,
          }));
          return;
        }
        if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/health')) return next();
        // Opt-in local asset pack for looking at the "pack installed" stage (same variable as the backend). The pack is
        // read from the developer's own data directory and never bundled; unset, the pack stays absent as before.
        const packDir = process.env.AMADEUS_ASSET_PACK_DIR;
        if (url.pathname.startsWith('/api/asset-pack/')) {
          if (packDir) {
            const root = path.resolve(packDir);
            const target = path.resolve(root, decodeURIComponent(url.pathname.slice('/api/asset-pack/'.length)));
            const type = PACK_TYPES[path.extname(target).toLowerCase()];
            if (!target.startsWith(root + path.sep) || !type || !fs.existsSync(target) || !fs.statSync(target).isFile()) {
              response.statusCode = 404;
              response.end();
              return;
            }
            response.setHeader('Content-Type', type);
            response.setHeader('Cache-Control', 'no-store');
            fs.createReadStream(target).pipe(response);
            return;
          }
          const cookies = request.headers.cookie || '';
          const reply = runtime.rest(
            url,
            request.method || 'GET',
            {},
            readCookie(cookies, 'amadeus_mock_client') || 'default',
            cookies,
          );
          response.setHeader('Content-Type', 'application/json; charset=utf-8');
          response.setHeader('Cache-Control', 'no-store');
          response.statusCode = reply.status;
          for (const [name, value] of Object.entries(reply.headers ?? {})) response.setHeader(name, value);
          response.end(reply.raw ? Buffer.from(reply.raw) : JSON.stringify(reply.body));
          return;
        }
        response.setHeader('Content-Type', 'application/json; charset=utf-8');
        response.setHeader('Cache-Control', 'no-store');
        if (request.headers.authorization !== `Bearer ${MOCK_LOCAL_TOKEN}`) {
          response.statusCode = 401;
          response.end(JSON.stringify({ detail: { code: 'local_auth_required' } }));
          return;
        }
        try {
          const cookies = request.headers.cookie || '';
          const allowBinaryZip = url.pathname.startsWith('/api/data/asset-pack');
          const { body, meta } = await readPayload(request, allowBinaryZip);
          const reply = runtime.rest(
            url, request.method || 'GET', body,
            readCookie(cookies, 'amadeus_mock_client') || 'default', cookies, meta,
          );
          if (reply.delayMs) await new Promise(resolve => setTimeout(resolve, reply.delayMs));
          response.statusCode = reply.status;
          for (const [name, value] of Object.entries(reply.headers ?? {})) response.setHeader(name, value);
          response.end(reply.raw ? Buffer.from(reply.raw) : JSON.stringify(reply.body));
        } catch (error) {
          response.statusCode = error instanceof Error && error.message === 'body_too_large' ? 413 : 400;
          response.end(JSON.stringify({ detail: { code: 'invalid_mock_request' } }));
        }
      });
    },
  };
}
