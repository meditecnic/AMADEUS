/** Selected-model chat verification (POST /api/providers/{id}/chat-check).
 *  The backend stores nothing; results live here, keyed by provider + model + local config version,
 *  so a late answer for an older configuration can never be shown as the current result. */

export type ChatCheckState =
  | { status: 'checking' }
  | { status: 'ok'; at: number }
  | { status: 'failed'; code: string; at: number };

const MESSAGES: Record<string, string> = {
  credential_missing: '还没有填写密钥。',
  credential_rejected: '服务拒绝了这个密钥，请检查是否填写正确。',
  model_unavailable: '所选模型当前不可用，请换一个模型。',
  model_id_required: '还没有选择模型。',
  chat_check_timeout: '20 秒内没有回应。这不代表密钥无效，可以稍后重试。',
  empty_response: '服务有响应，但没有返回内容。',
  provider_unavailable: '无法连接到这个服务，请检查地址或网络。',
  configuration_changed: '测试期间配置发生了变化，请重新测试。',
  chat_unsupported: '这个接入不支持聊天测试。',
  provider_not_found: '找不到这个接入，它可能已被移除。',
  network_error: '无法连接到 Amadeus 后端。',
};

export function chatCheckMessage(code: string): string {
  return MESSAGES[code] ?? `测试失败（${code}）。`;
}

export async function requestChatCheck(providerId: string, modelId: string): Promise<ChatCheckState> {
  try {
    const response = await fetch(`/api/providers/${encodeURIComponent(providerId)}/chat-check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_id: modelId }),
    });
    if (response.ok) {
      const body = await response.json().catch(() => null) as { ok?: boolean; model_id?: string } | null;
      if (body?.ok && (!body.model_id || body.model_id === modelId)) return { status: 'ok', at: Date.now() };
      return { status: 'failed', code: 'empty_response', at: Date.now() };
    }
    const body = await response.json().catch(() => null) as { detail?: { code?: string } | string } | null;
    const code = typeof body?.detail === 'object' && body.detail?.code ? body.detail.code : `http_${response.status}`;
    return { status: 'failed', code, at: Date.now() };
  } catch {
    return { status: 'failed', code: 'network_error', at: Date.now() };
  }
}

const hhmm = (at: number) => new Date(at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

export function ConnectionCheckPanel({ providerName, modelId, state, onCheck, disabled, pending = false, manual = false, onManualEnable }: {
  providerName: string;
  modelId: string;
  state: ChatCheckState | undefined;
  onCheck: () => void;
  disabled?: boolean;
  /** Saved but not yet in the chat selector. */
  pending?: boolean;
  /** Enabled by hand without a passing check; the real test state stays visible. */
  manual?: boolean;
  onManualEnable?: () => void;
}) {
  const status = state?.status ?? 'untested';
  const standing = pending ? '未加入聊天选择' : manual && status !== 'ok' ? '手动启用' : null;
  return (
    <div className={`ws2-check ws2-check-${status}${pending ? ' is-pending' : ''}`}>
      <div className="ws2-m-row">
        <span className={`ws2-lamp ${status === 'ok' ? 'ok' : status === 'failed' ? 'bad' : status === 'checking' ? 'busy' : 'idle'}`} aria-hidden="true" />
        <div className="ws2-m-label">
          <b>
            {providerName}<span className="ws2-check-model">{modelId || '未选择模型'}</span>
            {standing && <span className="ws2-check-standing">{standing}</span>}
          </b>
          <small role="status">
            {status === 'untested' && '所选模型尚未测试'}
            {status === 'checking' && '正在连接…'}
            {state?.status === 'ok' && `已连接 · ${hhmm(state.at)} 通过`}
            {state?.status === 'failed' && `未通过 · ${chatCheckMessage(state.code)}`}
          </small>
        </div>
        <button type="button" className="ws2-check-btn" onClick={onCheck} disabled={disabled || status === 'checking' || !modelId}>
          {status === 'failed' ? '重试' : status === 'ok' ? '重新测试' : '测试所选模型'}
        </button>
        {pending && onManualEnable && status !== 'checking' && (
          <button type="button" className="ws2-check-btn ghost" onClick={onManualEnable} disabled={!modelId}>
            仍然启用
          </button>
        )}
      </div>
      {pending ? (
        <p className="ws2-m-note">测试通过后会自动加入聊天模型选择。若服务不支持测试，也可以手动启用；它会保留“未通过/未测试”的真实标记。</p>
      ) : (
        <p className="ws2-m-note">测试会向所选模型发送一次简短请求，可能产生少量调用费用；不包含聊天内容或记忆，也不会写入对话。</p>
      )}
    </div>
  );
}
