import React, { useState, useEffect } from 'react';

export type CredentialStatus = 'checking' | 'configured' | 'not_configured' | 'saving' | 'error';

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
  systemPrompt: string;
  enableTts: boolean;
  sovitsUrl: string;
  enableBgm: boolean;
  bgmVolume: number;
  enableSfx: boolean;
  sfxVolume: number;
  temperature: number;
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
  onClearWebSearchCredential?: () => void | Promise<void>;
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
  systemPrompt,
  enableTts,
  sovitsUrl,
  enableBgm,
  bgmVolume,
  enableSfx,
  sfxVolume,
  temperature,
  providerDisplayName,
  providerBaseUrl,
  baseUrlConfigurable,
  model,
  modelOptions,
  reasoningEffort,
  onSaveSettings,
  onClearWebSearchCredential,
}) => {
  const [activeTab, setActiveTab] = useState<'basic' | 'voice'>('basic');
  const [localWorldline, setLocalWorldline] = useState<'steins_gate' | 'beta'>(worldline);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [localWebSearchApiKey, setLocalWebSearchApiKey] = useState(webSearchApiKey);
  const [localSystemPrompt, setLocalSystemPrompt] = useState(systemPrompt);
  const [localEnableTts, setLocalEnableTts] = useState(enableTts);
  const [localSovitsUrl, setLocalSovitsUrl] = useState(sovitsUrl);
  const [localTemperature, setLocalTemperature] = useState(temperature);
  const [localBaseUrl, setLocalBaseUrl] = useState(providerBaseUrl || '');
  const [localModel, setLocalModel] = useState(model);
  const [localReasoningIdx, setLocalReasoningIdx] = useState(
    reasoningEffort === 'xhigh' ? 1 : 0
  );
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isClearingWebSearch, setIsClearingWebSearch] = useState(false);
  const displayCredentialStatus: CredentialStatus = credentialStatus
    ?? (isSaving ? 'saving' : credentialConfigured ? 'configured' : 'not_configured');
  const displayWebSearchStatus: CredentialStatus = webSearchStatus
    ?? (isSaving || isClearingWebSearch ? 'saving' : webSearchConfigured ? 'configured' : 'not_configured');

  useEffect(() => {
    if (isOpen) {
      setActiveTab('basic');
      setLocalWorldline(worldline);
      setLocalApiKey(apiKey);
      setLocalWebSearchApiKey(webSearchApiKey);
      setSaveError(null);
      setLocalSystemPrompt(systemPrompt);
      setLocalEnableTts(enableTts);
      setLocalSovitsUrl(sovitsUrl);
      setLocalTemperature(temperature);
      setLocalBaseUrl(providerBaseUrl || '');
      setLocalModel(model);
      setLocalReasoningIdx(reasoningEffort === 'xhigh' ? 1 : 0);
    }
  }, [isOpen, worldline, apiKey, webSearchApiKey, systemPrompt, enableTts, sovitsUrl, temperature, providerBaseUrl, model, reasoningEffort]);

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
        systemPrompt: localSystemPrompt,
        enableTts: localEnableTts,
        sovitsUrl: localSovitsUrl,
        enableBgm: enableBgm,
        bgmVolume: bgmVolume,
        enableSfx: enableSfx,
        sfxVolume: sfxVolume,
        temperature: localTemperature,
        baseUrl: localBaseUrl,
        model: localModel,
        reasoningEffort: (['high', 'xhigh'] as const)[localReasoningIdx],
      });
      onClose();
    } catch (error) {
      const code = error instanceof Error ? error.message : 'settings_save_failed';
      setSaveError(code);
    } finally {
      setIsSaving(false);
    }
  };

  const handleClearWebSearchCredential = async () => {
    if (!onClearWebSearchCredential || isClearingWebSearch) return;
    setIsClearingWebSearch(true);
    setSaveError(null);
    try {
      await onClearWebSearchCredential();
      setLocalWebSearchApiKey('');
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
              <div className="setting-field">
                <label>{providerDisplayName.toUpperCase()} API 密钥</label>
                <input
                  type="password"
                  value={localApiKey}
                  onChange={(e) => setLocalApiKey(e.target.value)}
                  className="salieri-input"
                  data-testid="credential-input"
                  placeholder={displayCredentialStatus === 'configured' ? '••••••••' : '输入后仅在本机安全存储'}
                  autoComplete="new-password"
                />
                <p
                  className={`credential-status ${displayCredentialStatus.replace('_', '-')}`}
                  data-testid="credential-status"
                  aria-live="polite"
                >
                  {displayCredentialStatus === 'checking' && '正在读取凭据状态…'}
                  {displayCredentialStatus === 'configured' && '已配置 · 留空保存可保持当前密钥不变'}
                  {displayCredentialStatus === 'not_configured' && '尚未配置 · 保存后不会在页面回显密钥'}
                  {displayCredentialStatus === 'saving' && '保存中…'}
                  {displayCredentialStatus === 'error' && '保存失败 · 请检查连接后重试'}
                </p>
              </div>
              <div className="setting-field field-full web-search-setting">
                <label>联网搜索（Tavily）</label>
                <input
                  type="password"
                  value={localWebSearchApiKey}
                  onChange={(e) => setLocalWebSearchApiKey(e.target.value)}
                  className="salieri-input"
                  data-testid="web-search-credential-input"
                  placeholder={displayWebSearchStatus === 'configured' ? '••••••••••••••••' : '输入 Tavily API 密钥'}
                  autoComplete="new-password"
                />
                <p className={`credential-status ${displayWebSearchStatus.replace('_', '-')}`} data-testid="web-search-credential-status" aria-live="polite">
                  {displayWebSearchStatus === 'checking' && '正在检查搜索凭据…'}
                  {displayWebSearchStatus === 'configured' && '已配置 · 留空保存会保留现有密钥'}
                  {displayWebSearchStatus === 'not_configured' && '未配置 · DuckDuckGo 免费回退可能不可用'}
                  {displayWebSearchStatus === 'saving' && '正在保存…'}
                  {displayWebSearchStatus === 'error' && '保存失败 · 请检查连接后重试'}
                </p>
                {webSearchConfigured && onClearWebSearchCredential && (
                  <button
                    type="button"
                    className="salieri-btn cancel-btn"
                    data-testid="clear-web-search-credential"
                    onClick={() => { void handleClearWebSearchCredential(); }}
                    disabled={isSaving || isClearingWebSearch}
                  >
                    清除 Tavily 密钥
                  </button>
                )}
              </div>
              <div className="setting-field personality-field">
                <label>个性</label>
                <div className="slider-row personality-slider-row">
                  <input
                    data-testid="personality-range"
                    type="range"
                    min="0"
                    max="2"
                    step="0.1"
                    value={localTemperature}
                    onChange={(e) => setLocalTemperature(parseFloat(e.target.value))}
                    className="volume-slider personality-range"
                    style={{ '--val': `${(localTemperature / 2) * 100}%` } as React.CSSProperties}
                    aria-label="个性"
                  />
                  <span className="volume-label" data-testid="personality-value">{localTemperature.toFixed(1)}</span>
                </div>
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
