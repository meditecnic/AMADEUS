import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProviderControls, type ProviderMetadata } from './ProviderControls';

const providers: ProviderMetadata[] = [
  {
    id: 'deepseek', display_name: 'DeepSeek', default_model: 'deepseek-v4-flash',
    configured: true, model_discovery: true,
    credential_required: true,
  },
  {
    id: 'openai', display_name: 'OpenAI', default_model: 'gpt-5.6-luna',
    configured: false, model_discovery: true,
    credential_required: true,
  },
  {
    id: 'custom', display_name: 'OpenAI-compatible', default_model: 'local-model',
    configured: false, model_discovery: true, credential_required: false,
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
        models={['deepseek-v4-flash', 'gpt-5.6-pro']}
        disabled={false}
        loadingModels={false}
        onProviderChange={onProviderChange}
        onModelChange={onModelChange}
        onRefreshModels={onRefreshModels}
        onConfigureCredential={onConfigureCredential}
      />
    );

    fireEvent.change(screen.getByLabelText('模型供应商'), { target: { value: 'deepseek' } });
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'gpt-5.6-pro' } });
    fireEvent.click(screen.getByRole('button', { name: '刷新模型列表' }));
    rerender(
      <ProviderControls
        providers={providers}
        activeProviderId="openai"
        activeModelId="gpt-5.6-luna"
        models={['gpt-5.6-luna']}
        disabled={false}
        loadingModels={false}
        onProviderChange={onProviderChange}
        onModelChange={onModelChange}
        onRefreshModels={onRefreshModels}
        onConfigureCredential={onConfigureCredential}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: '配置 OpenAI 凭据' }));

    expect(onProviderChange).toHaveBeenCalledWith('deepseek');
    expect(onModelChange).toHaveBeenCalledWith('gpt-5.6-pro');
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
        models={['deepseek-v4-flash']}
        disabled
        loadingModels={false}
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
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
        models={['local-model']}
        disabled={false}
        loadingModels={false}
        onProviderChange={vi.fn()}
        onModelChange={vi.fn()}
        onRefreshModels={vi.fn()}
        onConfigureCredential={vi.fn()}
      />
    );

    expect(screen.getByRole('button', { name: '刷新模型列表' })).toBeEnabled();
    expect(screen.queryByText('需要凭据')).not.toBeInTheDocument();
  });
});
