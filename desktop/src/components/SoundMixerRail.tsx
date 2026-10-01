import React, { useEffect, useRef } from 'react';

export interface SoundMixerRailProps {
  isOpen: boolean;
  onClose: () => void;
  enableBgm: boolean;
  bgmVolume: number;
  enableSfx: boolean;
  sfxVolume: number;
  voiceVolume: number;
  onEnableBgmChange: (v: boolean) => void;
  onBgmVolumeChange: (v: number) => void;
  onEnableSfxChange: (v: boolean) => void;
  onSfxVolumeChange: (v: number) => void;
  onVoiceVolumeChange: (v: number) => void;
  /** Her voice mute (keeps the level; does not change TTS/autoplay). Omitted = no voice LED. */
  voiceEnabled?: boolean;
  onVoiceEnabledChange?: (v: boolean) => void;
  /** id for aria-controls on the Sound trigger */
  panelId?: string;
  /** Sound trigger — Escape restores focus here; outside-click excludes this node */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

/**
 * Header-inline Audio Mixer Rail (non-modal).
 * Not a floating dropdown / portal / backdrop popup.
 */
export const SoundMixerRail: React.FC<SoundMixerRailProps> = ({
  isOpen,
  onClose,
  enableBgm,
  bgmVolume,
  enableSfx,
  sfxVolume,
  voiceVolume,
  onEnableBgmChange,
  onBgmVolumeChange,
  onEnableSfxChange,
  onSfxVolumeChange,
  onVoiceVolumeChange,
  voiceEnabled = true,
  onVoiceEnabledChange,
  panelId = 'sound-mixer-rail',
  triggerRef,
}) => {
  const railRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing) return;
      event.preventDefault();
      // Innermost layer: one Escape closes only the mixer, not the system menu behind it.
      event.stopPropagation();
      // Focus trigger first (while still mounted), then close rail.
      triggerRef?.current?.focus();
      onClose();
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [isOpen, onClose, triggerRef]);

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (railRef.current?.contains(target)) return;
      if (triggerRef?.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  const pct = (v: number) => `${Math.round(v * 100)}%`;

  return (
    <div
      ref={railRef}
      id={panelId}
      className="sound-mixer-rail"
      data-sound-mixer-rail="true"
      role="region"
      aria-label="Audio mixer"
    >
      <span className="sound-mixer-rail-tag" aria-hidden="true">
        MIX
      </span>

      <div className="sound-mixer-channels">
        <div className="sound-mixer-channel" data-channel="bgm">
          <label className="sound-mixer-led-label">
            <input
              type="checkbox"
              className="sound-mixer-led"
              checked={enableBgm}
              onChange={(e) => onEnableBgmChange(e.target.checked)}
              aria-label="BGM"
            />
            <span className="sound-mixer-led-face" aria-hidden="true" />
            <span className="sound-mixer-channel-name">BGM</span>
          </label>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(bgmVolume * 100)}
            onChange={(e) => onBgmVolumeChange(Number(e.target.value) / 100)}
            className="volume-slider sound-mixer-range"
            style={{ '--val': pct(bgmVolume) } as React.CSSProperties}
            disabled={!enableBgm}
            aria-label="BGM volume"
          />
          <span className="sound-mixer-pct" aria-hidden="true">
            {pct(bgmVolume)}
          </span>
        </div>

        <div className="sound-mixer-channel" data-channel="sfx">
          <label className="sound-mixer-led-label">
            <input
              type="checkbox"
              className="sound-mixer-led"
              checked={enableSfx}
              onChange={(e) => onEnableSfxChange(e.target.checked)}
              aria-label="SFX"
            />
            <span className="sound-mixer-led-face" aria-hidden="true" />
            <span className="sound-mixer-channel-name">SFX</span>
          </label>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(sfxVolume * 100)}
            onChange={(e) => onSfxVolumeChange(Number(e.target.value) / 100)}
            className="volume-slider sound-mixer-range"
            style={{ '--val': pct(sfxVolume) } as React.CSSProperties}
            disabled={!enableSfx}
            aria-label="SFX volume"
          />
          <span className="sound-mixer-pct" aria-hidden="true">
            {pct(sfxVolume)}
          </span>
        </div>

        <div className="sound-mixer-channel" data-channel="voice">
          {onVoiceEnabledChange ? (
            <label className="sound-mixer-led-label">
              <input
                type="checkbox"
                className="sound-mixer-led"
                checked={voiceEnabled}
                onChange={(e) => onVoiceEnabledChange(e.target.checked)}
                aria-label="VOICE"
              />
              <span className="sound-mixer-led-face" aria-hidden="true" />
              <span className="sound-mixer-channel-name">VOICE</span>
            </label>
          ) : (
            <span className="sound-mixer-channel-name sound-mixer-channel-name--voice">
              <span className="sound-mixer-voice-full">VOICE</span>
              <span className="sound-mixer-voice-compact" aria-hidden="true">
                VOX
              </span>
            </span>
          )}
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(voiceVolume * 100)}
            onChange={(e) => onVoiceVolumeChange(Number(e.target.value) / 100)}
            className="volume-slider sound-mixer-range"
            style={{ '--val': pct(voiceVolume) } as React.CSSProperties}
            disabled={!voiceEnabled}
            aria-label="语音 (VOICE)"
          />
          <span className="sound-mixer-pct" aria-hidden="true">
            {pct(voiceVolume)}
          </span>
        </div>
      </div>
    </div>
  );
};

export default SoundMixerRail;
