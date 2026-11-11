import { apiFetch } from '../apiBase';
import { ApiError } from './dataApi';

/*
 * Connecting a model service in plain steps: keep the key, read the model list, say one sentence with the chosen
 * model. POST /api/providers/connect does the first two in one call and leaves nothing half-made when a step fails
 * (amadeus-ops report 220); the sentence goes through the page's own check so its result shows on the row. Every
 * failure comes back as a code with words a first-time user can act on (what went wrong, what to try).
 */

export type ConnectStep = 'save' | 'list' | 'check';

/** A failure that knows which step it happened in. */
export class ConnectFailure extends ApiError {
  constructor(status: number, code: string, readonly step?: ConnectStep) { super(status, code); }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch {
    throw new ApiError(0, 'unreachable');
  }
  if (!response.ok) {
    let code = `http_${response.status}`;
    let step: ConnectStep | undefined;
    try {
      const detail = (await response.json())?.detail;
      if (detail && typeof detail === 'object' && typeof detail.code === 'string') code = detail.code;
      if (detail && typeof detail === 'object' && ['save', 'list', 'check'].includes(detail.step)) step = detail.step;
    } catch { /* keep the status */ }
    throw new ConnectFailure(response.status, code, step);
  }
  return response.json().catch(() => ({})) as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export type Profile = { provider_id: string; configuration_version: string | null; default_model?: string };

export type Connected = {
  provider_id: string;
  models: string[];
  /** false: the service has no model list; the model is typed by hand. */
  models_listed?: boolean;
  /** The model already chosen for a connection that existed, or null while one is still to be picked. */
  model_id: string | null;
  /**
   * not_checked: an existing connection got a new key and keeps its model and its place in the menu. Without a
   * model sent, ok means the same, after one sentence with the kept model (the service has no list to prove the key).
   */
  check: { ok: boolean; code: 'ok' | 'model_selection_required' | 'not_checked' | string };
  configuration_version: string | null;
};

/** Keep the key and read the list, for a built-in provider or for an address (made or reused by address and label). */
export const connect = (target: { providerId: string } | { baseUrl: string; name?: string; catalogId?: string }, key?: string) =>
  call<Connected>('/api/providers/connect', json('POST', {
    ...('providerId' in target
      ? { provider_id: target.providerId }
      : { base_url: target.baseUrl, display_name: target.name || undefined, catalog_id: target.catalogId }),
    api_key: key || undefined,
  }));

/**
 * Changes a connection's name, address or model. The backend takes these on their own: a request that also carries
 * the admission or the expected version is refused (422), so the version never rides along here.
 */
export const updateProfile = (id: string, change: { display_name?: string; base_url?: string; default_model?: string }) =>
  call<Profile>(`/api/providers/custom-profiles/${encodeURIComponent(id)}`, json('PATCH', change));

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
    if (failure instanceof ApiError && ['model_discovery_unsupported', 'model_list_not_found'].includes(failure.code)) return null;
    throw failure;
  }
}

export const chatCheck = (providerId: string, modelId: string) =>
  call<{ ok: boolean }>(`/api/providers/${encodeURIComponent(providerId)}/chat-check`, json('POST', { model_id: modelId }));

/** What happened and what to try, for each failure the steps can meet. */
export function connectError(failure: unknown, step?: 'save' | 'list' | 'check'): { title: string; fix: string } {
  const code = failure instanceof ApiError ? failure.code : 'unknown';
  if (step === 'list' && (code === 'provider_unavailable' || code === 'http_502')) {
    return { title: '读不到模型列表', fix: '服务商回了一个看不懂的错误。检查服务地址，稍后再试。' };
  }
  switch (code) {
    case 'address_unreachable':
      return { title: '连不上这个地址', fix: '检查服务地址是否写对（通常以 /v1 结尾）、网络或代理是否通畅；本机服务要先开着。' };
    case 'rate_limited':
      return { title: '服务商说请求太多了', fix: '等一会儿再试。用的是订阅套餐的话，可能是这 5 小时或这一周的额度用完了。' };
    case 'insufficient_balance':
      return { title: '余额不足', fix: '到服务商后台充值；有的服务商要先开通这个模型才能用。' };
    case 'credential_rejected':
    case 'http_401':
    case 'http_403':
      return { title: '密钥被拒绝了', fix: '确认复制完整、前后没有空格；也可以到服务商后台看看密钥是否有效、余额是否充足。' };
    case 'credential_missing':
      return { title: '还没有密钥', fix: '先粘贴密钥再试。' };
    case 'chat_check_timeout':
    case 'discovery_timeout':
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
      return { title: step === 'save' ? '地址或名称不对' : '地址格式不对', fix: '地址应以 http:// 或 https:// 开头，不带用户名、问号或 #；名称不能留空。' };
    case 'configuration_changed':
      return { title: '设置在测试时改了', fix: '再试一次。' };
    case 'unreachable':
      return { title: '连不上 AMADEUS 的后台', fix: '后台可能没在运行；重新打开 AMADEUS 再试。' };
    default:
      return { title: '没有接上', fix: `再试一次；还不行就去「关于」生成反馈包。（${code}）` };
  }
}
