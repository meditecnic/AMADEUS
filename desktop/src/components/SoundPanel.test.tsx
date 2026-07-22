import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SoundPanel } from './SoundPanel';

afterEach(cleanup);

describe('SoundPanel', () => {
  it('exposes an independent VOICE volume control without TTS engine settings', () => {
    const onVoiceVolumeChange = vi.fn();
    render(
      <SoundPanel
        isOpen
        onClose={vi.fn()}
        enableBgm
        bgmVolume={0.2}
        enableSfx
        sfxVolume={0.8}
        voiceVolume={0.65}
        onEnableBgmChange={vi.fn()}
        onBgmVolumeChange={vi.fn()}
        onEnableSfxChange={vi.fn()}
        onSfxVolumeChange={vi.fn()}
        onVoiceVolumeChange={onVoiceVolumeChange}
      />,
    );

    const sliders = document.querySelectorAll('.sound-panel-dropdown input[type="range"]');
    expect(sliders).toHaveLength(3);
    expect(document.body.textContent).toContain('VOICE');
    expect(document.body.textContent).not.toContain('TTS');

    fireEvent.change(sliders[2], { target: { value: '40' } });
    expect(onVoiceVolumeChange).toHaveBeenCalledWith(0.4);
  });
});
