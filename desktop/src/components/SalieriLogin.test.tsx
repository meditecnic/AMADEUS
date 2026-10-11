import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('../services/AudioService', () => ({
  audioService: { playBGM: vi.fn(), stopBGM: vi.fn(), playSFX: vi.fn() },
}));
vi.mock('../hooks/useAudioPlayer', () => ({ audioPlayer: { init: vi.fn() } }));
vi.mock('../assetPack', () => ({ packAsset: () => '' }));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

it('keeps packaged login disabled until the local backend health check succeeds', async () => {
  vi.useFakeTimers();
  vi.stubEnv('DEV', false);
  vi.stubEnv('VITE_API_BASE', undefined);
  vi.stubGlobal('__TAURI_INTERNALS__', {});
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: false })
    .mockResolvedValue({ ok: true });
  vi.stubGlobal('fetch', fetchMock);
  const { SalieriLogin } = await import('./SalieriLogin');
  render(<SalieriLogin onLoginSuccess={vi.fn()} />);

  const button = screen.getByRole('button', { name: 'Decrypt and Connect' });
  expect(button).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('正在启动本地服务…');
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(button).toBeEnabled();
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenLastCalledWith('http://127.0.0.1:8000/health', expect.objectContaining({ signal: expect.any(AbortSignal) }));
});
