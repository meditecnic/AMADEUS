import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ProviderControls,
  resolveThinkingValue,
  type ModelCatalogEntry,
  type ProviderMetadata,
} from './ProviderControls';

const flashModel: ModelCatalogEntry = {
  id: 'deepseek-v4-flash',
  display_name: 'DeepSeek V4 Flash',
  lifecycle: 'stable',
  callable: true,
  source: 'manifest',
  thinking_control: {
    kind: 'effort',
    label: '推理强度',
    native: true,
    default: 'high',
    request_mode: 'reasoning_effort',
    options: [
      { value: 'high', label: 'HIGH' },
      { value: 'max', label: 'MAX' },
    ],
    aliases: { low: 'high', medium: 'high', xhigh: 'max' },
  },
};

const openAiModel: ModelCatalogEntry = {
  id: 'gpt-5.6-luna',
  display_name: 'GPT-5.6 Luna',
  lifecycle: 'stable',
  callable: true,
  source: 'manifest',
  thinking_control: {
    kind: 'effort',
    label: '推理强度',
    native: true,
    default: 'medium',
    request_mode: 'reasoning_effort',
    options: [
      { value: 'none', label: 'NONE' },
      { value: 'low', label: 'LOW' },
      { value: 'medium', label: 'MEDIUM' },
      { value: 'high', label: 'HIGH' },
      { value: 'xhigh', label: 'XHIGH' },
      { value: 'max', label: 'MAX' },
    ],
  },
};

const providers: ProviderMetadata[] = [
  {
    id: 'deepseek', display_name: 'DeepSeek', default_model: 'deepseek-v4-flash',
    configured: true, model_discovery: true,
    credential_required: true,
    catalog_version: 1,
    catalog_verified_at: '2026-07-29',
    default_model_capability: flashModel,
  },
  {
    id: 'openai', display_name: 'OpenAI', default_model: 'gpt-5.6-luna',
    configured: false, model_discovery: true,
    credential_required: true,
    catalog_version: 1,
    catalog_verified_at: '2026-07-29',
    default_model_capability: openAiModel,
  },
  {
    id: 'custom', display_name: 'OpenAI-compatible', default_model: 'local-model',
    configured: false, model_discovery: true, credential_required: false,
    catalog_version: 1,
    catalog_verified_at: '2026-07-29',
    default_model_capability: {
      id: 'local-model',
      display_name: 'local-model',
      lifecycle: 'unverified',
      callable: true,
      source: 'dynamic',
      thinking_control: null,
    },
  },
];

afterEach(cleanup);

