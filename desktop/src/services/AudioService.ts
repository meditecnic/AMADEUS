import { Howl, Howler } from 'howler';
import { packAsset } from '../assetPack';

type Worldline = 'steins_gate' | 'beta';

const WORLDLINE_PLAYLISTS: Record<Worldline, string[]> = {
  steins_gate: [
    '/audio/bgm/Amadeus.ogg',
    '/audio/bgm/Future of spirit.ogg',
  ],
  beta: [
    '/audio/bgm/Logical theme.ogg',
    '/audio/bgm/Messenger from zero.ogg',
  ],
};

class AudioService {
  private currentBGM: Howl | null = null;
  private bgmVolume = 0.15;
  private sfxVolume = 1.0;
  private isMuted = false;
  private enableBGM = true;
  private enableSFX = true;
  private readonly fadeDuration = 1800;
  private activeWorldline: Worldline | null = null;
  private playlist: string[] = [];
  private playlistIndex = 0;
  private transitionId = 0;

  constructor() {
    this.loadPreferences();
  }

  private loadPreferences() {
    try {
      const savedEnableBGM = localStorage.getItem('amadeus_pc_enable_bgm');
      if (savedEnableBGM !== null) this.enableBGM = savedEnableBGM !== 'false';

      const savedBgmVolume = localStorage.getItem('amadeus_pc_bgm_volume');
      if (savedBgmVolume !== null) this.bgmVolume = parseFloat(savedBgmVolume);

      const savedEnableSFX = localStorage.getItem('amadeus_pc_enable_sfx');
      if (savedEnableSFX !== null) this.enableSFX = savedEnableSFX !== 'false';

      const savedSfxVolume = localStorage.getItem('amadeus_pc_sfx_volume');
      if (savedSfxVolume !== null) this.sfxVolume = parseFloat(savedSfxVolume);
    } catch (e) {
      console.warn('Failed to load audio preferences from localStorage:', e);
    }
  }

  private persistPreferences() {
    try {
      localStorage.setItem('amadeus_pc_enable_bgm', String(this.enableBGM));
      localStorage.setItem('amadeus_pc_bgm_volume', String(this.bgmVolume));
      localStorage.setItem('amadeus_pc_enable_sfx', String(this.enableSFX));
      localStorage.setItem('amadeus_pc_sfx_volume', String(this.sfxVolume));
    } catch (e) {
      console.warn('Failed to save audio preferences to localStorage:', e);
    }
  }

  private targetBgmVolume() {
    return this.isMuted ? 0 : this.bgmVolume;
  }

  private unloadAfterFade(track: Howl) {
    const currentVolume = typeof track.volume() === 'number' ? track.volume() as number : 0;
    if (currentVolume <= 0.001) {
      track.stop();
      track.unload();
      return;
    }

    track.fade(currentVolume, 0, this.fadeDuration);
    track.once('fade', () => {
      track.stop();
      track.unload();
    });
  }

  private createTrack(src: string, loop: boolean, transitionId: number) {
    let track: Howl;
    track = new Howl({
      src: [src],
      // BGM files are multi-minute OGG streams. HTMLAudio avoids decoding the
      // entire asset into a WebAudio buffer, which fails in some WebView/Chromium
      // builds and needlessly multiplies memory usage during cross-fades.
      html5: true,
      loop,
      volume: 0,
      onloaderror: (_id, err) => console.error('BGM load error:', src, err),
      onplayerror: (_id, err) => {
        console.warn('BGM playback waiting for browser audio unlock:', src, err);
        track.once('unlock', () => {
          if (
            transitionId === this.transitionId &&
            this.currentBGM === track &&
            this.enableBGM
          ) {
            track.play();
            track.fade(0, this.targetBgmVolume(), this.fadeDuration);
          }
        });
      },
      onend: () => {
        if (!loop && transitionId === this.transitionId && this.currentBGM === track) {
          this.playNextTrack();
        }
      },
    });

    // Howler exposes the source internally, but keeping our own marker makes
    // same-track checks independent of a particular Howler version.
    (track as any)._amadeusSrc = src;
    return track;
  }

