// @vitest-environment node
import { createServer } from 'node:net';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { selectBackendPort } from './backend-port.mjs';

beforeEach(() => vi.stubEnv('BACKEND_PORT', undefined));
afterEach(() => vi.unstubAllEnvs());

it.each([undefined, '0'])('selects a nonzero loopback port for %s', async value => {
  const port = await selectBackendPort(value);
  expect(port).toBeGreaterThan(0);
  expect(port).toBeLessThanOrEqual(65535);
});

it('uses an available fixed port', async () => {
  const port = await selectBackendPort('0');
  expect(await selectBackendPort(String(port))).toBe(port);
});

it('rejects an occupied fixed port without choosing another', async () => {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const port = server.address().port;
    await expect(selectBackendPort(String(port))).rejects.toThrow(`Unable to bind backend port ${port}`);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});

it.each(['', '-1', '65536', 'abc', '1.5'])('rejects invalid BACKEND_PORT=%s', async value => {
  await expect(selectBackendPort(value)).rejects.toThrow('BACKEND_PORT must be an integer');
});
