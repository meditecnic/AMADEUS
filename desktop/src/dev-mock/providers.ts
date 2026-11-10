import { fixedTime, model, providers as fixtureProviders } from './fixtures';
import type { Reply } from './data';

/*
 * The providers routes the connections page uses (ConnectionsPanel.tsx): the built-ins (DeepSeek with a key, the
 * other three without), custom profiles at an address, a model list per service and a one-sentence check. No key
 * is kept: saving one only marks the service configured, and a key containing "bad" marks it as one the service
 * will refuse, so the failure words can be seen.
 */

type Row = Record<string, unknown> & { id: string; display_name: string; default_model: string; configured: boolean };
export type ProviderStore = { rows: Row[]; refused: Set<string>; versions: number };

const ok = (body: unknown, status = 200): Reply => ({ status, body });
const fail = (status: number, code: string): Reply => ok({ detail: { code } }, status);

const builtin = (id: string, name: string, defaultModel: string): Row => ({
  id, display_name: name, default_model: defaultModel, configured: false, credential_required: true, model_discovery: true,
  parameters: ['temperature'], base_url_configurable: false, catalog_version: 1, catalog_verified_at: fixedTime,
  default_model_capability: { ...model, id: defaultModel, display_name: defaultModel }, catalog: [], enabled: true,
  selector_admission: 'admitted', source: 'builtin', configuration_version: null,
});

export function createProviderStore(): ProviderStore {
  return {
    rows: [
      ...structuredClone(fixtureProviders).map((row) => ({ ...row, source: 'builtin', configuration_version: null }) as Row),
      builtin('glm', 'GLM', 'glm-4.6'),
      builtin('openai', 'OpenAI', 'gpt-5.1'),
      builtin('gemini', 'Gemini', 'gemini-3-pro'),
    ],
    refused: new Set(),
    versions: 0,
  };
}

/** A plausible list for the address, so the suggestion and the shelf have something to work with. */
function modelsFor(row: Row): string[] {
  const url = String(row.base_url ?? '');
  if (row.id === 'glm') return ['glm-4.6', 'glm-4.5-air', 'glm-4.5-flash', 'embedding-3'];
  if (row.id === 'openai') return ['gpt-5.1', 'gpt-5.1-mini', 'gpt-4.1', 'text-embedding-3-large', 'whisper-1', 'tts-1'];
  if (row.id === 'gemini') return ['gemini-3-pro', 'gemini-2.5-flash', 'text-embedding-004'];
  if (/moonshot/.test(url)) return ['kimi-k2-turbo-preview', 'kimi-k2-0905-preview', 'kimi-latest', 'moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'];
  if (/dashscope/.test(url)) return ['qwen-plus', 'qwen-max', 'qwen-turbo', 'qwen3-coder-plus', 'text-embedding-v4'];
  if (/siliconflow/.test(url)) return ['deepseek-ai/DeepSeek-V3.2', 'Qwen/Qwen3-235B-A22B', 'zai-org/GLM-4.6', 'moonshotai/Kimi-K2-Instruct', 'BAAI/bge-m3'];
  if (/openrouter/.test(url)) return ['anthropic/claude-sonnet-4.5', 'openai/gpt-5.1', 'google/gemini-3-pro', 'deepseek/deepseek-chat', 'qwen/qwen3-max', 'x-ai/grok-4', 'meta-llama/llama-4-maverick', 'mistralai/mistral-large'];
  if (/11434|1234/.test(url)) return ['qwen3:8b', 'llama3.2:3b'];
  return ['model-a', 'model-b'];
}

