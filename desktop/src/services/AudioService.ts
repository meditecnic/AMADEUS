import { Howl, Howler } from 'howler';

class AudioService {
  private currentBGM: Howl | null = null;
  private bgmVolume: number = 0.15;
  private sfxVolume: number = 1.0;
  private isMuted: boolean = false;
  private enableBGM: boolean = true;
  private enableSFX: boolean = true;
  private fadeDuration: number = 2000;

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

  public playBGM(src: string, volume?: number) {
    if (!this.enableBGM) return;
    if (volume !== undefined) this.bgmVolume = volume;
    const targetVolume = this.isMuted ? 0 : this.bgmVolume;

    if (this.currentBGM && (this.currentBGM as any)._src === src) {
      return;
    }

    const oldBGM = this.currentBGM;

    const newBGM = new Howl({
      src: [src],
      loop: true,
      volume: 0,
      onloaderror: (_id, err) => console.error('BGM load error:', err),
      onplayerror: (_id, err) => console.error('BGM play error:', err),
    });

    (newBGM as any)._src = src;
    this.currentBGM = newBGM;

    newBGM.play();
    newBGM.fade(0, targetVolume, this.fadeDuration);

    if (oldBGM) {
      const currentVol = typeof oldBGM.volume() === 'number' ? (oldBGM.volume() as number) : 1;
      oldBGM.fade(currentVol, 0, this.fadeDuration);
      oldBGM.once('fade', () => {
        oldBGM.unload();
      });
    }
  }

  public stopBGM() {
    if (this.currentBGM) {
      const oldBGM = this.currentBGM;
      this.currentBGM = null;
      const currentVol = typeof oldBGM.volume() === 'number' ? (oldBGM.volume() as number) : 1;
      oldBGM.fade(currentVol, 0, this.fadeDuration);
      oldBGM.once('fade', () => {
        oldBGM.unload();
      });
    }
  }

  public playSFX(src: string, volume?: number) {
    if (!this.enableSFX) return;
    if (this.isMuted) return;
    const vol = volume !== undefined ? volume : this.sfxVolume;

    const sfx = new Howl({
      src: [src],
      volume: vol,
      onend: () => {
        sfx.unload();
      },
    });
    sfx.play();
  }

  public setBGMVolume(volume: number) {
    this.bgmVolume = volume;
    if (this.currentBGM && !this.isMuted) {
      this.currentBGM.volume(volume);
    }
    this.persistPreferences();
  }

  public setSFXVolume(volume: number) {
    this.sfxVolume = volume;
    this.persistPreferences();
  }

  public setMuted(mute: boolean) {
    this.isMuted = mute;
    Howler.mute(mute);
  }

  public setEnableBGM(enable: boolean) {
    this.enableBGM = enable;
    if (!enable) {
      this.stopBGM();
    }
    this.persistPreferences();
  }

  public setEnableSFX(enable: boolean) {
    this.enableSFX = enable;
    this.persistPreferences();
  }
}

export const audioService = new AudioService();
