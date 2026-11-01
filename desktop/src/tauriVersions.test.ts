// @vitest-environment node
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';

it('keeps the Tauri API, CLI and Rust crate on the same major and minor versions', () => {
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
  const cargoLock = readFileSync(new URL('../src-tauri/Cargo.lock', import.meta.url), 'utf8');
  const apiVersion = manifest.dependencies['@tauri-apps/api'];
  const cliSpec = manifest.devDependencies['@tauri-apps/cli'];
  // The CLI is declared as a range (currently ^2); compare its locked version.
  const cliVersion = lock.packages['node_modules/@tauri-apps/cli'].version;
  const crateVersion = cargoLock.match(/^name = "tauri"\r?\nversion = "([^"]+)"/m)?.[1];
  expect(crateVersion).toBeDefined();
  const majorMinor = (version: string) => {
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
    return version.split('.').slice(0, 2);
  };
  expect(majorMinor(apiVersion)).toEqual(majorMinor(crateVersion!));
  expect(majorMinor(cliVersion)).toEqual(majorMinor(crateVersion!));
  expect(lock.packages[''].devDependencies['@tauri-apps/cli']).toBe(cliSpec);
  expect(lock.packages[''].dependencies['@tauri-apps/api']).toBe(apiVersion);
  expect(lock.packages['node_modules/@tauri-apps/api'].version).toBe(apiVersion);
});
