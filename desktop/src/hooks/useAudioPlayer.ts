import { useState, useEffect, useRef } from 'react';

const VOICE_VOLUME_STORAGE_KEY = 'amadeus_pc_voice_volume';

const clampVolume = (volume: number) => Math.max(0, Math.min(1, volume));

const readPersistedVoiceVolume = () => {
  try {
    const stored = window.localStorage.getItem(VOICE_VOLUME_STORAGE_KEY);
    if (stored === null) return 1.0;
    const parsed = Number(stored);
    return Number.isFinite(parsed) ? clampVolume(parsed) : 1.0;
  } catch {
    return 1.0;
  }
};

export class AudioPlayerManager {
  public ctx: AudioContext | null = null;
  public analyser: AnalyserNode | null = null;
  private gainNode: GainNode | null = null;
  private currentSource: AudioBufferSourceNode | null = null;
  private audioQueue: Array<{ buffer: AudioBuffer; onEnded?: () => void }> = [];
  private decodedQueue = new Map<number, { buffer: AudioBuffer | null; onEnded?: () => void }>();
  private nextDecodeSequence = 0;
  private decodeSequence = 0;
  private encodedBacklog: Array<{ base64Data: string; onEnded?: () => void }> = [];
  private isPlaying = false;
  private onVolumeCallback: ((vol: number) => void) | null = null;
  private onPlayStateChange: ((isPlaying: boolean) => void) | null = null;
  private onDecodeError: ((err: any) => void) | null = null;
  private animFrameId: number | null = null;
  private currentPlayId = 0;
  private isMuted = false;
  private voiceVolume = readPersistedVoiceVolume();

