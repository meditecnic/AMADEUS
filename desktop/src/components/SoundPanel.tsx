import React, { useEffect, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';

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
  const [position, setPosition] = useState<{ top: number; left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    if (!isOpen) return;
    const placePanel = () => {
      const trigger = document.querySelector<HTMLElement>('.sound-control-anchor .ctrl-btn');
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      const width = Math.min(300, window.innerWidth - 32);
      setPosition({
        top: rect.bottom + 10,
        left: Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16)),
        width,
      });
    };
    placePanel();
    window.addEventListener('resize', placePanel);
    window.addEventListener('scroll', placePanel, true);
    return () => {
      window.removeEventListener('resize', placePanel);
      window.removeEventListener('scroll', placePanel, true);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return createPortal(
    <>
      <button className="sound-panel-backdrop" type="button" aria-label="关闭音量控制" onClick={onClose} />
      <div
        className="sound-panel-dropdown"
        style={position ? { position: 'fixed', top: position.top, left: position.left, width: position.width } : undefined}
      >
        <div className="sound-panel-header">
          <span className="sound-panel-title">SOUND CONTROL</span>
          <button className="sound-panel-close" type="button" aria-label="关闭音量控制" onClick={onClose}>×</button>
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
    </>
    , document.body
  );
};

export default SoundPanel;
