import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response('{}', { status: 200 }));
vi.mock('../apiBase', async (original) => ({
  ...(await original<typeof import('../apiBase')>()),
  apiFetch: (path: string, init?: RequestInit) => apiFetch(path, init),
}));

const KEY = 'amadeus_pc_model_shelf';
const patched = () => apiFetch.mock.calls.map(([path, init]) => [path, JSON.parse(String(init?.body))]);

describe('model shelf', () => {
  beforeEach(() => { vi.resetModules(); apiFetch.mockClear(); localStorage.clear(); });
  afterEach(() => { localStorage.clear(); });

  it('takes the shelves the provider list came back with', async () => {
    localStorage.setItem(KEY, JSON.stringify({ deepseek: ['old'], gone: ['x'] }));
    const { adoptShelves, shelved } = await import('./modelShelf');
    adoptShelves([{ id: 'deepseek', visible_models: ['deepseek-chat'] }, { id: 'glm', visible_models: [] }]);
    const shelf = JSON.parse(localStorage.getItem(KEY)!);
    expect(shelf).toEqual({ deepseek: ['deepseek-chat'] });
    expect(shelved(shelf, 'deepseek', [{ id: 'deepseek-chat' }, { id: 'other' }])).toEqual([{ id: 'deepseek-chat' }]);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('sends up a shelf made before the backend kept it, once', async () => {
    localStorage.setItem(KEY, JSON.stringify({ glm: ['glm-5.3'] }));
    const { adoptShelves } = await import('./modelShelf');
    adoptShelves([{ id: 'glm', visible_models: [] }]);
    expect(patched()).toEqual([['/api/providers/glm/settings', { visible_models: ['glm-5.3'] }]]);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ glm: ['glm-5.3'] });
  });

  it('keeps the local shelf when the backend does not report one', async () => {
    localStorage.setItem(KEY, JSON.stringify({ glm: ['glm-5.3'] }));
    const { adoptShelves } = await import('./modelShelf');
    adoptShelves([{ id: 'glm' }]);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ glm: ['glm-5.3'] });
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('writes a change through, and an emptied shelf as an empty list', async () => {
    const { setShelf } = await import('./modelShelf');
    setShelf('custom:a', ['m1', 'm1', 'm2']);
    setShelf('custom:a', null);
    expect(patched()).toEqual([
      ['/api/providers/custom%3Aa/settings', { visible_models: ['m1', 'm2'] }],
      ['/api/providers/custom%3Aa/settings', { visible_models: [] }],
    ]);
    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({});
  });
});
