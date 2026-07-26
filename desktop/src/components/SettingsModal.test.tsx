import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CHAT_TEMPERATURE_DEFAULT,
  CHAT_TEMPERATURE_MAX,
  CHAT_TEMPERATURE_MIN,
  CHAT_TEMPERATURE_STEP,
  DEFAULT_IDENTITY_MODE,
  IDENTITY_MODE_STORAGE_KEY,
  RESERVED_OKABE_NAME_HINT,
  SELF_NAME_INVALID_CHARS_HINT,
  SELF_NAME_MAX_CODEPOINTS,
  SELF_NAME_STORAGE_KEY,
  SELF_NAME_TOO_LONG_HINT,
  clampChatTemperature,
  identityModeNewConversationConfirm,
  isReservedOkabeName,
  loadStoredIdentityMode,
  loadStoredSelfName,
  normalizeIdentityMode,
  persistIdentityMode,
  persistSelfName,
  validateSelfName,
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
  temperature: 0.0,
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
  try {
    window.localStorage.removeItem(IDENTITY_MODE_STORAGE_KEY);
    window.localStorage.removeItem(SELF_NAME_STORAGE_KEY);
  } catch {
    /* ignore */
  }
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

describe('identity mode helpers (Phase-2 S9)', () => {
  it('defaults to okabe and never treats nicknames as mode signals', () => {
    expect(DEFAULT_IDENTITY_MODE).toBe('okabe');
    expect(normalizeIdentityMode(undefined)).toBe('okabe');
    expect(normalizeIdentityMode('Christina')).toBe('okabe');
    expect(normalizeIdentityMode('助手')).toBe('okabe');
    expect(normalizeIdentityMode('self')).toBe('self');
    expect(loadStoredIdentityMode()).toBe('okabe');
    persistIdentityMode('self');
    expect(window.localStorage.getItem(IDENTITY_MODE_STORAGE_KEY)).toBe('self');
    expect(loadStoredIdentityMode()).toBe('self');
    expect(identityModeNewConversationConfirm('self')).toMatch(/不会合并/);
    expect(identityModeNewConversationConfirm('okabe')).toMatch(/不会合并/);
  });
});

