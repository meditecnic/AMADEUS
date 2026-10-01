import React from 'react';
import { SalieriSelect } from './SalieriSelect';
import { ModelPicker } from '../workstation/ModelPicker';
import { ProviderMark } from '../workstation/ProviderMark';

export type ModelLifecycle =
  | 'stable'
  | 'preview'
  | 'deprecated'
  | 'retired'
  | 'unavailable'
  | 'unverified';

export interface ModelThinkingOption {
  value: string;
  label: string;
}

export interface ModelThinkingControl {
  kind: 'effort' | 'level' | 'prompt_depth' | string;
  label: string;
  native: boolean;
  default: string;
  request_mode: 'prompt_depth' | 'reasoning_effort' | 'gemini_thinking_level' | string;
  options: ModelThinkingOption[];
  aliases?: Record<string, string>;
}

export type ModelDiscoveryStatus =
  | 'idle'
  | 'loading'
  | 'live'
  | 'cache'
  | 'failed'
  | 'failed_using_cache'
  | 'credential_missing'
  | 'id_only';

export interface ModelCatalogEntry {
  id: string;
  request_id?: string;
  display_name: string;
  display_source?: 'manifest' | 'live_name' | 'request_id' | string;
  lifecycle: ModelLifecycle;
  callable: boolean;
  source: 'manifest' | 'dynamic';
  context_window?: number | null;
  max_output_tokens?: number | null;
  persona_certification?: string;
  evidence?: string[];
  thinking_control: ModelThinkingControl | null;
  disclosed_version?: string | null;
  compatibility_of?: string | null;
  compatibility_scope?: string | null;
  preferred?: boolean;
}

export interface ProviderMetadata {
  id: string;
  display_name: string;
  default_model: string;
  configured: boolean;
  credential_required: boolean;
  model_discovery: boolean;
  parameters?: string[];
  base_url_configurable?: boolean;
  base_url?: string | null;
  catalog_version: number;
  catalog_verified_at: string;
  default_model_capability: ModelCatalogEntry;
  catalog?: ModelCatalogEntry[];
  preferred_model_id?: string | null;
  source?: 'builtin' | 'user_config' | string;
  enabled?: boolean;
  /** pending = saved but kept out of the chat selector until a check passes or it is enabled by hand. */
  selector_admission?: 'pending' | 'admitted' | 'manual';
  /** Custom connections only; admission writes must quote it. */
  configuration_version?: string | null;
}

interface ProviderControlsProps {
  providers: ProviderMetadata[];
  activeProviderId: string;
  activeModelId: string;
  models: ModelCatalogEntry[];
  disabled: boolean;
  /** A running turn keeps its provider and thinking level; the model can still be chosen for the next turn. */
  lockProvider?: boolean;
  /** Composer variant: searchable model picker with other connections and「管理连接」. */
  searchable?: boolean;
  onManageConnections?: () => void;
  loadingModels: boolean;
  discoveryStatus?: ModelDiscoveryStatus;
  thinkingValue: string | null;
  onProviderChange: (providerId: string) => void;
  onModelChange: (modelId: string) => void;
  onThinkingChange: (value: string) => void;
  onRefreshModels: () => void;
  onConfigureCredential: () => void;
}

export function resolveThinkingValue(
  control: ModelThinkingControl | null | undefined,
  stored: string | null | undefined,
  _current: string | null | undefined,
): string | null {
  if (!control) return null;
  const values = new Set(control.options.map((option) => option.value));
  if (stored !== null && stored !== undefined) {
    if (values.has(stored)) return stored;
    const aliasTarget = control.aliases?.[stored];
    return aliasTarget && values.has(aliasTarget) ? aliasTarget : control.default;
  }
  return control.default;
}

function fallbackModel(modelId: string): ModelCatalogEntry {
  return {
    id: modelId,
    display_name: modelId,
    lifecycle: 'unavailable',
    callable: false,
    source: 'dynamic',
    thinking_control: null,
  };
}

export function modelLifecycleSuffix(lifecycle: ModelLifecycle): string {
  return {
    stable: '',
    preview: '（预览）',
    deprecated: '（已弃用）',
    retired: '（已退役）',
    unavailable: '（不可用）',
    // Catalog persona evaluation, not the connection chat-check.
    unverified: '（人格未评估）',
  }[lifecycle];
}

export function modelOptionLabel(model: ModelCatalogEntry): string {
  const life = modelLifecycleSuffix(model.lifecycle);
  if (model.compatibility_of) {
    const availability = model.lifecycle === 'deprecated' ? '' : life;
    return `${model.display_name}（兼容 ID）${availability}`;
  }
  return `${model.display_name}${life}`;
}

export function modelOptionTitle(model: ModelCatalogEntry): string {
  const requestId = model.request_id || model.id;
  const parts = [`请求 ID：${requestId}`];
  if (model.disclosed_version) {
    parts.push(
      `目录披露：${model.disclosed_version}（有证据来源；GET /models 返回的 ID 不能证明底层版本）`,
    );
  } else {
    parts.push('未披露底层版本');
  }
  return parts.join('\n');
}

export function discoveryStatusLabel(status: ModelDiscoveryStatus | undefined): string | null {
  switch (status) {
    case 'loading':
      return '刷新中';
    case 'live':
      return '已刷新';
    case 'cache':
      return '已缓存';
    case 'failed':
      return '刷新失败';
    case 'failed_using_cache':
      return '刷新失败，使用缓存';
    case 'credential_missing':
      return '缺少凭据';
    case 'id_only':
      return '仅有请求 ID';
    default:
      return null;
  }
}

