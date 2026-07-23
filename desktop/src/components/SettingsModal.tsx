import React, { useState, useEffect } from 'react';

export type CredentialStatus = 'checking' | 'configured' | 'not_configured' | 'saving' | 'error';

/** Paid web-search backends only (Tavily | Firecrawl). No free HTML fallback. */
export type WebSearchProviderId = 'tavily' | 'firecrawl';

export interface WebSearchProviderInfo {
  id: WebSearchProviderId;
  label: string;
  configured: boolean;
  docs_url?: string;
  hint?: string;
}

export interface WebSearchSettingsSnapshot {
  active_provider: WebSearchProviderId;
  providers: Record<WebSearchProviderId, WebSearchProviderInfo>;
  fallback?: string;
  fallback_note?: string;
  configured?: boolean;
}

/** Chat sampling temperature exposed as Settings「回复随机性」. Not Persona content. */
export const CHAT_TEMPERATURE_MIN = 0;
export const CHAT_TEMPERATURE_MAX = 1.2;
export const CHAT_TEMPERATURE_STEP = 0.1;
/** New-install default; daily stable band is 0–0.5 (0 is the verified safe point). */
export const CHAT_TEMPERATURE_DEFAULT = 0.0;

export function clampChatTemperature(value: number): number {
  if (!Number.isFinite(value)) return CHAT_TEMPERATURE_DEFAULT;
  return Math.max(CHAT_TEMPERATURE_MIN, Math.min(CHAT_TEMPERATURE_MAX, value));
}

export interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  worldline: 'steins_gate' | 'beta';
  apiKey: string;
  credentialConfigured: boolean;
  credentialStatus?: CredentialStatus;
  webSearchApiKey?: string;
  webSearchConfigured?: boolean;
  webSearchStatus?: CredentialStatus;
  webSearchActiveProvider?: WebSearchProviderId;
  webSearchProviders?: Partial<Record<WebSearchProviderId, WebSearchProviderInfo>>;
  webSearchFallbackNote?: string;
  systemPrompt: string;
  enableTts: boolean;
  sovitsUrl: string;
  enableBgm: boolean;
  bgmVolume: number;
  enableSfx: boolean;
  sfxVolume: number;
  temperature: number;
  /** One-shot notice after migrating a legacy localStorage value > max (1.2) */
  temperatureMigrationNotice?: boolean;
  onDismissTemperatureMigrationNotice?: () => void;
  providerDisplayName: string;
  providerBaseUrl?: string | null;
  baseUrlConfigurable: boolean;
  model: string;
  modelOptions: string[];
  reasoningEffort: 'high' | 'xhigh';
  onSaveSettings: (settings: {
    worldline: 'steins_gate' | 'beta';
    apiKey: string;
    webSearchApiKey: string;
    webSearchProvider: WebSearchProviderId;
    systemPrompt: string;
    enableTts: boolean;
    sovitsUrl: string;
    enableBgm: boolean;
    bgmVolume: number;
    enableSfx: boolean;
    sfxVolume: number;
    temperature: number;
    baseUrl: string;
    model: string;
    reasoningEffort: 'high' | 'xhigh';
  }) => void | Promise<void>;
  onSelectWebSearchProvider?: (provider: WebSearchProviderId) => void | Promise<void>;
  onClearWebSearchCredential?: (provider: WebSearchProviderId) => void | Promise<void>;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  worldline,
  apiKey,
  credentialConfigured,
  credentialStatus,
  webSearchApiKey = '',
  webSearchConfigured = false,
  webSearchStatus,
  webSearchActiveProvider = 'tavily',
  webSearchProviders,
  webSearchFallbackNote = '请至少配置一个搜索提供商并设为当前使用。',
  systemPrompt,
  enableTts,
  sovitsUrl,
  enableBgm,
  bgmVolume,
  enableSfx,
  sfxVolume,
  temperature,
  temperatureMigrationNotice = false,
  onDismissTemperatureMigrationNotice,
  providerDisplayName,
  providerBaseUrl,
  baseUrlConfigurable,
  model,
  modelOptions,
  reasoningEffort,
  onSaveSettings,
  onSelectWebSearchProvider,
  onClearWebSearchCredential,
}) => {
  const [activeTab, setActiveTab] = useState<'basic' | 'voice'>('basic');
  const [localWorldline, setLocalWorldline] = useState<'steins_gate' | 'beta'>(worldline);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [localWebSearchApiKey, setLocalWebSearchApiKey] = useState(webSearchApiKey);
  const [localWebSearchProvider, setLocalWebSearchProvider] = useState<WebSearchProviderId>(webSearchActiveProvider);
  const [replacingWebSearchKey, setReplacingWebSearchKey] = useState(false);
  const [localSystemPrompt, setLocalSystemPrompt] = useState(systemPrompt);
  const [localEnableTts, setLocalEnableTts] = useState(enableTts);
  const [localSovitsUrl, setLocalSovitsUrl] = useState(sovitsUrl);
  const [localTemperature, setLocalTemperature] = useState(() => clampChatTemperature(temperature));
  const [localBaseUrl, setLocalBaseUrl] = useState(providerBaseUrl || '');
  const [localModel, setLocalModel] = useState(model);
  const [localReasoningIdx, setLocalReasoningIdx] = useState(
    reasoningEffort === 'xhigh' ? 1 : 0
  );
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isClearingWebSearch, setIsClearingWebSearch] = useState(false);
  const [isSwitchingSearchProvider, setIsSwitchingSearchProvider] = useState(false);
  // In-flight save/clear always wins over the parent snapshot status.
  const displayCredentialStatus: CredentialStatus = isSaving
    ? 'saving'
    : (credentialStatus ?? (credentialConfigured ? 'configured' : 'not_configured'));
  const activeSearchMeta = webSearchProviders?.[localWebSearchProvider];
  const activeSearchConfigured = Boolean(activeSearchMeta?.configured ?? webSearchConfigured);
  const displayWebSearchStatus: CredentialStatus =
    (isSaving || isClearingWebSearch || isSwitchingSearchProvider)
      ? 'saving'
      : (webSearchStatus ?? (activeSearchConfigured ? 'configured' : 'not_configured'));
  const temperatureWarning =
    localTemperature >= 0.9
      ? 'strong' as const
      : localTemperature >= 0.8
        ? 'mild' as const
        : null;
  const searchProviderCards: WebSearchProviderInfo[] = (
    ['tavily', 'firecrawl'] as const
  ).map((id) => webSearchProviders?.[id] ?? {
    id,
    label: id === 'tavily' ? 'Tavily' : 'Firecrawl',
    configured: id === localWebSearchProvider ? activeSearchConfigured : false,
    docs_url: id === 'tavily' ? 'https://app.tavily.com/home' : 'https://www.firecrawl.dev/app/api-keys',
    hint: id === 'tavily'
      ? '面向 AI 检索；免费额度见官方控制台。'
      : 'Search + 页面内容；免费额度见官方控制台。',
  });

  useEffect(() => {
    if (isOpen) {
      setActiveTab('basic');
      setLocalWorldline(worldline);
      setLocalApiKey(apiKey);
      setLocalWebSearchApiKey(webSearchApiKey);
      setLocalWebSearchProvider(webSearchActiveProvider);
      setReplacingWebSearchKey(false);
      setSaveError(null);
      setLocalSystemPrompt(systemPrompt);
      setLocalEnableTts(enableTts);
      setLocalSovitsUrl(sovitsUrl);
      setLocalTemperature(clampChatTemperature(temperature));
      setLocalBaseUrl(providerBaseUrl || '');
      setLocalModel(model);
      setLocalReasoningIdx(reasoningEffort === 'xhigh' ? 1 : 0);
    }
  }, [isOpen, worldline, apiKey, webSearchApiKey, webSearchActiveProvider, systemPrompt, enableTts, sovitsUrl, temperature, providerBaseUrl, model, reasoningEffort]);

  if (!isOpen) return null;

  const handleSave = async () => {
    if (isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      await onSaveSettings({
        worldline: localWorldline,
        apiKey: localApiKey,
        webSearchApiKey: localWebSearchApiKey,
        webSearchProvider: localWebSearchProvider,
        systemPrompt: localSystemPrompt,
        enableTts: localEnableTts,
        sovitsUrl: localSovitsUrl,
        enableBgm: enableBgm,
        bgmVolume: bgmVolume,
        enableSfx: enableSfx,
        sfxVolume: sfxVolume,
        temperature: clampChatTemperature(localTemperature),
        baseUrl: localBaseUrl,
        model: localModel,
        reasoningEffort: (['high', 'xhigh'] as const)[localReasoningIdx],
      });
      setReplacingWebSearchKey(false);
      onClose();
    } catch (error) {
      const code = error instanceof Error ? error.message : 'settings_save_failed';
      setSaveError(code);
    } finally {
      setIsSaving(false);
    }
  };

  const handleSelectSearchProvider = async (provider: WebSearchProviderId) => {
    if (provider === localWebSearchProvider || isSwitchingSearchProvider) return;
    setLocalWebSearchProvider(provider);
    setLocalWebSearchApiKey('');
    setReplacingWebSearchKey(false);
    if (!onSelectWebSearchProvider) return;
    setIsSwitchingSearchProvider(true);
    setSaveError(null);
    try {
      await onSelectWebSearchProvider(provider);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'web_search_provider_switch_failed');
    } finally {
      setIsSwitchingSearchProvider(false);
    }
  };

  const handleClearWebSearchCredential = async () => {
    if (!onClearWebSearchCredential || isClearingWebSearch) return;
    setIsClearingWebSearch(true);
    setSaveError(null);
    try {
      await onClearWebSearchCredential(localWebSearchProvider);
      setLocalWebSearchApiKey('');
      setReplacingWebSearchKey(false);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'web_search_credential_clear_failed');
    } finally {
      setIsClearingWebSearch(false);
    }
  };

  return (
    <div className="salieri-modal-overlay">
      <div className="salieri-settings-modal">
        {/* Config ring background */}
        <div className="config-ring-bg">
          <img src="/assets/ui/ring_config.png" alt="" />
        </div>
        {/* Inner content */}
        <div className="modal-inner-content">
          <div className="modal-header">
            <h2>SYSTEM CONFIG</h2>
            <button className="close-btn" onClick={onClose}>×</button>
          </div>
          <nav className="tab-pills-row">
            <button aria-label="BASIC" className={`tab-pill ${activeTab === 'basic' ? 'active' : ''}`} onClick={() => setActiveTab('basic')}>
              BASIC
            </button>
            <button aria-label="VOICE" className={`tab-pill ${activeTab === 'voice' ? 'active' : ''}`} onClick={() => setActiveTab('voice')}>
              VOICE
            </button>
          </nav>
          <main className="tab-panels">
            {/* BASIC tab */}
            <section className={`tab-panel ${activeTab === 'basic' ? 'active' : ''}`}>
              <div className="setting-field credential-card">
                <div className="credential-card-header">
                  <label>{providerDisplayName.toUpperCase()} API 密钥</label>
                  <span
                    className={`credential-badge ${displayCredentialStatus.replace('_', '-')}`}
                    data-testid="credential-badge"
                  >
                    {displayCredentialStatus === 'configured' && '已连接'}
                    {displayCredentialStatus === 'not_configured' && '未配置'}
                    {displayCredentialStatus === 'checking' && '检查中'}
                    {displayCredentialStatus === 'saving' && '保存中'}
                    {displayCredentialStatus === 'error' && '失败'}
                  </span>
                </div>
                <p className="setting-field-hint">
                  密钥只写不读：已连接时输入框为空表示「不改动」；仅在粘贴新密钥后点「保存修改」才会覆盖。
                </p>
                <input
                  type="password"
                  value={localApiKey}
                  onChange={(e) => setLocalApiKey(e.target.value)}
                  className="salieri-input"
                  data-testid="credential-input"
                  placeholder={displayCredentialStatus === 'configured' ? '已连接 · 粘贴新密钥以更换' : '粘贴 API 密钥（仅本机存储）'}
                  autoComplete="new-password"
                />
                <p
                  className={`credential-status ${displayCredentialStatus.replace('_', '-')}`}
                  data-testid="credential-status"
                  aria-live="polite"
                >
                  {displayCredentialStatus === 'checking' && '正在读取凭据状态…'}
                  {displayCredentialStatus === 'configured' && '已连接 · 不会在界面回显密钥'}
                  {displayCredentialStatus === 'not_configured' && '尚未配置 · 保存后不会在页面回显密钥'}
                  {displayCredentialStatus === 'saving' && '保存中…'}
                  {displayCredentialStatus === 'error' && '保存失败 · 请检查连接后重试'}
                </p>
              </div>
              <div className="setting-field field-full web-search-setting credential-card">
                <div className="credential-card-header">
                  <label>联网搜索</label>
                  <span
                    className={`credential-badge ${displayWebSearchStatus.replace('_', '-')}`}
                    data-testid="web-search-badge"
                  >
                    {displayWebSearchStatus === 'configured' && '已连接'}
                    {displayWebSearchStatus === 'not_configured' && '未配置'}
                    {displayWebSearchStatus === 'checking' && '检查中'}
                    {displayWebSearchStatus === 'saving' && '保存中'}
                    {displayWebSearchStatus === 'error' && '失败'}
                  </span>
                </div>
                <p className="setting-field-hint" data-testid="web-search-hint">
                  选择主力提供商并各自保存密钥；可随时切换。{webSearchFallbackNote}
                </p>
                <div className="search-provider-grid" data-testid="web-search-provider-grid" role="radiogroup" aria-label="搜索提供商">
                  {searchProviderCards.map((card) => {
                    const selected = localWebSearchProvider === card.id;
                    return (
                      <button
                        key={card.id}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        className={`search-provider-card ${selected ? 'active' : ''} ${card.configured ? 'configured' : ''}`}
                        data-testid={`web-search-provider-${card.id}`}
                        onClick={() => { void handleSelectSearchProvider(card.id); }}
                        disabled={isSaving || isClearingWebSearch || isSwitchingSearchProvider}
                      >
                        <span className="search-provider-name">{card.label}</span>
                        <span className="search-provider-state">
                          {card.configured ? '密钥已存' : '未存密钥'}
                          {selected ? ' · 当前使用' : ''}
                        </span>
                        {card.docs_url && (
                          <a
                            className="search-provider-docs"
                            href={card.docs_url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                          >
                            获取密钥
                          </a>
                        )}
                      </button>
                    );
                  })}
                </div>
                {activeSearchConfigured && !replacingWebSearchKey ? (
                  <div className="credential-actions">
                    <p className={`credential-status configured`} data-testid="web-search-credential-status" aria-live="polite">
                      {searchProviderCards.find((c) => c.id === localWebSearchProvider)?.label ?? localWebSearchProvider}
                      {' '}已连接 · 密钥不回显
                    </p>
                    <button
                      type="button"
                      className="salieri-btn cancel-btn"
                      data-testid="replace-web-search-credential"
                      onClick={() => setReplacingWebSearchKey(true)}
                      disabled={isSaving || isClearingWebSearch}
                    >
                      更换密钥
                    </button>
                    {onClearWebSearchCredential && (
                      <button
                        type="button"
                        className="salieri-btn cancel-btn"
                        data-testid="clear-web-search-credential"
                        onClick={() => { void handleClearWebSearchCredential(); }}
                        disabled={isSaving || isClearingWebSearch}
                      >
                        清除此提供商密钥
                      </button>
                    )}
                  </div>
                ) : (
                  <>
                    <input
                      type="password"
                      value={localWebSearchApiKey}
                      onChange={(e) => setLocalWebSearchApiKey(e.target.value)}
                      className="salieri-input"
                      data-testid="web-search-credential-input"
                      placeholder={`粘贴 ${searchProviderCards.find((c) => c.id === localWebSearchProvider)?.label ?? ''} API 密钥`}
                      autoComplete="new-password"
                    />
                    <p className={`credential-status ${displayWebSearchStatus.replace('_', '-')}`} data-testid="web-search-credential-status" aria-live="polite">
                      {displayWebSearchStatus === 'checking' && '正在检查搜索凭据…'}
                      {displayWebSearchStatus === 'configured' && replacingWebSearchKey && '粘贴新密钥后点「保存修改」以覆盖'}
                      {displayWebSearchStatus === 'not_configured' && '尚未配置此提供商 · 保存后不会回显密钥'}
                      {displayWebSearchStatus === 'saving' && '正在保存…'}
                      {displayWebSearchStatus === 'error' && '保存失败 · 请检查连接后重试'}
                    </p>
                    {replacingWebSearchKey && (
                      <button
                        type="button"
                        className="salieri-btn cancel-btn"
                        data-testid="cancel-replace-web-search-credential"
                        onClick={() => {
                          setReplacingWebSearchKey(false);
                          setLocalWebSearchApiKey('');
                        }}
                        disabled={isSaving}
                      >
                        取消更换
                      </button>
                    )}
                  </>
                )}
              </div>
              <div className="setting-field personality-field">
                <label>回复随机性</label>
                <p className="setting-field-hint" data-testid="temperature-hint">
                  越高越发散、越容易跑题；不改变角色设定本身。默认 0（最稳）；需要轻微变化可用 0.1～0.3。
                </p>
                {temperatureMigrationNotice && (
                  <p
                    className="temperature-migration-notice"
                    data-testid="temperature-migration-notice"
                    role="status"
                  >
                    已从过高采样降至安全上限 1.2。
                    {onDismissTemperatureMigrationNotice && (
                      <button
                        type="button"
                        className="temperature-migration-dismiss"
                        onClick={onDismissTemperatureMigrationNotice}
                      >
                        知道了
                      </button>
                    )}
                  </p>
                )}
                <div className="personality-control" data-testid="personality-control">
                  <div className="slider-row personality-slider-row">
                    <input
                      data-testid="personality-range"
                      type="range"
                      min={CHAT_TEMPERATURE_MIN}
                      max={CHAT_TEMPERATURE_MAX}
                      step={CHAT_TEMPERATURE_STEP}
                      value={localTemperature}
                      onChange={(e) => setLocalTemperature(clampChatTemperature(parseFloat(e.target.value)))}
                      className="volume-slider personality-range"
                      style={{ '--val': `${(localTemperature / CHAT_TEMPERATURE_MAX) * 100}%` } as React.CSSProperties}
                      aria-label="回复随机性"
                    />
                    <span className="volume-label personality-value" data-testid="personality-value">
                      {localTemperature.toFixed(1)}
                    </span>
                  </div>
                  <div className="personality-endpoints" aria-hidden="true">
                    <span className="slider-endpoint" data-testid="temperature-endpoint-min">稳定 / 贴人设</span>
                    <span className="slider-endpoint" data-testid="temperature-endpoint-max">发散 / 易跑偏</span>
                  </div>
                </div>
                {temperatureWarning === 'mild' && (
                  <p className="temperature-warning temperature-warning-mild" data-testid="temperature-warning">
                    当前采样偏高，回复可能不够稳定。
                  </p>
                )}
                {temperatureWarning === 'strong' && (
                  <p className="temperature-warning temperature-warning-strong" data-testid="temperature-warning">
                    当前采样很高，容易跑题或句式不稳。
                  </p>
                )}
              </div>
              {baseUrlConfigurable && (
              <div className="setting-field">
                <label>模型</label>
                {baseUrlConfigurable ? (
                  <input type="text" value={localModel} onChange={(e) => setLocalModel(e.target.value)} className="salieri-input" placeholder="local-model" />
                ) : (
                  <select value={localModel} onChange={(e) => setLocalModel(e.target.value)} className="salieri-select">
                    {Array.from(new Set([localModel, ...modelOptions].filter(Boolean))).map((modelId) => (
                      <option key={modelId} value={modelId}>{modelId}</option>
                    ))}
                  </select>
                )}
              </div>
              )}
              {baseUrlConfigurable && (
                <div className="setting-field field-full">
                  <label>OpenAI-compatible Base URL</label>
                  <input type="url" value={localBaseUrl} onChange={(e) => setLocalBaseUrl(e.target.value)} className="salieri-input" placeholder="http://127.0.0.1:1234/v1" />
                </div>
              )}
              <div className="setting-field">
                <label>当前世界线</label>
                <select value={localWorldline} onChange={(e) => setLocalWorldline(e.target.value as 'steins_gate' | 'beta')} className="salieri-select">
                  <option value="steins_gate">Steins;Gate (1.048596%)</option>
                  <option value="beta">β 世界线 (1.130205%)</option>
                </select>
              </div>
            </section>
            {/* VOICE tab */}
            <section className={`tab-panel ${activeTab === 'voice' ? 'active' : ''}`}>
              <div className="setting-field">
                <label className="checkbox-label">
                  <input type="checkbox" checked={localEnableTts} onChange={(e) => setLocalEnableTts(e.target.checked)} className="salieri-checkbox" />
                  启用语音合成 (TTS)
                </label>
              </div>
              <div className="setting-field field-full">
                <label>GPT-SoVITS 服务端点</label>
                <input type="text" value={localSovitsUrl} onChange={(e) => setLocalSovitsUrl(e.target.value)} className="salieri-input" placeholder="http://127.0.0.1:9880" />
              </div>
            </section>
          </main>
          {saveError && (
            <p className="settings-save-error" role="alert">
              {saveError === 'credential_save_failed'
                ? 'API 密钥保存失败，请检查后端连接后重试。'
                : saveError === 'provider_config_save_failed'
                  ? '供应商配置保存失败，请检查地址和模型名。'
                  : '设置保存失败，请稍后重试。'}
            </p>
          )}
          <div className="modal-footer">
            <button className="salieri-btn cancel-btn" onClick={onClose}>取消</button>
            <button className="salieri-btn save-btn" onClick={() => { void handleSave(); }} disabled={isSaving}>
              {isSaving ? '保存中…' : '保存修改'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
export default SettingsModal;
