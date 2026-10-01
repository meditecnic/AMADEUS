import { audioService } from '../services/AudioService';

export const useAudio = () => {
  return {
    playBGM: (src: string, volume?: number) => audioService.playBGM(src, volume),
    stopBGM: () => audioService.stopBGM(),
    playSFX: (src: string, volume?: number) => audioService.playSFX(src, volume),
  };
};