export function handleProviders(path: string, method: string, body: Record<string, unknown>, store: ProviderStore, catalog: unknown[]): Reply | null {
  const find = (id: string) => store.rows.find((row) => row.id === id);
  if (path === '/api/providers' && method === 'GET') {
    return ok(structuredClone(store.rows.map((row) => (row.id === 'deepseek' ? { ...row, catalog } : row))));
  }
  if (path === '/api/providers/custom-profiles' && method === 'POST') {
    const url = String(body.base_url ?? '').trim().replace(/\/+$/, '');
    if (!/^https?:\/\/[^/?#\s]+/.test(url)) return fail(422, 'invalid_provider_configuration');
    store.versions += 1;
    const id = `custom:00000000-0000-4000-8000-${String(store.versions).padStart(12, '0')}`;
    const row: Row = {
      ...builtin(id, String(body.display_name ?? 'Custom'), String(body.default_model ?? 'default')),
      credential_required: false, base_url_configurable: true, base_url: url, source: 'user_config',
      selector_admission: 'pending', configuration_version: `v${store.versions}`,
    };
    store.rows.push(row);
    return ok({ provider_id: id, display_name: row.display_name, base_url: url, default_model: row.default_model, enabled: true,
      selector_admission: 'pending', configuration_version: row.configuration_version, source: 'user_config', created_at_utc: fixedTime });
  }
  // One call: make or reuse the connection (same address and label), keep the key, read the list.
  if (path === '/api/providers/connect' && method === 'POST') {
    const stepFail = (status: number, code: string, step: string) => ok({ detail: { code, step } }, status);
    const key = String(body.api_key ?? '');
    let row: Row | undefined;
    let made = false;
    if (typeof body.provider_id === 'string') {
      row = find(body.provider_id);
      if (!row || row.source !== 'builtin') return stepFail(422, 'invalid_provider_configuration', 'save');
    } else {
      const url = String(body.base_url ?? '').trim().replace(/\/+$/, '');
      if (!/^https?:\/\/[^/?#\s]+/.test(url) || (body.display_name !== undefined && !String(body.display_name).trim())) {
        return stepFail(422, 'invalid_provider_configuration', 'save');
      }
      const label = typeof body.catalog_id === 'string' ? body.catalog_id : null;
      row = store.rows.find((r) => r.source === 'user_config' && r.enabled !== false && r.base_url === url && (r.catalog_id ?? null) === label);
      if (!row) {
        store.versions += 1;
        row = {
          ...builtin(`custom:00000000-0000-4000-8000-${String(store.versions).padStart(12, '0')}`, String(body.display_name ?? 'Custom'), ''),
          credential_required: false, base_url_configurable: true, base_url: url, source: 'user_config', catalog_id: label,
          selector_admission: 'pending', configuration_version: `v${store.versions}`,
        };
        store.rows.push(row);
        made = true;
      }
    }
    // A key containing "bad" is one the service refuses: nothing is kept of this attempt.
    if (key.includes('bad')) {
      if (made) store.rows.splice(store.rows.indexOf(row), 1);
      return stepFail(401, 'credential_rejected', 'list');
    }
    if (row.credential_required && !row.configured && !key) {
      return stepFail(409, 'credential_missing', 'list');
    }
    if (key) { row.configured = true; store.refused.delete(row.id); }
    const kept = !made && Boolean(row.default_model);
    return ok({
      provider_id: row.id, models: row.id === 'deepseek' ? (catalog as { id: string }[]).map((m) => m.id) : modelsFor(row), models_listed: true,
      model_id: kept ? row.default_model : null,
      check: { ok: false, code: kept ? 'not_checked' : 'model_selection_required' },
      configuration_version: row.configuration_version ?? null,
    });
  }
  const settings = /^\/api\/providers\/([^/]+)\/settings$/.exec(path);
  if (settings && method === 'PATCH') {
    const row = find(decodeURIComponent(settings[1]));
    if (!row) return fail(404, 'provider_not_found');
    row.visible_models = Array.isArray(body.visible_models) ? body.visible_models.filter((id): id is string => typeof id === 'string') : [];
    return ok({ provider_id: row.id, visible_models: row.visible_models });
  }
  const profile = /^\/api\/providers\/custom-profiles\/([^/]+)$/.exec(path);
  if (profile) {
    const row = find(profile[1]);
    if (!row || row.enabled === false) return fail(404, 'unknown_profile');
    if (method === 'DELETE') { row.enabled = false; return ok({ provider_id: row.id, enabled: false }); }
    if (method === 'PATCH') {
      if (body.expected_configuration_version && body.expected_configuration_version !== row.configuration_version) return fail(409, 'configuration_changed');
      if (typeof body.base_url === 'string') row.base_url = body.base_url.trim().replace(/\/+$/, '');
      if (typeof body.default_model === 'string') row.default_model = body.default_model;
      if (typeof body.display_name === 'string') row.display_name = body.display_name;
      if (typeof body.selector_admission === 'string') row.selector_admission = body.selector_admission;
      store.versions += 1;
      row.configuration_version = `v${store.versions}`;
      return ok({ provider_id: row.id, display_name: row.display_name, base_url: row.base_url, default_model: row.default_model, enabled: true,
        selector_admission: row.selector_admission, configuration_version: row.configuration_version, source: 'user_config', created_at_utc: fixedTime });
    }
  }
  const credentials = /^\/api\/providers\/([^/]+)\/credentials$/.exec(path);
  if (credentials) {
    const row = find(credentials[1]);
    if (!row) return fail(404, 'provider_not_found');
    if (method === 'POST') {
      row.configured = true;
      // The key itself is dropped here; only whether the service would refuse it is kept.
      if (String(body.api_key ?? '').includes('bad')) store.refused.add(row.id); else store.refused.delete(row.id);
      return ok({ provider_id: row.id, configured: true });
    }
    if (method === 'DELETE') { row.configured = false; store.refused.delete(row.id); return ok({ provider_id: row.id, configured: false }); }
  }
  const models = /^\/api\/providers\/([^/]+)\/models$/.exec(path);
  if (models && method === 'GET') {
    const row = find(models[1]);
    if (!row) return fail(404, 'provider_not_found');
    if (row.id === 'deepseek') return ok({ catalog, discovery_status: 'live', id_only: false });
    if (row.credential_required && !row.configured) return fail(409, 'credential_missing');
    if (store.refused.has(row.id)) return fail(502, 'provider_unavailable');
    const ids = modelsFor(row);
    return ok({ provider_id: row.id, models: ids, catalog: ids.map((id) => ({ ...model, id, display_name: id, callable: true })), discovery_status: 'live', id_only: true });
  }
  const check = /^\/api\/providers\/([^/]+)\/chat-check$/.exec(path);
  if (check && method === 'POST') {
    const row = find(check[1]);
    if (!row) return fail(404, 'provider_not_found');
    if (row.credential_required && !row.configured) return fail(409, 'credential_missing');
    if (store.refused.has(row.id)) return fail(401, 'credential_rejected');
    return ok({ ok: true, provider_id: row.id, model_id: body.model_id });
  }
  return null;
}