function mergeModels(
  activeModelId: string,
  provider: ProviderMetadata | undefined,
  models: ModelCatalogEntry[],
): ModelCatalogEntry[] {
  const byId = new Map(models.map((row) => [row.id, row]));
  for (const row of provider?.catalog || []) {
    if (!byId.has(row.id)) byId.set(row.id, row);
  }
  if (
    provider?.default_model_capability
    && !byId.has(provider.default_model_capability.id)
  ) {
    byId.set(
      provider.default_model_capability.id,
      provider.default_model_capability,
    );
  }
  if (activeModelId && !byId.has(activeModelId)) {
    byId.set(activeModelId, fallbackModel(activeModelId));
  }
  return Array.from(byId.values());
}

export const ProviderControls: React.FC<ProviderControlsProps> = ({
  providers,
  activeProviderId,
  activeModelId,
  models,
  disabled,
  lockProvider = false,
  searchable = false,
  onManageConnections,
  loadingModels,
  discoveryStatus,
  thinkingValue,
  onProviderChange,
  onModelChange,
  onThinkingChange,
  onRefreshModels,
  onConfigureCredential,
}) => {
  const activeProvider = providers.find((provider) => provider.id === activeProviderId);
  const modelOptions = mergeModels(activeModelId, activeProvider, models);
  const activeModel = modelOptions.find((row) => row.id === activeModelId);
  const thinkingControl = activeModel?.thinking_control ?? null;
  const selectedThinking = resolveThinkingValue(
    thinkingControl,
    thinkingValue,
    thinkingValue,
  );
  const statusLabel = discoveryStatusLabel(
    loadingModels ? 'loading' : discoveryStatus,
  );

  return (
    <div className="provider-controls" aria-label="对话模型配置">
      <SalieriSelect
        aria-label="模型供应商"
        className="provider-select-root"
        triggerClassName="provider-select"
        value={activeProviderId}
        disabled={disabled || lockProvider || providers.length === 0}
        options={providers.map((provider) => {
          const disabledProvider = provider.enabled === false;
          return {
            value: provider.id,
            label: disabledProvider
              ? `${provider.display_name}（已停用/不可用）`
              : provider.display_name,
            disabled: disabledProvider,
            icon: <ProviderMark provider={provider} />,
          };
        })}
        onChange={onProviderChange}
      />
      {searchable ? (
        <ModelPicker
          value={activeModelId}
          disabled={disabled || modelOptions.length === 0}
          options={modelOptions.map((model) => ({
            value: model.id,
            label: modelOptionLabel(model),
            disabled: !model.callable,
            title: modelOptionTitle(model),
          }))}
          title={activeModel ? modelOptionTitle(activeModel) : undefined}
          onChange={onModelChange}
          sourceName={activeProvider?.display_name || activeProviderId}
          sourceMark={activeProvider ? <ProviderMark provider={activeProvider} /> : undefined}
          otherSources={lockProvider ? [] : providers
            .filter((provider) => provider.id !== activeProviderId && provider.enabled !== false)
            .map((provider) => ({
              id: provider.id,
              name: provider.display_name,
              model: provider.default_model,
              mark: <ProviderMark provider={provider} />,
              needsKey: provider.credential_required && !provider.configured,
            }))}
          onSource={onProviderChange}
          onManage={onManageConnections}
        />
      ) : (
        <SalieriSelect
          aria-label="模型"
          className="provider-model-select-root"
          triggerClassName="provider-model-select"
          value={activeModelId}
          disabled={disabled || modelOptions.length === 0}
          options={modelOptions.map((model) => ({
            value: model.id,
            label: modelOptionLabel(model),
            disabled: !model.callable,
            title: modelOptionTitle(model),
          }))}
          title={activeModel ? modelOptionTitle(activeModel) : undefined}
          onChange={onModelChange}
        />
      )}
      {statusLabel ? (
        <span className="provider-discovery-status" data-status={loadingModels ? 'loading' : discoveryStatus} title={statusLabel}>
          {statusLabel}
        </span>
      ) : null}
      {activeProvider?.model_discovery && (
        <button
          type="button"
          className="provider-action-btn"
          aria-label="刷新模型列表"
          title="刷新模型列表"
          disabled={
            disabled
            || loadingModels
            || (activeProvider.credential_required && !activeProvider.configured)
          }
          onClick={onRefreshModels}
        >
          {loadingModels ? '…' : '↻'}
        </button>
      )}
      {activeProvider && activeProvider.credential_required && !activeProvider.configured && (
        <button
          type="button"
          className="provider-credential-btn"
          aria-label={`配置 ${activeProvider.display_name} 凭据`}
          onClick={onConfigureCredential}
        >
          需要凭据
        </button>
      )}
      {thinkingControl && selectedThinking ? (
        <SalieriSelect
          aria-label={thinkingControl.label}
          className="reasoning-compact"
          triggerClassName="reasoning-compact-trigger"
          value={selectedThinking}
          disabled={disabled || lockProvider || !activeModel?.callable}
          triggerPrefix={`${thinkingControl.label} · `}
          title={
            thinkingControl.native
              ? '供应商原生模型控制。'
              : '提示词引导，不是供应商原生推理参数。'
          }
          options={thinkingControl.options.map((option) => ({
            value: option.value,
            label: option.label,
          }))}
          onChange={onThinkingChange}
        />
      ) : (
        <span
          className="reasoning-unavailable"
          title="后端模型目录未声明可用的思考控制，因此不会发送猜测参数。"
        >
          当前模型未声明思考控制
        </span>
      )}
    </div>
  );
};

export default ProviderControls;
