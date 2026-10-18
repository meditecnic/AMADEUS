import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
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

async function renderLogin(props: Record<string, unknown> = {}) {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  const { SalieriLogin } = await import('./SalieriLogin');
  const onLoginSuccess = vi.fn();
  const onEntranceCue = vi.fn();
  const view = render(<SalieriLogin onLoginSuccess={onLoginSuccess} onEntranceCue={onEntranceCue} {...props} />);
  // The USER ID types itself in after the form has risen.
  await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
  return { ...view, onLoginSuccess, onEntranceCue };
}

it('sends a Unicode password unchanged and hands over to the entrance on success', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal('fetch', fetchMock);
  const { onLoginSuccess, container } = await renderLogin();

  expect(screen.getByLabelText('USER ID')).toHaveValue('Salieri');
  const pwd = screen.getByLabelText('PASSWORD');
  expect(pwd).toHaveFocus();
  // An IME commits its text only after composition; the field must not submit mid-composition.
  fireEvent.compositionStart(pwd);
  fireEvent.change(pwd, { target: { value: 'Gott würfelt nicht · 世界線 🜂' } });
  fireEvent.compositionEnd(pwd);
  await act(async () => { fireEvent.submit(pwd.closest('form')!); });

  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/auth/salieri'), expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ password: 'Gott würfelt nicht · 世界線 🜂' }),
  }));
  expect(onLoginSuccess).toHaveBeenCalledTimes(1);
  expect(container.querySelector('.sl-root')).toHaveClass('exiting');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('rejects a wrong password with an alert, keeps the user on the terminal and clears after 3s', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: '口令不正确，请重试。' }) }));
  const { onLoginSuccess, container } = await renderLogin();

  const pwd = screen.getByLabelText('PASSWORD');
  fireEvent.change(pwd, { target: { value: 'amadeus' } });
  await act(async () => { fireEvent.keyDown(pwd, { key: 'Enter' }); fireEvent.submit(pwd.closest('form')!); });

  expect(screen.getByRole('alert')).toHaveTextContent('REJECTED口令不正确，请重试。');
  expect(pwd).toHaveAttribute('aria-invalid', 'true');
  expect(onLoginSuccess).not.toHaveBeenCalled();
  expect(container.querySelector('.sl-root')).not.toHaveClass('exiting');
  expect(screen.getByRole('button', { name: 'Decrypt and Connect' })).toBeEnabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('validates USER ID before calling the backend', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  await renderLogin();

  fireEvent.change(screen.getByLabelText('USER ID'), { target: { value: 'Okabe' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('USER ID').closest('form')!); });
  expect(screen.getByRole('alert')).toHaveTextContent('无效的 USER ID');
  expect(fetchMock).not.toHaveBeenCalled();
});

it('under reduced motion leaves with a short fade and still reports the terminal gone', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
  const { onEntranceCue, container } = await renderLogin();

  expect(container.querySelector('.sl-root')).toHaveClass('reduced');
  fireEvent.change(screen.getByLabelText('PASSWORD'), { target: { value: 'Gott würfelt nicht' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('PASSWORD').closest('form')!); });
  await act(async () => { await vi.advanceTimersByTimeAsync(450); });
  expect(onEntranceCue).toHaveBeenCalledWith('gone');
  expect(onEntranceCue).not.toHaveBeenCalledWith('light');
});
