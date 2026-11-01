// @vitest-environment node
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it, vi } from 'vitest';
import { loadConfigFromFile } from 'vite';

afterEach(() => vi.unstubAllEnvs());

const configFile = fileURLToPath(new URL('../vite.config.ts', import.meta.url));
const environment = { command: 'serve', mode: 'development' } as const;

it.each([' 8 ', '+5', '1_0', '-1', '65536', '', '0', '8\n', '８'])(
  'rejects invalid AMADEUS_FRONTEND_PORT=%j', async value => {
    vi.stubEnv('AMADEUS_FRONTEND_PORT', value);
    await expect(loadConfigFromFile(environment, configFile)).rejects.toThrow('AMADEUS_FRONTEND_PORT');
  },
);

it.each([[undefined, 1420], ['1422', 1422], ['1', 1], ['65535', 65535]] as const)(
  'uses AMADEUS_FRONTEND_PORT=%j', async (value, expected) => {
    vi.stubEnv('AMADEUS_FRONTEND_PORT', value);
    const loaded = await loadConfigFromFile(environment, configFile);
    expect(loaded?.config.server?.port).toBe(expected);
  },
);
