import '@testing-library/jest-dom/vitest';
import { beforeEach } from 'vitest';

// Component tests mount below the real bootstrap gate. Use the supported manual
// development contract here; authentication tests initialize fresh modules themselves.
beforeEach(async () => {
  if (typeof window === 'undefined') return;
  const { initializeLocalBackend } = await import('../apiBase');
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    version: 1, http_origin: '', ws_origin: '', token: '',
  }), { headers: { 'Content-Type': 'application/json' } });
  try { await initializeLocalBackend(); }
  finally { globalThis.fetch = original; }
});
