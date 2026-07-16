import React, { useState, useEffect } from 'react';

export interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  worldline: 'steins_gate' | 'beta';
  apiKey: string;
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
  }) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  worldline,
  apiKey,
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
}) => {
  const [activeTab, setActiveTab] = useState<'basic' | 'voice'>('basic');
  const [localWorldline, setLocalWorldline] = useState<'steins_gate' | 'beta'>(worldline);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [localSystemPrompt, setLocalSystemPrompt] = useState(systemPrompt);
  const [localEnableTts, setLocalEnableTts] = useState(enableTts);
  const [localSovitsUrl, setLocalSovitsUrl] = useState(sovitsUrl);
  const [localTemperature, setLocalTemperature] = useState(temperature);
  const [localBaseUrl, setLocalBaseUrl] = useState(providerBaseUrl || '');
  const [localModel, setLocalModel] = useState(model);
  const [localReasoningIdx, setLocalReasoningIdx] = useState(
    reasoningEffort === 'xhigh' ? 1 : 0
  );

  useEffect(() => {
    if (isOpen) {
      setActiveTab('basic');
      setLocalWorldline(worldline);
      setLocalApiKey(apiKey);
      setLocalSystemPrompt(systemPrompt);
      setLocalEnableTts(enableTts);
      setLocalSovitsUrl(sovitsUrl);
      setLocalTemperature(temperature);
      setLocalBaseUrl(providerBaseUrl || '');
      setLocalModel(model);
      setLocalReasoningIdx(reasoningEffort === 'xhigh' ? 1 : 0);
    }
  }, [isOpen, worldline, apiKey, systemPrompt, enableTts, sovitsUrl, temperature, providerBaseUrl, model, reasoningEffort]);

  if (!isOpen) return null;

  const handleSave = () => {
    onSaveSettings({
      worldline: localWorldline,
      apiKey: localApiKey,
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
                <input type="password" value={localApiKey} onChange={(e) => setLocalApiKey(e.target.value)} className="salieri-input" placeholder="仅在本机安全存储" autoComplete="off" />
              </div>
              <div className="setting-field personality-field">
                <label>个性</label>
                <div className="slider-row">
                  <div className="salieri-range-shell" style={{ '--val': `${(localTemperature / 2) * 100}%` } as React.CSSProperties}>
                    <span className="salieri-range-fill" aria-hidden="true" />
                    <input type="range" min="0" max="2" step="0.1" value={localTemperature}
                      onChange={(e) => setLocalTemperature(parseFloat(e.target.value))} className="salieri-range"
                      aria-label="个性" />
                  </div>
                  <span className="slider-value">{localTemperature.toFixed(1)}</span>
                </div>
              </div>
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
          <div className="modal-footer">
            <button className="salieri-btn cancel-btn" onClick={onClose}>取消</button>
            <button className="salieri-btn save-btn" onClick={handleSave}>保存修改</button>
          </div>
        </div>
      </div>
    </div>
  );
};
export default SettingsModal;