describe('self name helpers (Slice B)', () => {
  it('trims, keeps normal names, and treats empty as valid unset', () => {
    expect(validateSelfName('  阿伟  ')).toEqual({ ok: true, value: '阿伟' });
    expect(validateSelfName('Salieri')).toEqual({ ok: true, value: 'Salieri' });
    expect(validateSelfName("O'Brien")).toEqual({ ok: true, value: "O'Brien" });
    expect(validateSelfName('クリス・マキセ')).toEqual({ ok: true, value: 'クリス・マキセ' });
    expect(validateSelfName('阿·伟')).toEqual({ ok: true, value: '阿·伟' });
    expect(validateSelfName('Jean-Luc')).toEqual({ ok: true, value: 'Jean-Luc' });
    expect(validateSelfName('Dr. Rin')).toEqual({ ok: true, value: 'Dr. Rin' });
    expect(validateSelfName('R2D2')).toEqual({ ok: true, value: 'R2D2' });
    expect(validateSelfName('')).toEqual({ ok: true, value: '' });
    expect(validateSelfName('   ')).toEqual({ ok: true, value: '' });
  });

  it('caps at 40 code points (fail with hint, not silent truncate)', () => {
    const max = '伟'.repeat(SELF_NAME_MAX_CODEPOINTS);
    expect(validateSelfName(max)).toEqual({ ok: true, value: max });
    expect(validateSelfName(max + '伟')).toEqual({ ok: false, error: SELF_NAME_TOO_LONG_HINT });
    // Astral LETTERS count once per code point (not UTF-16 units); emoji are
    // rejected by the allowlist, so use a CJK Ext-B letter here.
    const astral = '𠀀'.repeat(SELF_NAME_MAX_CODEPOINTS);
    expect(validateSelfName(astral)).toEqual({ ok: true, value: astral });
    expect(validateSelfName(astral + '𠀀')).toEqual({ ok: false, error: SELF_NAME_TOO_LONG_HINT });
  });

  it('rejects dangerous characters whole-name with an explicit hint (no silent repair)', () => {
    for (const name of [
      '阿\n伟',              // interior control char
      '阿\u0000伟',          // NUL
      '阿\u2028伟',          // line separator
      '阿\u2029伟',          // paragraph separator
      '阿\u200b伟',          // zero-width space (format char)
      '「阿伟」',            // Japanese quotes
      '阿"伟',              // ASCII quote
      'SYSTEM: 阿伟',        // colon / fake header
      '阿(伟)',              // brackets
      '阿（伟）',            // fullwidth brackets
      'a/b', 'a\\b', 'a_b',  // slash, backslash, underscore
      '🙂', '阿🙂伟',         // emoji
      '阿！伟',              // fullwidth punctuation
    ]) {
      expect(validateSelfName(name), JSON.stringify(name)).toEqual({
        ok: false,
        error: SELF_NAME_INVALID_CHARS_HINT,
      });
    }
  });

  it('edge-trims plain space U+0020 only; other edge whitespace rejects whole-name', () => {
    expect(validateSelfName('  阿伟  ')).toEqual({ ok: true, value: '阿伟' });
    for (const name of [
      '\n阿伟', '阿伟\n',          // edge newlines
      '\t阿伟', '阿伟\t',          // edge tabs
      '\u00a0阿伟', '阿伟\u00a0',  // edge NBSP
      '\ufeff阿伟',                // edge BOM (format char)
    ]) {
      expect(validateSelfName(name), JSON.stringify(name)).toEqual({
        ok: false,
        error: SELF_NAME_INVALID_CHARS_HINT,
      });
    }
  });

  it('rejects reserved Okabe identities in all common variants with the exact hint', () => {
    for (const name of [
      '岡部', '岡部倫太郎', '冈部', '冈部伦太郎',
      '鳳凰院凶真', '凤凰院凶真',
      'Okabe', 'OKABE', 'ＯＫＡＢＥ', 'okabe',
      'Rintaro Okabe', 'rintaro-okabe', 'Okabe Rintaro',
      'Hououin Kyouma', 'hououin_kyouma', '鳳凰院・凶真', '凤凰院 凶真', '岡 部',
    ]) {
      expect(isReservedOkabeName(name), name).toBe(true);
      expect(validateSelfName(name)).toEqual({ ok: false, error: RESERVED_OKABE_NAME_HINT });
    }
    expect(RESERVED_OKABE_NAME_HINT).toBe('如需使用冈部身份，请选择 okabe 模式。');
  });

  it('does not flag normal names as reserved', () => {
    for (const name of ['阿伟', 'Salieri', 'クリス', '小冈']) {
      expect(isReservedOkabeName(name), name).toBe(false);
    }
  });

  it('persists, restores, and clears via localStorage', () => {
    expect(loadStoredSelfName()).toBe('');
    persistSelfName('阿伟');
    expect(window.localStorage.getItem(SELF_NAME_STORAGE_KEY)).toBe('阿伟');
    expect(loadStoredSelfName()).toBe('阿伟');
    persistSelfName('');
    expect(loadStoredSelfName()).toBe('');
  });

  it('returns empty for illegal, overlong, or reserved values found in localStorage', () => {
    for (const bad of ['「阿伟」', 'SYSTEM: 阿伟', '🙂', '伟'.repeat(SELF_NAME_MAX_CODEPOINTS + 1), '冈部', 'Okabe Rintaro', '\n阿伟', '阿伟\t', '\u00a0阿伟', '\ufeff阿伟']) {
      window.localStorage.setItem(SELF_NAME_STORAGE_KEY, bad);
      expect(loadStoredSelfName(), JSON.stringify(bad)).toBe('');
    }
  });
});