describe('ProviderControls', () => {
  it('routes provider, model, refresh, and credential actions', () => {
    const onProviderChange = vi.fn();
    const onModelChange = vi.fn();
    const onRefreshModels = vi.fn();
    const onConfigureCredential = vi.fn();
    const { rerender } = render(
      <ProviderControls
        providers={providers}
        activeProviderId="deepseek"
        activeModelId="deepseek-v4-flash"
        models={[
          flashModel,
          {
            ...flashModel,
            id: 'deepseek-v4-pro',
            display_name: 'DeepSeek V4 Pro',
          },
        ]}
        disabled={false}
        loadingModels={false}
        thinkingValue="high"
        onProviderChange={onProviderChange}
        onModelChange={onModelChange}
        onThinkingChange={vi.fn()}
        onRefreshModels={onRefreshModels}
        onConfigureCredential={onConfigureCredential}
      />
    );

    fireEvent.change(screen.getByLabelText('模型供应商'), { target: { value: 'deepseek' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'deepseek-v4-pro' } });
    fireEvent.click(screen.getByRole('button', { name: '刷新模型列表' }));
    rerender(
      <ProviderControls
        providers={providers}
        activeProviderId="openai"
        activeModelId="gpt-5.6-luna"
        models={[openAiModel]}
        disabled={false}
        loadingModels={false}
        thinkingValue="medium"
        onProviderChange={onProviderChange}
        onModelChange={onModelChange}
        onThinkingChange={vi.fn()}
        onRefreshModels={onRefreshModels}
        onConfigureCredential={onConfigureCredential}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: '配置 OpenAI 凭据' }));

    expect(onProviderChange).toHaveBeenCalledWith('deepseek');
    expect(onModelChange).toHaveBeenCalledWith('deepseek-v4-pro');
    expect(onRefreshModels).toHaveBeenCalledTimes(1);
    expect(onConfigureCredential).toHaveBeenCalledTimes(1);
    expect(screen.getByText('需要凭据')).toBeInTheDocument();
  });

  it('locks provider and model changes while a turn is active', () => {
    render(
      <ProviderControls
        providers={providers}
        activeProviderId="deepseek"
        activeModelId="deepseek-v4-flash"
        models={[flashModel]}
        disabled
        loadingModels={false}
        thinkingValue="high"
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
        onThinkingChange={vi.fn()}
        onRefreshModels={vi.fn()}
        onConfigureCredential={vi.fn()}
      />
    );

    expect(screen.getByLabelText('模型供应商')).toBeDisabled();
    expect(screen.getByLabelText('模型')).toBeDisabled();
  });

  it('allows local custom model discovery without forcing a credential', () => {
    render(
      <ProviderControls
        providers={providers}
        activeProviderId="custom"
        activeModelId="local-model"
        models={[providers[2].default_model_capability!]}
        disabled={false}
        loadingModels={false}
        thinkingValue={null}
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
        onThinkingChange={vi.fn()}
        onRefreshModels={vi.fn()}
        onConfigureCredential={vi.fn()}
      />
    );

    expect(screen.getByRole('button', { name: '刷新模型列表' })).toBeEnabled();
    expect(screen.queryByText('需要凭据')).not.toBeInTheDocument();
  });

  it('renders backend-owned thinking options without provider-name heuristics', () => {
    const onThinkingChange = vi.fn();
    render(
      <ProviderControls
        providers={providers}
        activeProviderId="openai"
        activeModelId="gpt-5.6-luna"
        models={[openAiModel]}
        disabled={false}
        loadingModels={false}
        thinkingValue="medium"
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
        onThinkingChange={onThinkingChange}
        onRefreshModels={vi.fn()}
        onConfigureCredential={vi.fn()}
      />
    );

    expect(screen.getByText('推理强度')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '推理强度' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'MAX' }));
    expect(onThinkingChange).toHaveBeenCalledWith('max');
  });

  it('labels native DeepSeek effort and blocks unavailable models', () => {
    render(
      <ProviderControls
        providers={providers}
        activeProviderId="deepseek"
        activeModelId="deepseek-v4-flash"
        models={[
          flashModel,
          {
            ...flashModel,
            id: 'deepseek-v4-pro',
            display_name: 'DeepSeek V4 Pro',
            lifecycle: 'unavailable',
            callable: false,
          },
          {
            ...flashModel,
            id: 'deepseek-next',
            display_name: 'deepseek-next',
            lifecycle: 'unverified',
            source: 'dynamic',
            thinking_control: null,
          },
        ]}
        disabled={false}
        loadingModels={false}
        thinkingValue="high"
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
        onThinkingChange={vi.fn()}
        onRefreshModels={vi.fn()}
        onConfigureCredential={vi.fn()}
      />
    );

    expect(screen.getByText('推理强度')).toBeInTheDocument();
    expect(screen.getByTitle('供应商原生模型控制。')).toBeInTheDocument();
    const unavailable = screen.getByRole('option', {
      name: 'DeepSeek V4 Pro（不可用）',
    });
    expect(unavailable).toBeDisabled();
    expect(screen.getByRole('option', {
      name: 'deepseek-next（未验证）',
    })).toBeEnabled();
  });

  it('falls back to the backend default when a stored value is invalid', () => {
    expect(resolveThinkingValue(openAiModel.thinking_control, 'legacy-x', 'high')).toBe(
      'medium',
    );
    expect(resolveThinkingValue(openAiModel.thinking_control, 'max', 'high')).toBe(
      'max',
    );
    expect(resolveThinkingValue(flashModel.thinking_control, 'xhigh', 'high')).toBe(
      'max',
    );
    expect(resolveThinkingValue(null, 'high', 'high')).toBeNull();
  });

  it('fails closed when the active model is absent from the backend catalog', () => {
    render(
      <ProviderControls
        providers={providers}
        activeProviderId="deepseek"
        activeModelId="missing-pinned-model"
        models={[flashModel]}
        disabled={false}
        loadingModels={false}
        thinkingValue={null}
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
        onThinkingChange={vi.fn()}
        onRefreshModels={vi.fn()}
        onConfigureCredential={vi.fn()}
      />,
    );

    expect(screen.getByRole('option', {
      name: 'missing-pinned-model（不可用）',
    })).toBeDisabled();
  });
});
