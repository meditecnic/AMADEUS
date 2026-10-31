import type { Plugin } from 'vite';
import { MOCK_LOCAL_TOKEN } from './auth';

/** Server-only environment: never expose the token through Vite's client defines. */
export function localBootstrapPlugin(): Plugin {
  return {
    name: 'amadeus-local-bootstrap',
    apply: 'serve',
    configureServer(server) {
      const token = process.env.AMADEUS_DEV_MOCK === '0'
        ? process.env.AMADEUS_DEV_LOCAL_API_TOKEN || '' : MOCK_LOCAL_TOKEN;
      server.middlewares.use((request, response, next) => {
        const path = request.url?.split('?')[0] || '';
        if (!/^\/__amadeus\/bootstrap\/?$/i.test(path)) return next();
        response.setHeader('Cache-Control', 'no-store');
        response.setHeader('Content-Type', 'application/json');
        const site = request.headers['sec-fetch-site'];
        if (request.url !== '/__amadeus/bootstrap'
          || request.method !== 'POST' || request.headers['x-amadeus-bootstrap'] !== '1'
          || (site !== undefined && site !== 'same-origin')
          || !/^(127\.0\.0\.1|localhost):[0-9]+$/.test(request.headers.host || '')) {
          response.statusCode = 403;
          response.end(JSON.stringify({ detail: { code: 'bootstrap_forbidden' } }));
          return;
        }
        response.end(JSON.stringify({ version: 1, http_origin: '', ws_origin: '', token }));
      });
      // Vite 7 installs CORS/Host middleware before configureServer hooks.
      // Own this endpoint's entire response, including rejected preflights.
      server.middlewares.stack.unshift(server.middlewares.stack.pop()!);
    },
  };
}
