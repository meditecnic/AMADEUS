import { Howl, Howler } from 'howler';

class AudioService {
  private static instance: AudioService;
  private currentBGM: Howl | null = null;
  private bgmVolume: number = 0.15;
  private sfxVolume: number = 1.0;
  private isMuted: boolean = false;
  private fadeDuration: number = 2000;

  private constructor() {
    // Howler autoUnlock is true by default
  }

  public static getInstance(): AudioService {
    if (!AudioService.instance) {
      AudioService.instance = new AudioService();
    }
    return AudioService.instance;
  }

  public playBGM(src: string, volume?: number) {
    if (volume !== undefined) this.bgmVolume = volume;
    const targetVolume = this.isMuted ? 0 : this.bgmVolume;

    // If same source is playing, do nothing
    if (this.currentBGM && (this.currentBGM as any)._src === src) {
      return;
    }

    const oldBGM = this.currentBGM;

    const newBGM = new Howl({
      src: [src],
      loop: true,
      volume: 0,
      onloaderror: (_id, err) => console.error('BGM load error:', err),
      onplayerror: (_id, err) => console.error('BGM play error:', err)
    });

    // Keep track of src manually to avoid restarting the same track
    (newBGM as any)._src = src; 
    this.currentBGM = newBGM;

    newBGM.play();
    newBGM.fade(0, targetVolume, this.fadeDuration);

    if (oldBGM) {
      const currentVol = typeof oldBGM.volume() === 'number' ? oldBGM.volume() as number : 1;
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
      const currentVol = typeof oldBGM.volume() === 'number' ? oldBGM.volume() as number : 1;
      oldBGM.fade(currentVol, 0, this.fadeDuration);
      oldBGM.once('fade', () => {
        oldBGM.unload();
      });
    }
  }

  public playSFX(src: string, volume?: number) {
    if (this.isMuted) return;
    const vol = volume !== undefined ? volume : this.sfxVolume;
    
    const sfx = new Howl({
      src: [src],
      volume: vol,
      onend: () => {
        sfx.unload();
      }
    });
    sfx.play();
  }

  public setBGMVolume(volume: number) {
    this.bgmVolume = volume;
    if (this.currentBGM && !this.isMuted) {
      this.currentBGM.volume(volume);
    }
  }

  public setSFXVolume(volume: number) {
    this.sfxVolume = volume;
  }

  public setMute(mute: boolean) {
    this.isMuted = mute;
    Howler.mute(mute);
  }
}

export const audioService = AudioService.getInstance();