  init(): boolean {
    if (this.ctx) return true;
    const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
    if (!AudioContextClass) return false;
    try {
      this.ctx = new AudioContextClass();
    } catch (err) {
      console.warn('AudioContext is not available yet:', err);
      this.ctx = null;
      return false;
    }
    this.gainNode = this.ctx.createGain();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;

    this.analyser.connect(this.gainNode);
    this.gainNode.connect(this.ctx.destination);

    this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : this.voiceVolume, this.ctx.currentTime);
    const backlog = this.encodedBacklog.splice(0);
    backlog.forEach(({ base64Data, onEnded }) => {
      void this.playBase64(base64Data, onEnded);
    });
    return true;
  }

  /** Ramp the voice down over `ms`, then resolve; the caller stops playback. Gain is restored for the next turn. */
  fadeOut(ms = 400): Promise<void> {
    if (!this.gainNode || !this.ctx || !this.currentSource) return Promise.resolve();
    const gain = this.gainNode.gain;
    const now = this.ctx.currentTime;
    gain.cancelScheduledValues(now);
    gain.setValueAtTime(gain.value, now);
    gain.linearRampToValueAtTime(0, now + ms / 1000);
    return new Promise((resolve) => window.setTimeout(() => {
      if (this.gainNode && this.ctx) {
        this.gainNode.gain.cancelScheduledValues(this.ctx.currentTime);
        this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : this.voiceVolume, this.ctx.currentTime + 0.05);
      }
      resolve();
    }, ms));
  }

  setMuted(muted: boolean) {
    this.isMuted = muted;
    if (this.gainNode && this.ctx) {
      this.gainNode.gain.setValueAtTime(muted ? 0 : this.voiceVolume, this.ctx.currentTime);
    }
  }

  getVoiceVolume() {
    return this.voiceVolume;
  }

  setVoiceVolume(volume: number) {
    this.voiceVolume = clampVolume(volume);
    try {
      window.localStorage.setItem(VOICE_VOLUME_STORAGE_KEY, String(this.voiceVolume));
    } catch {
      // Audio playback remains usable when browser storage is unavailable.
    }
    if (this.gainNode && this.ctx) {
      this.gainNode.gain.setValueAtTime(this.isMuted ? 0 : this.voiceVolume, this.ctx.currentTime);
    }
  }

  async unlockAudioContext() {
    if (!this.init()) return;
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

  setDecodeErrorListener(cb: (err: any) => void) {
    this.onDecodeError = cb;
  }

  async playBase64(base64Data: string, onEnded?: () => void) {
    if (!this.init() || !this.ctx || !this.analyser) {
      this.encodedBacklog.push({ base64Data, onEnded });
      if (this.encodedBacklog.length > 32) this.encodedBacklog.shift();
      return;
    }

    const generation = this.currentPlayId;
    const sequence = this.decodeSequence++;

    try {
      if (this.ctx.state === 'suspended') {
        await this.ctx.resume();
      }
      if (generation !== this.currentPlayId) return;

      const binary = atob(base64Data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }

      const audioBuffer = await this.ctx.decodeAudioData(bytes.buffer.slice(0));

      if (generation !== this.currentPlayId) {
        return;
      }

      this.decodedQueue.set(sequence, { buffer: audioBuffer, onEnded });
    } catch (err) {
      if (generation !== this.currentPlayId) return;
      if (this.onDecodeError) this.onDecodeError(err);
      this.decodedQueue.set(sequence, { buffer: null, onEnded });
    }
    this.flushDecodedQueue();
  }

  private flushDecodedQueue() {
    while (this.decodedQueue.has(this.nextDecodeSequence)) {
      const decoded = this.decodedQueue.get(this.nextDecodeSequence)!;
      this.decodedQueue.delete(this.nextDecodeSequence);
      this.nextDecodeSequence += 1;
      if (decoded.buffer) {
        this.audioQueue.push({ buffer: decoded.buffer, onEnded: decoded.onEnded });
      }
    }
    if (!this.isPlaying && this.audioQueue.length > 0) {
      this.processQueue();
    }
  }

  private processQueue() {
    if (!this.ctx || !this.analyser) return;
    while (this.audioQueue.length > 0) {
      const { buffer, onEnded } = this.audioQueue.shift()!;
      let source: AudioBufferSourceNode | null = null;
      try {
        source = this.ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(this.analyser);
        const playingSource = source;
        source.onended = () => {
          if (this.currentSource !== playingSource) return;
          this.currentSource = null;
          playingSource.disconnect();
          try { onEnded?.(); } finally { this.processQueue(); }
        };
        source.start(0);
      } catch (err) {
        if (source) {
          source.onended = null;
          source.disconnect();
        }
        this.onDecodeError?.(err);
        continue;
      }
      this.currentSource = source;
      const wasPlaying = this.isPlaying;
      this.isPlaying = true;
      if (!wasPlaying) this.onPlayStateChange?.(true);
      this.startVolumeTracking();
      return;
    }
    const wasPlaying = this.isPlaying;
    this.isPlaying = false;
    if (wasPlaying) this.onPlayStateChange?.(false);
    this.onVolumeCallback?.(0);
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
      this.currentSource.disconnect();
      this.currentSource = null;
    }
    this.audioQueue = [];
    this.decodedQueue.clear();
    this.encodedBacklog = [];
    this.decodeSequence = 0;
    this.nextDecodeSequence = 0;
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
  const [decodeErrors, setDecodeErrors] = useState(0);
  const lastVolumeUpdateRef = useRef(0);

  useEffect(() => {
    audioPlayer.setVolumeListener((vol) => {
      const now = Date.now();
      if (now - lastVolumeUpdateRef.current < 100) return;
      lastVolumeUpdateRef.current = now;
      setCurrentVolume(vol);
    });
    audioPlayer.setPlayStateListener((playing) => {
      setIsPlaying(playing);
    });
    audioPlayer.setDecodeErrorListener((_err) => {
      setDecodeErrors((n) => n + 1);
    });
    return () => {
      audioPlayer.setVolumeListener(() => {});
      audioPlayer.setPlayStateListener(() => {});
      audioPlayer.setDecodeErrorListener(() => {});
    };
  }, []);

  return {
    currentVolume,
    isPlaying,
    decodeErrors,
    playAudio: (base64: string, onEnded?: () => void) => audioPlayer.playBase64(base64, onEnded),
    stopAudio: () => audioPlayer.stopAll(),
    initContext: () => audioPlayer.init(),
    unlockAudioContext: () => audioPlayer.unlockAudioContext(),
    setMuted: (muted: boolean) => audioPlayer.setMuted(muted),
    voiceVolume: audioPlayer.getVoiceVolume(),
    setVoiceVolume: (volume: number) => audioPlayer.setVoiceVolume(volume),
  };
};