describe('SettingsModal self name field (Slice B)', () => {
  it('renders the field with the current value and saves the trimmed name', async () => {
    const onSaveSettings = vi.fn();
    render(<SettingsModal {...baseProps} selfName="阿伟" onSaveSettings={onSaveSettings} />);

    const input = screen.getByTestId('self-name-input') as HTMLInputElement;
    expect(input.value).toBe('阿伟');
    fireEvent.change(input, { target: { value: '  新名字  ' } });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => expect(onSaveSettings).toHaveBeenCalled());
    expect(onSaveSettings.mock.calls[0][0].selfName).toBe('新名字');
  });

  it('clears the name when the field is emptied', async () => {
    const onSaveSettings = vi.fn();
    render(<SettingsModal {...baseProps} selfName="阿伟" onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByTestId('self-name-input'), { target: { value: '' } });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => expect(onSaveSettings).toHaveBeenCalled());
    expect(onSaveSettings.mock.calls[0][0].selfName).toBe('');
  });

  it('blocks saving a reserved Okabe name and shows the exact hint', async () => {
    const onSaveSettings = vi.fn();
    render(<SettingsModal {...baseProps} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByTestId('self-name-input'), { target: { value: '凤凰院凶真' } });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('如需使用冈部身份，请选择 okabe 模式。');
    });
    expect(onSaveSettings).not.toHaveBeenCalled();
  });

  it('blocks saving an overlong name', async () => {
    const onSaveSettings = vi.fn();
    render(<SettingsModal {...baseProps} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByTestId('self-name-input'), {
      target: { value: '伟'.repeat(SELF_NAME_MAX_CODEPOINTS + 1) },
    });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(SELF_NAME_TOO_LONG_HINT);
    });
    expect(onSaveSettings).not.toHaveBeenCalled();
  });

  it('blocks saving a name with disallowed characters and shows the allowlist hint', async () => {
    const onSaveSettings = vi.fn();
    render(<SettingsModal {...baseProps} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByTestId('self-name-input'), { target: { value: '「阿伟」' } });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(SELF_NAME_INVALID_CHARS_HINT);
    });
    expect(onSaveSettings).not.toHaveBeenCalled();
  });

  it('blocks saving a name with edge NBSP and shows the allowlist hint', async () => {
    // Single-line <input> strips \r\n per HTML spec, so an edge newline can
    // never even enter the field (validateSelfName still rejects it — covered
    // by the helper tests). NBSP survives the input, so it proves the UI path.
    const onSaveSettings = vi.fn();
    render(<SettingsModal {...baseProps} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByTestId('self-name-input'), { target: { value: '\u00a0阿伟' } });
    fireEvent.click(screen.getByText('保存修改'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(SELF_NAME_INVALID_CHARS_HINT);
    });
    expect(onSaveSettings).not.toHaveBeenCalled();
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
    expect(screen.getByTestId('personality-value')).toHaveTextContent('0.0');
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

  it('renders Identity settings with plain labels only (okabe default; no lore/about notes)', () => {
    render(<SettingsModal {...baseProps} onSaveSettings={vi.fn()} />);
    expect(screen.getByTestId('identity-settings')).toBeTruthy();
    expect(screen.getByTestId('identity-mode-okabe')).toBeChecked();
    expect(screen.getByTestId('identity-mode-self')).not.toBeChecked();
    expect(screen.getByText('冈部（默认）')).toBeTruthy();
    expect(screen.getByText('自己')).toBeTruthy();
    expect(screen.queryByTestId('identity-boundary-note')).toBeNull();
    expect(screen.queryByTestId('identity-no-about')).toBeNull();
    expect(screen.queryByText(/非生身本尊/)).toBeNull();
    expect(screen.queryByText(/About this Amadeus/)).toBeNull();
    // No in-session mode switch control — only default for *new* conversations
    expect(screen.queryByTestId('identity-mode-switch-active')).toBeNull();
    expect(screen.queryByLabelText(/当前会话身份/)).toBeNull();
  });

  it('saves the selected default identity_mode (self) without nickname inference UI', async () => {
    const onSaveSettings = vi.fn().mockResolvedValue(undefined);
    render(
      <SettingsModal
        {...baseProps}
        identityMode="okabe"
        onSaveSettings={onSaveSettings}
      />,
    );
    fireEvent.click(screen.getByTestId('identity-mode-self'));
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    await waitFor(() =>
      expect(onSaveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ identityMode: 'self', temperature: 0.0 }),
      ),
    );
    expect(screen.queryByText(/Christina/)).toBeNull();
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
