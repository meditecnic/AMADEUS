import { describe, expect, it, vi } from 'vitest';

import { AudioPlayerManager } from './useAudioPlayer';


describe('AudioPlayerManager', () => {
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
