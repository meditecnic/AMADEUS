import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsModal } from './SettingsModal';

const baseProps = {
  isOpen: true,
  onClose: vi.fn(),
  worldline: 'steins_gate' as const,
  apiKey: '',
  credentialConfigured: true,
  systemPrompt: '',
  enableTts: true,
  sovitsUrl: 'http://127.0.0.1:9880',
  enableBgm: true,
  bgmVolume: 0.15,
  enableSfx: true,
  sfxVolume: 1,
  temperature: 0.8,
  providerDisplayName: 'DeepSeek',
  providerBaseUrl: null,
  baseUrlConfigurable: false,
  model: 'deepseek-v4-flash',
  modelOptions: [],
  reasoningEffort: 'high' as const,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('SettingsModal UX contracts', () => {
  it('renders the personality control as a direct themed range with endpoints', () => {
    render(<SettingsModal {...baseProps} onSaveSettings={vi.fn()} />);

    const range = screen.getByTestId('personality-range');
    expect(range).toHaveAttribute('type', 'range');
    expect(range.parentElement).toHaveClass('slider-row');
    expect(range.closest('.salieri-range-shell')).toBeNull();
    expect(screen.getByText('严谨')).toBeInTheDocument();
    expect(screen.getByText('发散')).toBeInTheDocument();
    expect(screen.getByTestId('personality-value')).toHaveTextContent('0.8');
  });

  it('keeps configured credentials empty and shows a masked placeholder', () => {
    render(<SettingsModal {...baseProps} credentialStatus="configured" onSaveSettings={vi.fn()} />);

    const input = screen.getByTestId('credential-input') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input).toHaveAttribute('placeholder', '••••••••');
    expect(screen.getByTestId('credential-status')).toHaveTextContent('已配置');
  });

  it('shows saving while the save request is pending and preserves a blank key', async () => {
    let resolveSave!: () => void;
    const onSaveSettings = vi.fn(() => new Promise<void>((resolve) => { resolveSave = resolve; }));
    render(<SettingsModal {...baseProps} onSaveSettings={onSaveSettings} />);

    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    expect(screen.getByTestId('credential-status')).toHaveTextContent('保存中');
    expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled();
    expect(onSaveSettings).toHaveBeenCalledWith(expect.objectContaining({ apiKey: '' }));

    resolveSave();
    await waitFor(() => expect(baseProps.onClose).toHaveBeenCalled());
  });

  it('exposes an explicit credential error state without rendering a secret', () => {
    render(
      <SettingsModal
        {...baseProps}
        credentialStatus="error"
        onSaveSettings={vi.fn()}
      />,
    );

    expect(screen.getByTestId('credential-status')).toHaveTextContent('保存失败');
    expect(screen.getByTestId('credential-input')).toHaveValue('');
  });
});
