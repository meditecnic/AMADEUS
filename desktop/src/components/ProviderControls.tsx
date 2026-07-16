import React from 'react';

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
}

interface ProviderControlsProps {
  providers: ProviderMetadata[];
  activeProviderId: string;
  activeModelId: string;
  models: string[];
  disabled: boolean;
  loadingModels: boolean;
  onProviderChange: (providerId: string) => void;
  onModelChange: (modelId: string) => void;
  onRefreshModels: () => void;
  onConfigureCredential: () => void;
}

export const ProviderControls: React.FC<ProviderControlsProps> = ({
  providers,
  activeProviderId,
  activeModelId,
  models,
  disabled,
  loadingModels,
  onProviderChange,
  onModelChange,
  onRefreshModels,
  onConfigureCredential,
}) => {
  const activeProvider = providers.find((provider) => provider.id === activeProviderId);
  const modelOptions = Array.from(new Set([
    activeModelId,
    activeProvider?.default_model || '',
    ...models,
  ].filter(Boolean)));

  return (
    <div className="provider-controls" aria-label="对话模型配置">
      <select
        className="provider-select"
        aria-label="模型供应商"
        value={activeProviderId}
        disabled={disabled || providers.length === 0}
        onChange={(event) => onProviderChange(event.target.value)}
      >
        {providers.map((provider) => (
          <option key={provider.id} value={provider.id}>
            {provider.display_name}
          </option>
        ))}
      </select>
      <select
        className="provider-model-select"
        aria-label="模型"
        value={activeModelId}
        disabled={disabled || modelOptions.length === 0}
        onChange={(event) => onModelChange(event.target.value)}
      >
        {modelOptions.map((modelId) => (
          <option key={modelId} value={modelId}>{modelId}</option>
        ))}
      </select>
      {activeProvider?.model_discovery && (
        <button
          type="button"
          className="provider-action-btn"
          aria-label="刷新模型列表"
          title="刷新模型列表"
          disabled={
            disabled ||
            loadingModels ||
            (activeProvider.credential_required && !activeProvider.configured)
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
    </div>
  );
};

export default ProviderControls;
