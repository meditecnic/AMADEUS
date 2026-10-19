// @vitest-environment node
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import { expect, it } from 'vitest';

it('bundles the complete runtime and shaders with Vite without demo styles or external dependencies', async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  const entry = fileURLToPath(new URL('../worldline-engine/index.ts', import.meta.url));
  const result = await build({
    configFile: false,
    envFile: false,
    root,
    logLevel: 'silent',
    build: {
      write: false,
      minify: false,
      lib: { entry, formats: ['es'] },
    },
  });
  const bundles = Array.isArray(result) ? result : [result];
  const output = bundles.flatMap(bundle => 'output' in bundle ? bundle.output : []);
  const chunks = output.filter(item => item.type === 'chunk');
  expect(chunks).toHaveLength(1);
  const chunk = chunks[0];
  expect(chunk.exports).toEqual(expect.arrayContaining(['Renderer', 'Controller', 'evaluateShow']));
  expect(chunk.imports).toEqual([]);
  expect(chunk.dynamicImports).toEqual([]);
  const modules = Object.keys(chunk.modules).map(id => id.replaceAll('\\', '/'));
  expect(modules.filter(id => id.includes('/shaders/'))).toHaveLength(6);
  expect(modules.every(id => id.includes('/worldline-engine/'))).toBe(true);
  expect(output.filter(item => item.type === 'asset')).toHaveLength(0);
});
