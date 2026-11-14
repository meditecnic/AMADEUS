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
  vi.resetModules();
  vi.stubGlobal('__TAURI_INTERNALS__', { invoke: vi.fn().mockResolvedValue({
    version: 1, http_origin: 'http://127.0.0.1:8000', ws_origin: 'ws://127.0.0.1:8000', token: 'synthetic-login-token',
  }) });
  const { initializeLocalBackend } = await import('../apiBase');
  await initializeLocalBackend();
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
  const health = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/health'));
  expect(health).toHaveLength(2);
  expect(health[1]).toEqual(['http://127.0.0.1:8000/health', expect.objectContaining({ signal: expect.any(AbortSignal) })]);
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

  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/auth/login'), expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ passcode: 'Gott würfelt nicht · 世界線 🜂' }),
  }));
  // Accepted: the key shows its tick and the form folds into it (handover); the workstation is mounted behind the
  // terminal just after the tick is drawn.
  expect(screen.getByRole('button', { name: 'Decrypt and Connect' })).toHaveClass('is-ok');
  expect(container.querySelector('.sl-root')).toHaveClass('handover');
  expect(screen.getByRole('status')).toHaveTextContent('已连接，正在打开工作台');
  expect(onLoginSuccess).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(380); });
  expect(onLoginSuccess).toHaveBeenCalledTimes(1);
  // The dial turns while the workstation draws its first frames; with no word from it, the pen goes after 1.4 s.
  expect(container.querySelector('.sl-root')).not.toHaveClass('exiting');
  await act(async () => { await vi.advanceTimersByTimeAsync(1400); });
  expect(container.querySelector('.sl-root')).toHaveClass('exiting');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('clears the form and sweeps as soon as the workstation behind it says it is drawn', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
  const { container, onLoginSuccess, rerender } = await renderLogin();
  fireEvent.change(screen.getByLabelText('PASSWORD'), { target: { value: 'x' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('PASSWORD').closest('form')!); });
  await act(async () => { await vi.advanceTimersByTimeAsync(380); });
  expect(onLoginSuccess).toHaveBeenCalledTimes(1);
  const { SalieriLogin } = await import('./SalieriLogin');
  rerender(<SalieriLogin onLoginSuccess={onLoginSuccess} stageReady />);
  expect(container.querySelector('.sl-root')).toHaveClass('exiting');
});

it('lets the key go at the handover beat when the workstation was drawn ahead under the terminal', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
  const { container, onLoginSuccess } = await renderLogin({ stagePrepared: true });
  fireEvent.change(screen.getByLabelText('PASSWORD'), { target: { value: 'x' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('PASSWORD').closest('form')!); });
  // The tick is drawn and the form folds into the key first...
  await act(async () => { await vi.advanceTimersByTimeAsync(370); });
  expect(container.querySelector('.sl-root')).not.toHaveClass('exiting');
  // ...then unlocking the workstation and the key going are one beat: no wait for its word.
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(onLoginSuccess).toHaveBeenCalledTimes(1);
  expect(container.querySelector('.sl-root')).toHaveClass('exiting');
});

it('does not take a "drawn" left over from the last visit for this one (U 11-10: re-entry played twice)', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
  const { container, onLoginSuccess } = await renderLogin({ stageReady: true });
  fireEvent.change(screen.getByLabelText('PASSWORD'), { target: { value: 'x' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('PASSWORD').closest('form')!); });
  // Before this visit's workstation exists, nothing behind the terminal can have been drawn: no sweep yet.
  await act(async () => { await vi.advanceTimersByTimeAsync(370); });
  expect(onLoginSuccess).not.toHaveBeenCalled();
  expect(container.querySelector('.sl-root')).not.toHaveClass('exiting');
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(onLoginSuccess).toHaveBeenCalledTimes(1);
});

it('rejects a wrong password with an alert, keeps the user on the terminal and clears after 3s', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ detail: 'Invalid password' }) }));
  const { onLoginSuccess, container } = await renderLogin();

  const pwd = screen.getByLabelText('PASSWORD');
  fireEvent.change(pwd, { target: { value: 'amadeus' } });
  await act(async () => { fireEvent.keyDown(pwd, { key: 'Enter' }); fireEvent.submit(pwd.closest('form')!); });

  expect(screen.getByRole('alert')).toHaveTextContent(/^密码错误$/);
  expect(pwd).toHaveAttribute('aria-invalid', 'true');
  expect(onLoginSuccess).not.toHaveBeenCalled();
  expect(container.querySelector('.sl-root')).not.toHaveClass('exiting');
  expect(screen.getByRole('button', { name: 'Decrypt and Connect' })).toBeEnabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('reports other backend refusals by their own detail, not as a wrong password', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({ detail: '本地服务正在维护' }) }));
  await renderLogin();

  const pwd = screen.getByLabelText('PASSWORD');
  fireEvent.change(pwd, { target: { value: 'Gott würfelt nicht' } });
  await act(async () => { fireEvent.submit(pwd.closest('form')!); });
  expect(screen.getByRole('alert')).toHaveTextContent(/^本地服务正在维护$/);
});

it('validates USER ID before calling the backend', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  await renderLogin();

  fireEvent.change(screen.getByLabelText('USER ID'), { target: { value: 'Okabe' } });
  screen.getByLabelText('PASSWORD').focus();
  await act(async () => { fireEvent.submit(screen.getByLabelText('USER ID').closest('form')!); });
  expect(screen.getByRole('alert')).toHaveTextContent('无效的 USER ID');
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/auth/login'))).toHaveLength(0);
  // The keyboard goes back to the field at fault.
  expect(screen.getByLabelText('USER ID')).toHaveFocus();
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
  expect(onEntranceCue).not.toHaveBeenCalledWith('draft');
});

it('without a startup passcode, Enter on an empty field signs in with no passcode', async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn(async (url: string) => (String(url).includes('/api/auth/state')
    ? { ok: true, status: 200, json: async () => ({ passcode_set: false }) }
    : { ok: true, status: 200, json: async () => ({ status: 'success', easter_egg: false }) }));
  vi.stubGlobal('fetch', fetchMock);
  const { onLoginSuccess } = await renderLogin();

  expect(screen.getByText('没有设口令，直接按 Enter 进入')).toBeInTheDocument();
  await act(async () => { fireEvent.submit(screen.getByLabelText('PASSWORD').closest('form')!); });
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/auth/login'), expect.objectContaining({
    method: 'POST', body: JSON.stringify({ passcode: null }),
  }));
  await act(async () => { await vi.advanceTimersByTimeAsync(380); });
  expect(onLoginSuccess).toHaveBeenCalledTimes(1);
});

it('asks to wait when the server is backing off retries', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({ detail: { code: 'too_many_attempts' } }) }));
  await renderLogin();
  fireEvent.change(screen.getByLabelText('PASSWORD'), { target: { value: 'x' } });
  await act(async () => { fireEvent.submit(screen.getByLabelText('PASSWORD').closest('form')!); });
  expect(screen.getByRole('alert')).toHaveTextContent('试得太快了，请稍等几秒');
});
