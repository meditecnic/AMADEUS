import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocketServer, WebSocket } from 'ws';
import type { Plugin } from 'vite';
import { installScenarioBridge } from './client-bridge';
import { createMockRuntime, type Frame } from './runtime';
import { readCookie, scenarios } from './scenarios';

async function readBody(request: IncomingMessage): Promise<Frame> {
  let text = '';
  for await (const chunk of request) {
    text += chunk.toString();
    if (Buffer.byteLength(text) > 65_536) throw new Error('body_too_large');
  }
  if (!text) return {};
  const body: unknown = JSON.parse(text);
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('invalid_body');
  return body as Frame;
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
      const runtime = createMockRuntime();
      const wss = new WebSocketServer({ noServer: true, maxPayload: 65_536 });
      const sockets = new Map<WebSocket, ReturnType<typeof runtime.connect>>();
      const upgrade = (request: IncomingMessage, socket: Duplex, head: Buffer) => {
        const url = new URL(request.url || '/', 'http://localhost');
        if (url.pathname !== '/ws' && url.pathname !== '/ws/chat') return;
        wss.handleUpgrade(request, socket, head, ws => {
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
        if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/health')) return next();
        response.setHeader('Content-Type', 'application/json; charset=utf-8');
        response.setHeader('Cache-Control', 'no-store');
        try {
          const cookies = request.headers.cookie || '';
          const reply = runtime.rest(
            url, request.method || 'GET', await readBody(request),
            readCookie(cookies, 'amadeus_mock_client') || 'default', cookies,
          );
          response.statusCode = reply.status;
          response.end(JSON.stringify(reply.body));
        } catch (error) {
          response.statusCode = error instanceof Error && error.message === 'body_too_large' ? 413 : 400;
          response.end(JSON.stringify({ detail: { code: 'invalid_mock_request' } }));
        }
      });
    },
  };
}
