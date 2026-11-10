import { apiFetch } from '../apiBase';
import { ApiError } from './dataApi';

/*
 * Connecting a model service in plain steps, on the backend's existing routes (amadeus-ops reports 1xx providers):
 * keep the key, read the model list, say one sentence with the chosen model. A built-in provider only takes its
 * key; anything else becomes a custom profile at its address. Every failure comes back as a code with words a
 * first-time user can act on (what went wrong, what to try).
 */

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch {
    throw new ApiError(0, 'unreachable');
  }
  if (!response.ok) {
    let code = `http_${response.status}`;
    try {
      const detail = (await response.json())?.detail;
      if (detail && typeof detail === 'object' && typeof detail.code === 'string') code = detail.code;
    } catch { /* keep the status */ }
    throw new ApiError(response.status, code);
  }
  return response.json().catch(() => ({})) as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export type Profile = { provider_id: string; configuration_version: string | null; default_model?: string };

/** A custom profile at an address. The backend wants a model up front; the real one is set once the list is read. */
export const createProfile = (name: string, baseUrl: string, model: string) =>
  call<Profile>('/api/providers/custom-profiles', json('POST', { display_name: name, base_url: baseUrl, default_model: model || 'default' }));

export const updateProfile = (id: string, change: { display_name?: string; base_url?: string; default_model?: string }, version: string | null) =>
  call<Profile>(`/api/providers/custom-profiles/${encodeURIComponent(id)}`, json('PATCH', { ...change, expected_configuration_version: version ?? undefined }));

export const removeProfile = (id: string) =>
  call<unknown>(`/api/providers/custom-profiles/${encodeURIComponent(id)}`, { method: 'DELETE' });

export const saveKey = (providerId: string, key: string) =>
  call<unknown>(`/api/providers/${encodeURIComponent(providerId)}/credentials`, json('POST', { api_key: key }));

export const clearKey = (providerId: string) =>
  call<unknown>(`/api/providers/${encodeURIComponent(providerId)}/credentials`, { method: 'DELETE' });

/** The ids the service lists (discovery), or null when it does not list them. */
export async function listModels(providerId: string): Promise<string[] | null> {
  try {
    const body = await call<{ models?: unknown[]; catalog?: { id?: unknown; callable?: unknown }[] }>(
      `/api/providers/${encodeURIComponent(providerId)}/models?refresh=true`,
    );
    const fromCatalog = (body.catalog ?? []).filter((row) => row.callable !== false).map((row) => String(row.id ?? '')).filter(Boolean);
    const fromModels = (body.models ?? []).map((row) => (typeof row === 'string' ? row : String((row as { id?: unknown })?.id ?? ''))).filter(Boolean);
    const ids = fromCatalog.length ? fromCatalog : fromModels;
    return [...new Set(ids)];
  } catch (failure) {
    if (failure instanceof ApiError && (failure.code === 'model_discovery_unsupported' || failure.status === 404)) return null;
    throw failure;
  }
}

export const chatCheck = (providerId: string, modelId: string) =>
  call<{ ok: boolean }>(`/api/providers/${encodeURIComponent(providerId)}/chat-check`, json('POST', { model_id: modelId }));

/** What happened and what to try, for each failure the steps can meet. */
export function connectError(failure: unknown, step?: 'save' | 'list' | 'check'): { title: string; fix: string } {
  const code = failure instanceof ApiError ? failure.code : 'unknown';
  // The backend reports any failed listing as unavailable, a refused key included (hand-off 2026-11-09).
  if (step === 'list' && (code === 'provider_unavailable' || code === 'http_502')) {
    return { title: '读不到模型列表', fix: '多半是密钥不对，或服务地址写错；也可能是网络、代理不通。检查后再试。' };
  }
  switch (code) {
    case 'credential_rejected':
    case 'http_401':
    case 'http_403':
      return { title: '密钥被拒绝了', fix: '确认复制完整、前后没有空格；也可以到服务商后台看看密钥是否有效、余额是否充足。' };
    case 'credential_missing':
      return { title: '还没有密钥', fix: '先粘贴密钥再试。' };
    case 'chat_check_timeout':
      return { title: '等了 20 秒没有回应', fix: '网络慢或服务繁忙。海外服务需要能访问海外网络；稍后再试一次。' };
    case 'provider_unavailable':
    case 'http_502':
      return { title: '服务商没有正常回应', fix: '检查服务地址是否写对（通常以 /v1 结尾）、网络或代理是否通畅；本机服务要先开着。' };
    case 'model_unavailable':
      return { title: '这个模型用不了', fix: '换一个模型再试；有的模型要先在服务商后台开通。' };
    case 'empty_response':
      return { title: '服务商回了空内容', fix: '换一个对话模型再试。' };
    case 'invalid_provider_configuration':
    case 'http_422':
      return { title: '地址格式不对', fix: '应以 http:// 或 https:// 开头，不带用户名、问号或 #。' };
    case 'configuration_changed':
      return { title: '设置在测试时改了', fix: '再试一次。' };
    case 'unreachable':
      return { title: '连不上 AMADEUS 的后台', fix: '后台可能没在运行；重新打开 AMADEUS 再试。' };
    default:
      return { title: '没有接上', fix: `再试一次；还不行就去「关于」生成反馈包。（${code}）` };
  }
}
