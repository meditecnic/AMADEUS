import React, { useState, useEffect } from 'react';

export interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  worldline: 'steins_gate' | 'beta';
  apiKey: string;
  systemPrompt: string;
  enableTts: boolean;
  sovitsUrl: string;
  onSaveSettings: (settings: {
    worldline: 'steins_gate' | 'beta';
    apiKey: string;
    systemPrompt: string;
    enableTts: boolean;
    sovitsUrl: string;
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
  onSaveSettings,
}) => {
  const [localWorldline, setLocalWorldline] = useState<'steins_gate' | 'beta'>(worldline);
  const [localApiKey, setLocalApiKey] = useState(apiKey);
  const [localSystemPrompt, setLocalSystemPrompt] = useState(systemPrompt);
  const [localEnableTts, setLocalEnableTts] = useState(enableTts);
  const [localSovitsUrl, setLocalSovitsUrl] = useState(sovitsUrl);

  // Sync state with props when modal opens
  useEffect(() => {
    if (isOpen) {
      setLocalWorldline(worldline);
      setLocalApiKey(apiKey);
      setLocalSystemPrompt(systemPrompt);
      setLocalEnableTts(enableTts);
      setLocalSovitsUrl(sovitsUrl);
    }
  }, [isOpen, worldline, apiKey, systemPrompt, enableTts, sovitsUrl]);

  if (!isOpen) return null;

  const handleSave = () => {
    onSaveSettings({
      worldline: localWorldline,
      apiKey: localApiKey,
      systemPrompt: localSystemPrompt,
      enableTts: localEnableTts,
      sovitsUrl: localSovitsUrl,
    });
    onClose();
  };

  return (
    <div className="salieri-modal-overlay">
      <div className="salieri-settings-modal">
        <div className="modal-header">
          <h2>系统设置</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="setting-group">
            <label>当前世界线</label>
            <select
              value={localWorldline}
              onChange={(e) => setLocalWorldline(e.target.value as 'steins_gate' | 'beta')}
              className="salieri-select"
            >
              <option value="steins_gate">Steins;Gate 世界线 (1.048596%)</option>
              <option value="beta">β 世界线 (1.130205%)</option>
            </select>
          </div>

          <div className="setting-group">
            <label>DEEPSEEK API 密钥</label>
            <input
              type="password"
              value={localApiKey}
              onChange={(e) => setLocalApiKey(e.target.value)}
              className="salieri-input"
              placeholder="sk-..."
            />
          </div>

          <div className="setting-group">
            <label>GPT-SoVITS 服务端点</label>
            <input
              type="text"
              value={localSovitsUrl}
              onChange={(e) => setLocalSovitsUrl(e.target.value)}
              className="salieri-input"
              placeholder="http://127.0.0.1:9880"
            />
          </div>

          <div className="setting-group checkbox-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={localEnableTts}
                onChange={(e) => setLocalEnableTts(e.target.checked)}
                className="salieri-checkbox"
              />
              启用语音合成 (TTS)
            </label>
          </div>

          <div className="setting-group">
            <label>系统基础提示词</label>
            <textarea
              value={localSystemPrompt}
              onChange={(e) => setLocalSystemPrompt(e.target.value)}
              className="salieri-textarea"
              rows={8}
            />
          </div>
        </div>

        <div className="modal-footer">
          <button className="salieri-btn cancel-btn" onClick={onClose}>
            取消
          </button>
          <button className="salieri-btn save-btn" onClick={handleSave}>
            保存修改
          </button>
        </div>
      </div>
    </div>
  );
};
export default SettingsModal;
