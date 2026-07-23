import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CHAT_TEMPERATURE_DEFAULT,
  CHAT_TEMPERATURE_MAX,
  CHAT_TEMPERATURE_MIN,
  CHAT_TEMPERATURE_STEP,
  clampChatTemperature,
  SettingsModal,
} from './SettingsModal';

const baseProps = {
  isOpen: true,
  onClose: vi.fn(),
  worldline: 'steins_gate' as const,
  apiKey: '',
  credentialConfigured: true,
  webSearchConfigured: true,
  systemPrompt: '',
  enableTts: true,
  sovitsUrl: 'http://127.0.0.1:9880',
  enableBgm: true,
  bgmVolume: 0.15,
  enableSfx: true,
  sfxVolume: 1,
  temperature: 0.4,
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

describe('clampChatTemperature', () => {
  it('clamps to the approved [0, 1.2] band and uses the product default for non-finite input', () => {
    expect(clampChatTemperature(-1)).toBe(CHAT_TEMPERATURE_MIN);
    expect(clampChatTemperature(0)).toBe(0);
    expect(clampChatTemperature(0.7)).toBe(0.7);
    expect(clampChatTemperature(1)).toBe(1);
    expect(clampChatTemperature(1.2)).toBe(CHAT_TEMPERATURE_MAX);
    expect(clampChatTemperature(2)).toBe(CHAT_TEMPERATURE_MAX);
    expect(clampChatTemperature(Number.NaN)).toBe(CHAT_TEMPERATURE_DEFAULT);
  });
});

describe('SettingsModal UX contracts', () => {
  it('renders the sampling control with range 0–1.2, endpoints, and no legacy 2.0 max', () => {
    render(<SettingsModal {...baseProps} onSaveSettings={vi.fn()} />);

    const range = screen.getByTestId('personality-range');
    expect(range).toHaveAttribute('type', 'range');
    expect(range).toHaveAttribute('min', String(CHAT_TEMPERATURE_MIN));
    expect(range).toHaveAttribute('max', String(CHAT_TEMPERATURE_MAX));
    expect(range).toHaveAttribute('step', String(CHAT_TEMPERATURE_STEP));
    expect(range).toHaveClass('volume-slider', 'personality-range');
    expect(range.closest('.slider-row')).toHaveClass('personality-slider-row');
    expect(screen.getByTestId('personality-control')).toBeTruthy();
    expect(screen.getByTestId('personality-value')).toHaveTextContent('0.4');
    expect(screen.getByText('回复随机性')).toBeTruthy();
    expect(screen.getByTestId('temperature-hint')).toHaveTextContent('不改变角色设定');
    expect(screen.getByTestId('temperature-endpoint-min')).toHaveTextContent('稳定 / 贴人设');
    expect(screen.getByTestId('temperature-endpoint-max')).toHaveTextContent('发散 / 易跑偏');
    // Endpoints sit under the track; control uses a moderate (not full-modal) width.
    expect(screen.getByTestId('temperature-endpoint-min').closest('.personality-endpoints')).toBeTruthy();
    expect(screen.getByTestId('personality-control')).toHaveClass('personality-control');
    expect(screen.queryByText('个性')).toBeNull();
    expect(screen.queryByTestId('temperature-warning')).toBeNull();
  });

  it('shows mild then strong sampling warnings at 0.8 and 0.9', () => {
    const { rerender } = render(
      <SettingsModal {...baseProps} temperature={0.8} onSaveSettings={vi.fn()} />,
    );
    expect(screen.getByTestId('temperature-warning')).toHaveTextContent('不够稳定');

    rerender(<SettingsModal {...baseProps} temperature={0.9} onSaveSettings={vi.fn()} />);
    expect(screen.getByTestId('temperature-warning')).toHaveTextContent('容易跑题');
  });

  it('clamps a legacy temperature prop above 1.2 when opening the control', () => {
    render(<SettingsModal {...baseProps} temperature={2} onSaveSettings={vi.fn()} />);
    const range = screen.getByTestId('personality-range') as HTMLInputElement;
    expect(range.value).toBe('1.2');
    expect(screen.getByTestId('personality-value')).toHaveTextContent('1.2');
  });

  it('shows the one-shot migration notice when legacy storage was clamped', () => {
    const onDismiss = vi.fn();
    render(
      <SettingsModal
        {...baseProps}
        temperature={1.2}
        temperatureMigrationNotice
        onDismissTemperatureMigrationNotice={onDismiss}
        onSaveSettings={vi.fn()}
      />,
    );
    expect(screen.getByTestId('temperature-migration-notice')).toHaveTextContent('安全上限 1.2');
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it('saves a clamped temperature never above 1.2', async () => {
    const onSaveSettings = vi.fn().mockResolvedValue(undefined);
    render(<SettingsModal {...baseProps} temperature={0.5} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByTestId('personality-range'), { target: { value: '1.2' } });
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));

    await waitFor(() =>
      expect(onSaveSettings).toHaveBeenCalledWith(expect.objectContaining({ temperature: 1.2 })),
    );
  });

  it('keeps configured credentials empty and shows a masked placeholder', () => {
    render(<SettingsModal {...baseProps} credentialStatus="configured" onSaveSettings={vi.fn()} />);

    const input = screen.getByTestId('credential-input') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(input).toHaveAttribute('placeholder', '已连接 · 粘贴新密钥以更换');
    expect(screen.getByTestId('credential-status')).toHaveTextContent('已连接');
    expect(screen.getByTestId('credential-badge')).toHaveTextContent('已连接');
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

  it('renders dual search providers and only shows key input when not connected', () => {
    render(
      <SettingsModal
        {...baseProps}
        webSearchConfigured={false}
        webSearchStatus="not_configured"
        webSearchActiveProvider="tavily"
        webSearchProviders={{
          tavily: { id: 'tavily', label: 'Tavily', configured: false },
          firecrawl: { id: 'firecrawl', label: 'Firecrawl', configured: false },
        }}
        onSaveSettings={vi.fn()}
      />,
    );

    expect(screen.getByTestId('web-search-provider-tavily')).toBeTruthy();
    expect(screen.getByTestId('web-search-provider-firecrawl')).toBeTruthy();
    const input = screen.getByTestId('web-search-credential-input') as HTMLInputElement;
    expect(input.type).toBe('password');
    expect(input.value).toBe('');
    expect(screen.getByTestId('web-search-badge')).toHaveTextContent('未配置');
  });

  it('hides the key field when connected and exposes replace/clear actions', async () => {
    const onClearWebSearchCredential = vi.fn().mockResolvedValue(undefined);
    render(
      <SettingsModal
        {...baseProps}
        webSearchConfigured
        webSearchStatus="configured"
        webSearchActiveProvider="tavily"
        webSearchProviders={{
          tavily: { id: 'tavily', label: 'Tavily', configured: true },
          firecrawl: { id: 'firecrawl', label: 'Firecrawl', configured: false },
        }}
        onSaveSettings={vi.fn()}
        onClearWebSearchCredential={onClearWebSearchCredential}
      />,
    );

    expect(screen.queryByTestId('web-search-credential-input')).toBeNull();
    expect(screen.getByTestId('web-search-badge')).toHaveTextContent('已连接');
    fireEvent.click(screen.getByTestId('clear-web-search-credential'));
    await waitFor(() => expect(onClearWebSearchCredential).toHaveBeenCalledWith('tavily'));
  });

  it('saves a Web Search key with the selected provider and never stores it in localStorage', async () => {
    let resolveSave!: () => void;
    const onSaveSettings = vi.fn(() => new Promise<void>((resolve) => { resolveSave = resolve; }));
    render(
      <SettingsModal
        {...baseProps}
        webSearchConfigured={false}
        webSearchStatus="not_configured"
        webSearchActiveProvider="firecrawl"
        onSaveSettings={onSaveSettings}
      />,
    );

    fireEvent.change(screen.getByTestId('web-search-credential-input'), { target: { value: 'fc-secret' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));

    expect(screen.getByTestId('web-search-credential-status')).toHaveTextContent('正在保存');
    expect(onSaveSettings).toHaveBeenCalledWith(expect.objectContaining({
      webSearchApiKey: 'fc-secret',
      webSearchProvider: 'firecrawl',
    }));
    expect(window.localStorage.getItem('fc-secret')).toBeNull();

    resolveSave();
    await waitFor(() => expect(baseProps.onClose).toHaveBeenCalled());
  });

  it('switches active search provider through the dedicated callback', async () => {
    const onSelectWebSearchProvider = vi.fn().mockResolvedValue(undefined);
    render(
      <SettingsModal
        {...baseProps}
        webSearchActiveProvider="tavily"
        webSearchProviders={{
          tavily: { id: 'tavily', label: 'Tavily', configured: true },
          firecrawl: { id: 'firecrawl', label: 'Firecrawl', configured: true },
        }}
        onSaveSettings={vi.fn()}
        onSelectWebSearchProvider={onSelectWebSearchProvider}
      />,
    );

    fireEvent.click(screen.getByTestId('web-search-provider-firecrawl'));
    await waitFor(() => expect(onSelectWebSearchProvider).toHaveBeenCalledWith('firecrawl'));
  });
});
