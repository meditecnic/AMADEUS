import { describe, expect, it, vi } from 'vitest';
import { createProviderStore, handleProviders } from '../dev-mock/providers';

const apiFetch = vi.fn(async (_path: string, _init?: RequestInit) => new Response('{"provider_id":"custom:a"}', { status: 200 }));
vi.mock('../apiBase', async (original) => ({
  ...(await original<typeof import('../apiBase')>()),
  apiFetch: (path: string, init?: RequestInit) => apiFetch(path, init),
}));

describe('changing a connection', () => {
  it('sends the changed fields on their own: the backend refuses them next to a version', async () => {
    const { updateProfile } = await import('./connectApi');
    await updateProfile('custom:a', { default_model: 'm1' });
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/providers/custom-profiles/custom%3Aa');
    expect(init?.method).toBe('PATCH');
    expect(JSON.parse(String(init?.body))).toEqual({ default_model: 'm1' });
  });

  it('is refused by the development backend the way the real one refuses it', () => {
    const store = createProviderStore();
    const made = handleProviders('/api/providers/custom-profiles', 'POST', { display_name: 'relay', base_url: 'https://relay.example/v1' }, store, []);
    const { provider_id: id, configuration_version: version } = made!.body as { provider_id: string; configuration_version: string };
    const patch = (body: Record<string, unknown>) => handleProviders(`/api/providers/custom-profiles/${id}`, 'PATCH', body, store, [])!;
    // Name, address or model together with the version: 422, as backend/app/routers/providers.py answers.
    expect(patch({ default_model: 'm1', expected_configuration_version: version }).status).toBe(422);
    expect(patch({ selector_admission: 'admitted' }).status).toBe(422);
    const set = patch({ default_model: 'm1' });
    expect(set.status).toBe(200);
    const next = (set.body as { configuration_version: string }).configuration_version;
    expect(patch({ selector_admission: 'admitted', expected_configuration_version: version }).status).toBe(409);
    expect(patch({ selector_admission: 'admitted', expected_configuration_version: next }).status).toBe(200);
  });
});
