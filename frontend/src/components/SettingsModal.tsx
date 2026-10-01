import React, { useState, useEffect } from 'react';
import { X, RotateCcw } from 'lucide-react';
import { DEFAULT_SYSTEM_PROMPT } from '../constants/defaultPrompt';

export interface Settings {
  deepseekKey: string;
  sovitsUrl: string;
  systemPrompt: string;
  rememberMe: boolean;
  enableTTS: boolean;
  enableBGM: boolean;
  bgmVolume: number;
  sfxVolume: number;
  temperature: number;
}

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  savedSettings: Settings;
  onSave: (settings: Settings, isKeyChanged: boolean) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  savedSettings,
  onSave
}) => {
  const [deepseekKey, setDeepseekKey] = useState('');
  const [sovitsUrl, setSovitsUrl] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [enableTTS, setEnableTTS] = useState(true);
  const [enableBGM, setEnableBGM] = useState(true);
  const [bgmVolume, setBgmVolume] = useState(0.15);
  const [sfxVolume, setSfxVolume] = useState(1.0);
  const [temperature, setTemperature] = useState(1.0);
  const [isKeyDirty, setIsKeyDirty] = useState(false);

  const MASK = 'sk-*********';

  // 每次弹窗打开或传入的设置变化时，同步临时状态
  useEffect(() => {
    if (isOpen) {
      setDeepseekKey(savedSettings.deepseekKey ? MASK : '');
      setSovitsUrl(savedSettings.sovitsUrl || '');
      setSystemPrompt(savedSettings.systemPrompt || DEFAULT_SYSTEM_PROMPT);
      setRememberMe(savedSettings.rememberMe);
      setEnableTTS(savedSettings.enableTTS ?? true);
      setEnableBGM(savedSettings.enableBGM ?? true);
      setBgmVolume(savedSettings.bgmVolume ?? 0.15);
      setSfxVolume(savedSettings.sfxVolume ?? 1.0);
      setTemperature(savedSettings.temperature ?? 1.0);
      setIsKeyDirty(false);
    }
  }, [isOpen, savedSettings]);

  if (!isOpen) return null;

  const handleFocus = () => {
    if (!isKeyDirty && savedSettings.deepseekKey) {
      setDeepseekKey(savedSettings.deepseekKey);
    }
  };

  const handleBlur = () => {
    if (deepseekKey === savedSettings.deepseekKey) {
      setDeepseekKey(savedSettings.deepseekKey ? MASK : '');
      setIsKeyDirty(false);
    } else if (deepseekKey === MASK) {
      setIsKeyDirty(false);
    } else {
      setIsKeyDirty(true);
    }
  };

  const handleKeyChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setDeepseekKey(val);
    if (val === MASK) {
      setIsKeyDirty(false);
    } else if (val === savedSettings.deepseekKey) {
      setIsKeyDirty(false);
    } else {
      setIsKeyDirty(true);
    }
  };

  const handleResetPrompt = () => {
    setSystemPrompt(DEFAULT_SYSTEM_PROMPT);
  };

  const handleSave = () => {
    const updatedKey = isKeyDirty ? deepseekKey : savedSettings.deepseekKey;
    onSave(
      {
        deepseekKey: updatedKey,
        sovitsUrl: sovitsUrl.trim(),
        systemPrompt: systemPrompt.trim(),
        rememberMe,
        enableTTS,
        enableBGM,
        bgmVolume,
        sfxVolume,
        temperature
      },
      isKeyDirty
    );
    onClose();
  };

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-70 backdrop-blur-sm"
      style={{ fontFamily: 'inherit' }}
    >
      <div 
        data-testid="settings-modal"
        className="w-full max-w-md mx-4 overflow-hidden rounded-md border border-[#3c1e10] bg-[#0c0604] shadow-2xl"
        style={{
          boxShadow: '0 0 30px rgba(255, 108, 0, 0.15)',
        }}
      >
        {/* Header */}
        <div className="flex justify-between items-center border-b border-[#3c1e10] bg-[#140804] px-4 py-3">
          <span className="font-bold text-xs tracking-widest text-[#ff6c00] uppercase">
            系统参数设置
          </span>
          <button 
            onClick={onClose}
            className="text-gray-500 hover:text-[#ff6c00] transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Form Body */}
        <div className="p-4 flex flex-col gap-4 text-xs">
          {/* DeepSeek Key */}
          <div className="flex flex-col gap-1.5">
            <label className="font-bold text-[#ff6c00]/70 tracking-wider">
              DEEPSEEK API KEY
            </label>
            <input
              data-testid="api-key-input"
              type="password"
              value={deepseekKey}
              onChange={handleKeyChange}
              onFocus={handleFocus}
              onBlur={handleBlur}
              placeholder="请输入您的 DeepSeek API Key"
              className="bg-[#1b0d07] border border-[#ff6c00]/25 rounded px-3 py-2 text-sm text-[#ff6c00] outline-none transition-all focus:border-[#ff6c00]"
            />
          </div>

          {/* SoVITS URL */}
          <div className="flex flex-col gap-1.5">
            <label className="font-bold text-[#ff6c00]/70 tracking-wider">
              GPT-SOVITS URL
            </label>
            <input
              type="text"
              value={sovitsUrl}
              onChange={(e) => setSovitsUrl(e.target.value)}
              placeholder="例如：http://127.0.0.1:9880"
              disabled={!enableTTS}
              className={`bg-[#1b0d07] border ${enableTTS ? 'border-[#ff6c00]/25' : 'border-[#3c1e10]'} rounded px-3 py-2 text-sm ${enableTTS ? 'text-[#ff6c00]' : 'text-[#3c1e10]'} outline-none transition-all focus:border-[#ff6c00]`}
            />
            <div className="flex items-center gap-2 mt-1">
              <input
                id="enable_tts"
                type="checkbox"
                checked={enableTTS}
                onChange={(e) => setEnableTTS(e.target.checked)}
                className="w-3.5 h-3.5 accent-[#ff6c00] bg-[#1b0d07] border border-[#ff6c00]/25 rounded cursor-pointer"
              />
              <label 
                htmlFor="enable_tts"
                className="font-bold text-[#ff6c00]/60 tracking-wider cursor-pointer hover:text-[#ff6c00] select-none"
              >
                启用语音模型 (GPT-SoVITS)
              </label>
            </div>
          </div>

          {/* BGM 设置区 */}
          <div className="flex flex-col gap-2 pb-2 border-b border-[#3c1e10]">
            <div className="flex items-center justify-between">
              <label className="font-bold text-[#ff6c00]/70 tracking-wider">
                环境音效 (BGM)
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="enable_bgm"
                  type="checkbox"
                  checked={enableBGM}
                  onChange={(e) => setEnableBGM(e.target.checked)}
                  className="w-3.5 h-3.5 accent-[#ff6c00] bg-[#1b0d07] border border-[#ff6c00]/25 rounded cursor-pointer"
                />
                <label 
                  htmlFor="enable_bgm"
                  className="font-bold text-[#ff6c00]/60 tracking-wider cursor-pointer hover:text-[#ff6c00] select-none"
                >
                  开启音乐
                </label>
              </div>
            </div>
            
            <div className="flex items-center gap-3">
              <span className={`text-[10px] font-bold ${enableBGM ? 'text-[#ff6c00]/50' : 'text-[#3c1e10]'}`}>VOL</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={bgmVolume}
                onChange={(e) => setBgmVolume(parseFloat(e.target.value))}
                disabled={!enableBGM}
                className="flex-1 h-1 bg-[#3c1e10] rounded appearance-none cursor-pointer accent-[#ff6c00]"
                style={{ opacity: enableBGM ? 1 : 0.3 }}
              />
              <span className={`text-[10px] font-bold w-6 text-right ${enableBGM ? 'text-[#ff6c00]/70' : 'text-[#3c1e10]'}`}>
                {Math.round(bgmVolume * 100)}%
              </span>
            </div>
          </div>

          {/* SFX 设置区 */}
          <div className="flex flex-col gap-2 pb-2 border-b border-[#3c1e10]">
            <div className="flex items-center justify-between">
              <label className="font-bold text-[#ff6c00]/70 tracking-wider">
                界面音效 (SFX)
              </label>
            </div>
            
            <div className="flex items-center gap-3">
              <span className="text-[10px] font-bold text-[#ff6c00]/50">VOL</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={sfxVolume}
                onChange={(e) => setSfxVolume(parseFloat(e.target.value))}
                className="flex-1 h-1 bg-[#3c1e10] rounded appearance-none cursor-pointer accent-[#ff6c00]"
              />
              <span className="text-[10px] font-bold w-6 text-right text-[#ff6c00]/70">
                {Math.round(sfxVolume * 100)}%
              </span>
            </div>
          </div>

          {/* 温度 (Temperature) 设置区 */}
          <div className="flex flex-col gap-2 pb-2 border-b border-[#3c1e10]">
            <label className="font-bold text-[#ff6c00]/70 tracking-wider">
              温度 (TEMPERATURE)
            </label>
            <div className="flex items-center gap-3">
              <span className="text-[10px] font-bold text-[#ff6c00]/50">TMP</span>
              <input
                data-testid="temperature-slider"
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                className="flex-1 h-1 bg-[#3c1e10] rounded appearance-none cursor-pointer accent-[#ff6c00]"
              />
              <span className="text-[10px] font-bold w-6 text-right text-[#ff6c00]/70">
                {temperature.toFixed(1)}
              </span>
            </div>
          </div>

          {/* System Prompt */}
          <div className="flex flex-col gap-1.5">
            <div className="flex justify-between items-center">
              <label className="font-bold text-[#ff6c00]/70 tracking-wider">
                SYSTEM PROMPT
              </label>
              <button
                type="button"
                onClick={handleResetPrompt}
                className="flex items-center gap-1 text-[10px] text-[#ff6c00]/50 hover:text-[#ff6c00] transition-colors"
              >
                <RotateCcw size={10} />
                <span>恢复默认</span>
              </button>
            </div>
            <textarea
              rows={4}
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="在这里配置大模型的人设或规则..."
              className="bg-[#1b0d07] border border-[#ff6c00]/25 rounded px-3 py-2 text-sm text-[#ff6c00] outline-none transition-all focus:border-[#ff6c00] resize-none leading-relaxed"
            />
          </div>

          {/* Remember Me Checkbox */}
          <div className="flex items-center gap-2 mt-1">
            <input
              data-testid="remember-me-checkbox"
              id="remember_me"
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
              className="w-3.5 h-3.5 accent-[#ff6c00] bg-[#1b0d07] border border-[#ff6c00]/25 rounded cursor-pointer"
            />
            <label 
              htmlFor="remember_me"
              className="font-bold text-[#ff6c00]/60 tracking-wider cursor-pointer hover:text-[#ff6c00] select-none"
            >
              记住配置 (保存在本地浏览器中)
            </label>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="flex justify-end gap-2 border-t border-[#3c1e10] bg-[#140804] px-4 py-3">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded border border-transparent text-gray-500 hover:text-[#ff6c00] transition-all text-xs font-bold"
          >
            取消
          </button>
          <button
            data-testid="settings-save-btn"
            onClick={handleSave}
            className="px-4 py-1.5 rounded bg-[#ff6c00]/10 border border-[#ff6c00]/40 text-[#ff6c00] hover:bg-[#ff6c00]/20 hover:border-[#ff6c00] transition-all text-xs font-bold"
          >
            保存设置
          </button>
        </div>
      </div>
    </div>
  );
};
