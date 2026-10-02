import { beforeEach, describe, expect, it, vi } from 'vitest';


const howlerState = vi.hoisted(() => ({
  configs: [] as Array<Record<string, unknown>>,
  instances: [] as Array<{
    play: ReturnType<typeof vi.fn>;
    once: ReturnType<typeof vi.fn>;
  }>,
}));

vi.mock('howler', () => ({
  Howl: class MockHowl {
    play = vi.fn(() => 1);
    once = vi.fn();

    constructor(config: Record<string, unknown>) {
      howlerState.configs.push(config);
      howlerState.instances.push(this);
    }
    stop() {}
    unload() {}
    fade() {}
    volume() { return 0; }
  },
  Howler: { mute: vi.fn() },
}));

const pack = vi.hoisted(() => ({ installed: true }));
vi.mock('../assetPack', () => ({
  packAsset: (path: string) => (pack.installed ? `/api/asset-pack/${path.replace(/^\/+/, '')}` : null),
}));

import { audioService } from './AudioService';


describe('AudioService BGM loading', () => {
  beforeEach(() => {
    localStorage.clear();
    howlerState.configs.length = 0;
    howlerState.instances.length = 0;
    pack.installed = true;
    audioService.stopBGM();
    audioService.setEnableBGM(true);
  });

  it('streams long BGM through HTML audio instead of decoding the whole OGG', () => {
    audioService.playBGM('/audio/bgm/test.ogg', 0.2);

    expect(howlerState.configs).toHaveLength(1);
    expect(howlerState.configs[0]).toMatchObject({
      src: ['/api/asset-pack/audio/bgm/test.ogg'],
      html5: true,
    });
  });

  it('stays silent without the original asset pack', () => {
    pack.installed = false;
    audioService.playBGM('/audio/bgm/test.ogg', 0.2);

    expect(howlerState.configs).toHaveLength(0);
  });

  it('retries the active BGM after the browser unlocks audio playback', () => {
    const warningSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    audioService.playBGM('/audio/bgm/test.ogg', 0.2);
    const config = howlerState.configs[0] as {
      onplayerror: (id: number, error: unknown) => void;
    };
    const track = howlerState.instances[0];

    config.onplayerror(1, 'autoplay blocked');

    expect(track.once).toHaveBeenCalledWith('unlock', expect.any(Function));
    const unlock = track.once.mock.calls[0][1] as () => void;
    unlock();
    expect(track.play).toHaveBeenCalledTimes(2);
    warningSpy.mockRestore();
  });
});
