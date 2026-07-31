import React from 'react';
import { SalieriSelect } from './SalieriSelect';

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

export interface ModelCatalogEntry {
  id: string;
  display_name: string;
  lifecycle: ModelLifecycle;
  callable: boolean;
  source: 'manifest' | 'dynamic';
  context_window?: number | null;
  max_output_tokens?: number | null;
  persona_certification?: string;
  evidence?: string[];
  thinking_control: ModelThinkingControl | null;
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
}

interface ProviderControlsProps {
  providers: ProviderMetadata[];
  activeProviderId: string;
  activeModelId: string;
  models: ModelCatalogEntry[];
  disabled: boolean;
  loadingModels: boolean;
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
    unverified: '（未验证）',
  }[lifecycle];
}

function mergeModels(
  activeModelId: string,
  provider: ProviderMetadata | undefined,
  models: ModelCatalogEntry[],
): ModelCatalogEntry[] {
  const byId = new Map(models.map((row) => [row.id, row]));
  if (provider?.default_model_capability) {
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
  loadingModels,
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

  return (
    <div className="provider-controls" aria-label="对话模型配置">
      <SalieriSelect
        aria-label="模型供应商"
        className="provider-select-root"
        triggerClassName="provider-select"
        value={activeProviderId}
        disabled={disabled || providers.length === 0}
        options={providers.map((provider) => ({
          value: provider.id,
          label: provider.display_name,
        }))}
        onChange={onProviderChange}
      />
      <SalieriSelect
        aria-label="模型"
        className="provider-model-select-root"
        triggerClassName="provider-model-select"
        value={activeModelId}
        disabled={disabled || modelOptions.length === 0}
        options={modelOptions.map((model) => ({
          value: model.id,
          label: `${model.display_name}${modelLifecycleSuffix(model.lifecycle)}`,
          disabled: !model.callable,
        }))}
        onChange={onModelChange}
      />
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
          disabled={disabled || !activeModel?.callable}
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
