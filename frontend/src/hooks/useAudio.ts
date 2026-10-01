import { audioService } from '../services/AudioService';

export const useAudio = () => {
  return {
    playBGM: (src: string, vol?: number) => audioService.playBGM(src, vol),
    stopBGM: () => audioService.stopBGM(),
    setBGMVolume: (vol: number) => audioService.setBGMVolume(vol),
    setSFXVolume: (vol: number) => audioService.setSFXVolume(vol),
    playSFX: (src: string, vol?: number) => audioService.playSFX(src, vol),
    setMute: (mute: boolean) => audioService.setMute(mute)
  };
};
