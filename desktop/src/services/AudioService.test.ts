import { beforeEach, describe, expect, it, vi } from 'vitest';


const howlerState = vi.hoisted(() => ({
  configs: [] as Array<Record<string, unknown>>,
  instances: [] as Array<{
    play: ReturnType<typeof vi.fn>;
    once: ReturnType<typeof vi.fn>;
    fade: ReturnType<typeof vi.fn>;
    stop: ReturnType<typeof vi.fn>;
    unload: ReturnType<typeof vi.fn>;
  }>,
}));

vi.mock('howler', () => ({
  Howl: class MockHowl {
    play = vi.fn(() => 1);
    once = vi.fn();
    stop = vi.fn();
    unload = vi.fn();
    fade = vi.fn();

    constructor(config: Record<string, unknown>) {
      howlerState.configs.push(config);
      howlerState.instances.push(this);
    }
    volume() { return 0; }
  },
  Howler: { mute: vi.fn() },
}));

const pack = vi.hoisted(() => ({
  installed: true,
  revision: 0,
  listeners: new Set<() => void>(),
  emit() {
    for (const cb of Array.from(this.listeners)) cb();
  },
}));
vi.mock('../assetPack', () => ({
  packAsset: (path: string) => {
    if (!pack.installed) return null;
    const base = `/api/asset-pack/${path.replace(/^\/+/, '')}`;
    return pack.revision > 0 ? `${base}?v=${pack.revision}` : base;
  },
  subscribeAssetPack: (cb: () => void) => {
    pack.listeners.add(cb);
    return () => pack.listeners.delete(cb);
  },
}));

import { audioService } from './AudioService';


describe('AudioService BGM loading', () => {
  beforeEach(() => {
    localStorage.clear();
    howlerState.configs.length = 0;
    howlerState.instances.length = 0;
    pack.installed = true;
    pack.revision = 0;
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

  it('stops active worldline BGM immediately when the asset pack becomes absent', () => {
    audioService.playBGMForWorldline('steins_gate', 0.2);
    expect(howlerState.instances).toHaveLength(1);
    const active = howlerState.instances[0];
    const stopSpy = vi.spyOn(audioService, 'stopBGM');

    pack.installed = false;
    audioService.playBGMForWorldline('steins_gate', 0.2);

    expect(stopSpy).toHaveBeenCalledTimes(1);
    expect(active.fade).toHaveBeenCalled();
    stopSpy.mockRestore();
  });

  it('stops and refreshes BGM live on assetPack subscription notifications', () => {
    audioService.playBGMForWorldline('beta', 0.2);
    expect(howlerState.configs).toHaveLength(1);
    const first = howlerState.instances[0];

    pack.installed = false;
    pack.revision = 1;
    pack.emit();
    expect(first.fade).toHaveBeenCalled();

    pack.installed = true;
    pack.revision = 2;
    audioService.playBGMForWorldline('beta', 0.2);
    expect(howlerState.configs).toHaveLength(2);
    expect((howlerState.configs[1].src as string[])[0]).toContain('?v=2');
  });
});
