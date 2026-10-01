import { useState, useEffect } from 'react';

class AudioPlayerManager {
  public ctx: AudioContext | null = null;
  public analyser: AnalyserNode | null = null;
  private gainNode: GainNode | null = null;
  private currentSource: AudioBufferSourceNode | null = null;
  private audioQueue: Array<{ buffer: AudioBuffer; onEnded?: () => void }> = [];
  private isPlaying = false;
  private onVolumeCallback: ((vol: number) => void) | null = null;
  private onPlayStateChange: ((isPlaying: boolean) => void) | null = null;
  private animFrameId: number | null = null;
  private currentPlayId = 0;
  private isMuted = false;

  init() {
    if (this.ctx) return;
    const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
    this.ctx = new AudioContextClass();
    this.gainNode = this.ctx.createGain();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    
    this.analyser.connect(this.gainNode);
    this.gainNode.connect(this.ctx.destination);
    
    this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : 1, this.ctx.currentTime);
  }

  setMuted(muted: boolean) {
    this.isMuted = muted;
    if (this.gainNode && this.ctx) {
      this.gainNode.gain.setValueAtTime(muted ? 0 : 1, this.ctx.currentTime);
    }
  }

  async unlockAudioContext() {
    this.init();
    if (!this.ctx) return;
    if (this.ctx.state === 'suspended') {
      await this.ctx.resume();
    }
    const buffer = this.ctx.createBuffer(1, 1, 22050);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);
    source.start(0);
  }

  setVolumeListener(cb: (vol: number) => void) {
    this.onVolumeCallback = cb;
  }

  setPlayStateListener(cb: (isPlaying: boolean) => void) {
    this.onPlayStateChange = cb;
  }

  async playBase64(base64Data: string, onEnded?: () => void) {
    this.init();
    if (!this.ctx || !this.analyser) return;

    if (this.ctx.state === 'suspended') {
      await this.ctx.resume();
    }

    const playId = ++this.currentPlayId;

    try {
      const binary = atob(base64Data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }

      const audioBuffer = await this.ctx.decodeAudioData(bytes.buffer.slice(0));
      
      if (playId !== this.currentPlayId) {
        return;
      }

      this.audioQueue.push({ buffer: audioBuffer, onEnded });
      if (!this.isPlaying) {
        this.processQueue();
      }
    } catch (err) {
      console.error('Audio decode failed:', err);
    }
  }

  private processQueue() {
    if (this.audioQueue.length === 0) {
      const wasPlaying = this.isPlaying;
      this.isPlaying = false;
      if (wasPlaying && this.onPlayStateChange) this.onPlayStateChange(false);
      if (this.onVolumeCallback) this.onVolumeCallback(0);
      return;
    }

    const wasPlaying = this.isPlaying;
    this.isPlaying = true;
    if (!wasPlaying && this.onPlayStateChange) this.onPlayStateChange(true);
    const { buffer, onEnded } = this.audioQueue.shift()!;
    
    if (!this.ctx || !this.analyser) return;

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    
    source.connect(this.analyser);
    this.currentSource = source;
    
    source.onended = () => {
      if (onEnded) onEnded();
      this.processQueue();
    };

    source.start(0);
    this.startVolumeTracking();
  }

  private startVolumeTracking() {
    if (this.animFrameId) cancelAnimationFrame(this.animFrameId);
    if (!this.analyser) return;

    const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
    
    const track = () => {
      if (!this.isPlaying || !this.analyser) {
        if (this.onVolumeCallback) this.onVolumeCallback(0);
        return;
      }
      this.analyser.getByteTimeDomainData(dataArray);
      
      let sumSquares = 0;
      for (let i = 0; i < dataArray.length; i++) {
        const normalized = (dataArray[i] - 128) / 128;
        sumSquares += normalized * normalized;
      }
      const rms = Math.sqrt(sumSquares / dataArray.length);
      
      if (this.onVolumeCallback) {
        this.onVolumeCallback(rms);
      }
      this.animFrameId = requestAnimationFrame(track);
    };

    this.animFrameId = requestAnimationFrame(track);
  }

  stopAll() {
    this.currentPlayId++;
    if (this.currentSource) {
      this.currentSource.onended = null;
      try {
        this.currentSource.stop();
      } catch (e) {}
      this.currentSource = null;
    }
    this.audioQueue = [];
    const wasPlaying = this.isPlaying;
    this.isPlaying = false;
    if (wasPlaying && this.onPlayStateChange) this.onPlayStateChange(false);
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.onVolumeCallback) this.onVolumeCallback(0);
  }
}

export const audioPlayer = new AudioPlayerManager();

export const useAudioPlayer = () => {
  const [currentVolume, setCurrentVolume] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    audioPlayer.setVolumeListener((vol) => {
      setCurrentVolume(vol);
    });
    audioPlayer.setPlayStateListener((playing) => {
      setIsPlaying(playing);
    });
    return () => {
      audioPlayer.setVolumeListener(() => {});
      audioPlayer.setPlayStateListener(() => {});
    };
  }, []);

  return {
    currentVolume,
    isPlaying,
    playAudio: (base64: string, onEnded?: () => void) => audioPlayer.playBase64(base64, onEnded),
    stopAudio: () => audioPlayer.stopAll(),
    initContext: () => audioPlayer.init(),
    unlockAudioContext: () => audioPlayer.unlockAudioContext(),
    setMuted: (muted: boolean) => audioPlayer.setMuted(muted)
  };
};
