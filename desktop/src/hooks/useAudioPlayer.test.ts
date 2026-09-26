import { afterEach, describe, expect, it, vi } from 'vitest';

import { AudioPlayerManager } from './useAudioPlayer';


describe('AudioPlayerManager', () => {
  afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

  it.each(['resume', 'start'])('plays the next segment after a %s failure', async (failure) => {
    const started: number[] = [];
    const errors: unknown[] = [];
    let decodeId = 0;
    let attempts = 0;
    class FakeAudioContext {
      state = failure === 'resume' ? 'suspended' : 'running';
      currentTime = 0;
      destination = {};
      createGain() { return { connect: vi.fn(), gain: { setValueAtTime: vi.fn() } }; }
      createAnalyser() { return { connect: vi.fn(), fftSize: 0, frequencyBinCount: 1, getByteTimeDomainData: vi.fn() }; }
      async resume() { this.state = 'running'; throw new Error('audio temporarily unavailable'); }
      async decodeAudioData() { return { id: ++decodeId } as unknown as AudioBuffer; }
      createBufferSource() {
        const source = {
          buffer: null as AudioBuffer | null,
          connect: vi.fn(), disconnect: vi.fn(), stop: vi.fn(), onended: null,
          start() {
            if (failure === 'start' && attempts++ === 0) throw new Error('audio start failed');
            started.push((source.buffer as unknown as { id: number }).id);
          },
        };
        return source;
      }
    }
    vi.stubGlobal('AudioContext', FakeAudioContext);
    vi.stubGlobal('requestAnimationFrame', () => 1);
    const manager = new AudioPlayerManager();
    manager.setDecodeErrorListener(error => errors.push(error));
    const first = manager.playBase64('AQ==').catch(error => error);
    await first;
    await manager.playBase64('Ag==');
    expect(started).toEqual([failure === 'resume' ? 1 : 2]);
    expect(errors).toHaveLength(1);
    manager.stopAll();
  });

  it('ignores decoding failures from stopped playback', async () => {
    let rejectDecode!: (reason: Error) => void;
    class FakeAudioContext {
      state = 'running'; currentTime = 0; destination = {};
      createGain() { return { connect: vi.fn(), gain: { setValueAtTime: vi.fn() } }; }
      createAnalyser() { return { connect: vi.fn(), fftSize: 0, frequencyBinCount: 1 }; }
      decodeAudioData() { return new Promise<AudioBuffer>((_resolve, reject) => { rejectDecode = reject; }); }
    }
    vi.stubGlobal('AudioContext', FakeAudioContext);
    const manager = new AudioPlayerManager();
    const errors = vi.fn();
    manager.setDecodeErrorListener(errors);
    const pending = manager.playBase64('AQ==');
    manager.stopAll();
    rejectDecode(new Error('late decode failure'));
    await pending;
    expect(errors).not.toHaveBeenCalled();
  });
  it('persists voice volume and keeps mute independent from the saved value', () => {
    const gain = { setValueAtTime: vi.fn() };
    class FakeAudioContext {
      state = 'running';
      currentTime = 0;
      destination = {};
      createGain() { return { connect: vi.fn(), gain }; }
      createAnalyser() { return { connect: vi.fn(), fftSize: 0, frequencyBinCount: 1, getByteTimeDomainData: vi.fn() }; }
    }

    vi.stubGlobal('AudioContext', FakeAudioContext);
    localStorage.clear();
    const manager = new AudioPlayerManager();
    manager.setVoiceVolume(0.4);
    expect(localStorage.getItem('amadeus_pc_voice_volume')).toBe('0.4');

    expect(manager.init()).toBe(true);
    expect(gain.setValueAtTime).toHaveBeenLastCalledWith(0.4, 0);

    manager.setMuted(true);
    expect(gain.setValueAtTime).toHaveBeenLastCalledWith(0, 0);
    expect(localStorage.getItem('amadeus_pc_voice_volume')).toBe('0.4');

    manager.setMuted(false);
    expect(gain.setValueAtTime).toHaveBeenLastCalledWith(0.4, 0);
  });

  it('restores the persisted voice volume when a new manager is created', () => {
    localStorage.setItem('amadeus_pc_voice_volume', '0.35');
    const manager = new AudioPlayerManager();
    expect(manager.getVoiceVolume()).toBe(0.35);
  });

  it('plays in arrival order when decoding completes out of order', async () => {
    const decodeResolvers: Array<(buffer: AudioBuffer) => void> = [];
    const started: number[] = [];
    const sources: Array<{ onended: (() => void) | null }> = [];

    class FakeAudioContext {
      state = 'running';
      currentTime = 0;
      destination = {};
      createGain() {
        return { connect: vi.fn(), gain: { setValueAtTime: vi.fn() } };
      }
      createAnalyser() {
        return {
          connect: vi.fn(),
          fftSize: 0,
          frequencyBinCount: 1,
          getByteTimeDomainData: vi.fn(),
        };
      }
      createBufferSource() {
        const source = {
          buffer: null as AudioBuffer | null,
          connect: vi.fn(),
          disconnect: vi.fn(),
          onended: null as (() => void) | null,
          start: () => started.push((source.buffer as unknown as { id: number }).id),
          stop: vi.fn(),
        };
        sources.push(source);
        return source;
      }
      createBuffer() { return {} as AudioBuffer; }
      resume() { return Promise.resolve(); }
      decodeAudioData() {
        return new Promise<AudioBuffer>((resolve) => decodeResolvers.push(resolve));
      }
    }

    vi.stubGlobal('AudioContext', FakeAudioContext);
    const manager = new AudioPlayerManager();
    const first = manager.playBase64('AQ==');
    const second = manager.playBase64('Ag==');

    decodeResolvers[1]({ id: 2 } as unknown as AudioBuffer);
    await Promise.resolve();
    expect(started).toEqual([]);

    decodeResolvers[0]({ id: 1 } as unknown as AudioBuffer);
    await Promise.all([first, second]);
    expect(started).toEqual([1]);

    sources.find((source) => (source as any).buffer?.id === 1)?.onended?.();
    expect(started).toEqual([1, 2]);
  });

  it('retains encoded audio while AudioContext is unavailable', async () => {
    vi.stubGlobal('AudioContext', undefined);
    const manager = new AudioPlayerManager();
    await expect(manager.playBase64('AQ==')).resolves.toBeUndefined();
    expect(manager.ctx).toBeNull();
  });
});
