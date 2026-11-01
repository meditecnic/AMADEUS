// @vitest-environment node
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

it('permits dynamic loopback ports for backend requests and assets', () => {
  const config = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'));
  const csp = config.app.security.csp as string;
  expect(csp).not.toContain(':8000');
  const directives = new Map(csp.split(';').map(directive => {
    const [name, ...sources] = directive.trim().split(/\s+/);
    return [name, sources];
  }));
  for (const name of ['img-src', 'media-src', 'connect-src']) {
    expect(directives.get(name)).toContain('http://127.0.0.1:*');
  }
  expect(directives.get('connect-src')).toContain('ws://127.0.0.1:*');
});
