import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRef } from 'react';

import { SoundMixerRail } from './SoundMixerRail';

afterEach(cleanup);

const baseProps = {
  enableBgm: true,
  bgmVolume: 0.15,
  enableSfx: true,
  sfxVolume: 1,
  voiceVolume: 1,
  onEnableBgmChange: vi.fn(),
  onBgmVolumeChange: vi.fn(),
  onEnableSfxChange: vi.fn(),
  onSfxVolumeChange: vi.fn(),
  onVoiceVolumeChange: vi.fn(),
};

describe('SoundMixerRail', () => {
  it('does not mount portal/backdrop/dropdown when closed or open', () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <div className="workstation-header">
        <div className="header-audio-zone" data-header-audio-zone="true">
          <SoundMixerRail isOpen={false} onClose={onClose} {...baseProps} />
        </div>
      </div>,
    );
    expect(document.querySelector('.sound-panel-backdrop')).toBeNull();
    expect(document.querySelector('.sound-panel-dropdown')).toBeNull();
    expect(document.body.querySelector('[data-sound-mixer-rail]')).toBeNull();

    rerender(
      <div className="workstation-header">
        <div className="header-audio-zone" data-header-audio-zone="true">
          <SoundMixerRail isOpen onClose={onClose} panelId="sound-mixer-rail" {...baseProps} />
        </div>
      </div>,
    );
    expect(document.querySelector('.sound-panel-backdrop')).toBeNull();
    expect(document.querySelector('.sound-panel-dropdown')).toBeNull();
    // Must not portal to body — rail lives under header audio zone.
    const rail = document.querySelector('[data-sound-mixer-rail="true"]');
    expect(rail).toBeTruthy();
    expect(rail?.closest('[data-header-audio-zone]')).toBeTruthy();
    expect(rail?.parentElement).not.toBe(document.body);
    expect(document.body.querySelector(':scope > .sound-panel-backdrop')).toBeNull();
  });

  it('exposes region semantics without modal dialog / focus trap', () => {
    render(
      <div className="header-audio-zone" data-header-audio-zone="true">
        <SoundMixerRail isOpen onClose={vi.fn()} panelId="sound-mixer-rail" {...baseProps} />
      </div>,
    );
    const rail = screen.getByRole('region', { name: /audio mixer/i });
    expect(rail).toHaveAttribute('id', 'sound-mixer-rail');
    expect(rail).not.toHaveAttribute('aria-modal');
    expect(rail.getAttribute('role')).toBe('region');
    expect(document.querySelector('[aria-modal="true"]')).toBeNull();
  });

  it('keeps BGM / SFX / VOICE controls and independent VOICE volume without TTS engine settings', () => {
    const onVoiceVolumeChange = vi.fn();
    render(
      <SoundMixerRail
        isOpen
        onClose={vi.fn()}
        {...baseProps}
        onVoiceVolumeChange={onVoiceVolumeChange}
      />,
    );

    const ranges = screen.getAllByRole('slider');
    expect(ranges).toHaveLength(3);
    expect(screen.getByRole('checkbox', { name: /BGM/i })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: /SFX/i })).toBeTruthy();
    // VOICE has accessible name; may display as VOX compact label.
    expect(screen.getByRole('slider', { name: /VOICE|语音/i })).toBeTruthy();
    expect(document.body.textContent).not.toContain('語り声');
    expect(document.body.textContent).not.toMatch(/\bTTS\b/);

    fireEvent.change(ranges[2], { target: { value: '40' } });
    expect(onVoiceVolumeChange).toHaveBeenCalledWith(0.4);
  });

  it('DOM order supports natural Tab flow: BGM toggle → BGM range → SFX toggle → SFX range → VOICE range', () => {
    render(<SoundMixerRail isOpen onClose={vi.fn()} {...baseProps} />);
    const focusables = Array.from(
      document.querySelectorAll(
        '.sound-mixer-rail input[type="checkbox"], .sound-mixer-rail input[type="range"]',
      ),
    ) as HTMLElement[];
    expect(focusables).toHaveLength(5);
    expect((focusables[0] as HTMLInputElement).type).toBe('checkbox');
    expect((focusables[0] as HTMLInputElement).getAttribute('aria-label') || focusables[0].closest('label')?.textContent || '').toMatch(/BGM/i);
    expect((focusables[1] as HTMLInputElement).type).toBe('range');
    expect((focusables[2] as HTMLInputElement).type).toBe('checkbox');
    expect((focusables[3] as HTMLInputElement).type).toBe('range');
    expect((focusables[4] as HTMLInputElement).type).toBe('range');
  });

  it('Escape closes and restores focus to the Sound trigger', () => {
    const onClose = vi.fn();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <div>
        <button type="button" ref={triggerRef}>
          Sound
        </button>
        <SoundMixerRail
          isOpen
          onClose={onClose}
          triggerRef={triggerRef}
          panelId="sound-mixer-rail"
          {...baseProps}
        />
      </div>,
    );
    triggerRef.current?.focus();
    expect(document.activeElement).toBe(triggerRef.current);
    // Move focus into rail, then Escape.
    const bgm = screen.getByRole('checkbox', { name: /BGM/i });
    bgm.focus();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(triggerRef.current);
  });

  it('outside mousedown closes without changing volumes', () => {
    const onClose = vi.fn();
    const onBgmVolumeChange = vi.fn();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <div>
        <button type="button" ref={triggerRef} data-testid="sound-trigger">
          Sound
        </button>
        <button type="button" data-testid="outside">
          outside
        </button>
        <SoundMixerRail
          isOpen
          onClose={onClose}
          triggerRef={triggerRef}
          {...baseProps}
          onBgmVolumeChange={onBgmVolumeChange}
        />
      </div>,
    );
    fireEvent.mouseDown(screen.getByTestId('outside'));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onBgmVolumeChange).not.toHaveBeenCalled();
  });

  it('mousedown inside rail or on trigger does not close', () => {
    const onClose = vi.fn();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <div>
        <button type="button" ref={triggerRef} data-testid="sound-trigger">
          Sound
        </button>
        <SoundMixerRail isOpen onClose={onClose} triggerRef={triggerRef} {...baseProps} />
      </div>,
    );
    fireEvent.mouseDown(screen.getByRole('region', { name: /audio mixer/i }));
    fireEvent.mouseDown(screen.getByTestId('sound-trigger'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('renders nothing when closed (not a persistent overlay)', () => {
    render(<SoundMixerRail isOpen={false} onClose={vi.fn()} {...baseProps} />);
    expect(screen.queryByRole('region', { name: /audio mixer/i })).toBeNull();
    expect(document.querySelector('.sound-mixer-rail')).toBeNull();
  });
});