  private transitionTo(src: string, loop: boolean) {
    const transitionId = ++this.transitionId;
    const oldBGM = this.currentBGM;
    const newBGM = this.createTrack(src, loop, transitionId);
    this.currentBGM = newBGM;

    newBGM.play();
    newBGM.fade(0, this.targetBgmVolume(), this.fadeDuration);

    if (oldBGM) this.unloadAfterFade(oldBGM);
  }

  private pickRandomTrack() {
    return this.playlist.length > 1
      ? Math.floor(Math.random() * this.playlist.length)
      : 0;
  }

  private pickNextTrack() {
    if (this.playlist.length < 2) return 0;
    // Do not replay the same track back-to-back, while preserving a random
    // choice for every transition instead of hard-coding an alternating order.
    const candidates = this.playlist
      .map((_src, index) => index)
      .filter((index) => index !== this.playlistIndex);
    return candidates[Math.floor(Math.random() * candidates.length)];
  }

  private playNextTrack() {
    if (!this.activeWorldline || this.playlist.length === 0 || !this.enableBGM) return;
    // When a track ends, avoid replaying the same song immediately while
    // still keeping the order non-deterministic over the playlist.
    this.playlistIndex = this.pickNextTrack();
    this.transitionTo(this.playlist[this.playlistIndex], false);
  }

  /** Play an explicitly supplied source in a looping channel. */
  public playBGM(path: string, volume?: number) {
    if (!this.enableBGM) return;
    // All music is original soundtrack from the optional pack; without it the channel stays silent.
    const src = packAsset(path);
    if (!src) return;
    if (volume !== undefined) this.bgmVolume = Math.max(0, Math.min(1, volume));

    if (this.currentBGM && (this.currentBGM as any)._amadeusSrc === src) return;

    this.activeWorldline = null;
    this.playlist = [];
    this.transitionTo(src, true);
  }

  public stopBGM() {
    this.transitionId += 1;
    const oldBGM = this.currentBGM;
    this.currentBGM = null;
    this.activeWorldline = null;
    this.playlist = [];

    if (oldBGM) this.unloadAfterFade(oldBGM);
  }

  public playBGMForWorldline(worldline: Worldline, volume?: number) {
    if (!this.enableBGM) return;
    if (volume !== undefined) this.bgmVolume = Math.max(0, Math.min(1, volume));

    const nextPlaylist = WORLDLINE_PLAYLISTS[worldline].map(packAsset).filter((src): src is string => Boolean(src));
    if (nextPlaylist.length === 0) return;
    const sameWorldline = this.activeWorldline === worldline && this.playlist.length > 0;
    if (sameWorldline && this.currentBGM) return;

    this.activeWorldline = worldline;
    this.playlist = [...nextPlaylist];
    // A worldline switch starts a fresh random draw. Using pickNextTrack here
    // would always select the opposite index because each playlist has two
    // tracks, producing the fixed 0 -> 1 -> 0 pattern users observe.
    this.playlistIndex = this.pickRandomTrack();
    this.transitionTo(this.playlist[this.playlistIndex], false);
  }

  public playSFX(path: string, volume?: number) {
    if (!this.enableSFX || this.isMuted) return;
    const src = packAsset(path);
    if (!src) return;
    const vol = volume !== undefined ? volume : this.sfxVolume;

    const sfx = new Howl({
      src: [src],
      volume: vol,
      onend: () => sfx.unload(),
    });
    sfx.play();
  }

  public setBGMVolume(volume: number) {
    this.bgmVolume = Math.max(0, Math.min(1, volume));
    if (this.currentBGM && !this.isMuted) this.currentBGM.volume(this.bgmVolume);
    this.persistPreferences();
  }

  public setSFXVolume(volume: number) {
    this.sfxVolume = Math.max(0, Math.min(1, volume));
    this.persistPreferences();
  }

  public setMuted(mute: boolean) {
    this.isMuted = mute;
    Howler.mute(mute);
  }

  public setEnableBGM(enable: boolean) {
    this.enableBGM = enable;
    if (!enable) this.stopBGM();
    this.persistPreferences();
  }

  public setEnableSFX(enable: boolean) {
    this.enableSFX = enable;
    this.persistPreferences();
  }
}

export const audioService = new AudioService();
