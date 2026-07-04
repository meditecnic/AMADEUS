import React from 'react';

interface SoundPanelProps {
  isOpen: boolean;
  onClose: () => void;
  enableBgm: boolean;
  bgmVolume: number;
  enableSfx: boolean;
  sfxVolume: number;
  enableTts: boolean;
  onEnableBgmChange: (v: boolean) => void;
  onBgmVolumeChange: (v: number) => void;
  onEnableSfxChange: (v: boolean) => void;
  onSfxVolumeChange: (v: number) => void;
  onEnableTtsChange: (v: boolean) => void;
}

export const SoundPanel: React.FC<SoundPanelProps> = ({
  isOpen,
  onClose,
  enableBgm,
  bgmVolume,
  enableSfx,
  sfxVolume,
  enableTts,
  onEnableBgmChange,
  onBgmVolumeChange,
  onEnableSfxChange,
  onSfxVolumeChange,
  onEnableTtsChange,
}) => {
  if (!isOpen) return null;

  return (
    <div className="sound-panel-overlay" onClick={onClose}>
      <div className="sound-panel-dropdown" onClick={(e) => e.stopPropagation()}>
        <div className="sound-panel-header">
          <span className="sound-panel-title">SOUND CONTROL</span>
          <button className="sound-panel-close" onClick={onClose}>×</button>
        </div>

        <div className="sound-panel-body">
          <div className="sound-setting-row">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={enableBgm}
                onChange={(e) => onEnableBgmChange(e.target.checked)}
                className="salieri-checkbox"
              />
              背景音乐 (BGM)
            </label>
            <input
              type="range"
              min="0"
              max="100"
              value={Math.round(bgmVolume * 100)}
              onChange={(e) => onBgmVolumeChange(Number(e.target.value) / 100)}
              className="volume-slider"
              style={{ '--val': `${bgmVolume * 100}%` } as React.CSSProperties}
              disabled={!enableBgm}
            />
            <span className="volume-label">{Math.round(bgmVolume * 100)}%</span>
          </div>

          <div className="sound-setting-row">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={enableSfx}
                onChange={(e) => onEnableSfxChange(e.target.checked)}
                className="salieri-checkbox"
              />
              音效 (SFX)
            </label>
            <input
              type="range"
              min="0"
              max="100"
              value={Math.round(sfxVolume * 100)}
              onChange={(e) => onSfxVolumeChange(Number(e.target.value) / 100)}
              className="volume-slider"
              style={{ '--val': `${sfxVolume * 100}%` } as React.CSSProperties}
              disabled={!enableSfx}
            />
            <span className="volume-label">{Math.round(sfxVolume * 100)}%</span>
          </div>

          <div className="sound-setting-row">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={enableTts}
                onChange={(e) => onEnableTtsChange(e.target.checked)}
                className="salieri-checkbox"
              />
              语音合成 (TTS)
            </label>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SoundPanel;
