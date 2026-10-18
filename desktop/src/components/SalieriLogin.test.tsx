import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

// Let React register standard animation events in jsdom, as Chromium does.
vi.hoisted(() => { window.AnimationEvent ??= Event as typeof AnimationEvent; });

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

async function renderLogin(reducedMotion = false) {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.stubGlobal('matchMedia', () => ({
    matches: reducedMotion, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
  const onLoginSuccess = vi.fn();
  const { SalieriLogin } = await import('./SalieriLogin');
  render(<SalieriLogin onLoginSuccess={onLoginSuccess} />);
  return {
    onLoginSuccess,
    user: screen.getByRole('textbox', { name: 'USER ID' }),
    password: screen.getByLabelText('PASSWORD'),
    form: screen.getByRole('button', { name: 'Decrypt and Connect' }).closest('form')!,
  };
}

it('validates locally without glitch or a request and focuses the invalid field', async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  const { user, form } = await renderLogin();
  fireEvent.change(user, { target: { value: 'invalid' } });
  fireEvent.submit(form);
  expect(screen.getByRole('alert')).toHaveTextContent('无效的 USER ID');
  expect(user).toHaveFocus();
  expect(screen.getByRole('alert')).not.toHaveClass('is-glitching');
  expect(fetchMock).not.toHaveBeenCalled();
});

it('gates duplicate requests, shows one terminating auth glitch, and allows a Unicode retry', async () => {
  let resolveFailure!: (value: unknown) => void;
  const fetchMock = vi.fn()
    .mockReturnValueOnce(new Promise(resolve => { resolveFailure = resolve; }))
    .mockResolvedValueOnce({ ok: true });
  vi.stubGlobal('fetch', fetchMock);
  const { user, password, form, onLoginSuccess } = await renderLogin();
  fireEvent.change(user, { target: { value: 'Salieri' } });
  fireEvent.change(password, { target: { value: 'wrong' } });
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(fetchMock).toHaveBeenCalledOnce();
  await act(async () => { resolveFailure({ ok: false, json: async () => ({ detail: '拒绝接入' }) }); });
  const error = screen.getByRole('alert');
  expect(error).toHaveClass('is-glitching');
  expect(password).toHaveFocus();
  expect(password).toBeEnabled();
  fireEvent.animationEnd(error);
  expect(error).not.toHaveClass('is-glitching');
  expect(error).toHaveTextContent('拒绝接入');
  fireEvent.change(password, { target: { value: 'Gott würfelt nicht' } });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await act(async () => { fireEvent.submit(form); });
  expect(onLoginSuccess).toHaveBeenCalledOnce();
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ password: 'Gott würfelt nicht' });
});

it('never submits an IME confirmation Enter, including keyCode 229', async () => {
  const { password, form } = await renderLogin();
  expect(fireEvent.keyDown(password, { key: 'Enter', isComposing: true })).toBe(false);
  expect(fireEvent.keyDown(password, { key: 'Enter', keyCode: 229 })).toBe(false);
  expect(fireEvent.keyDown(form, { key: 'Enter' })).toBe(true);
});

it('renders USER ID instantly and an auth failure without glitch under reduced motion', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: '访问被拒绝' }) }));
  const { user, password, form } = await renderLogin(true);
  expect(user).toHaveValue('Salieri');
  expect(document.querySelector('.login-typewriter')).not.toBeInTheDocument();
  fireEvent.change(password, { target: { value: 'wrong' } });
  await act(async () => { fireEvent.submit(form); });
  expect(screen.getByRole('alert')).not.toHaveClass('is-glitching');
  expect(password).toHaveFocus();
});

it('finishes the typewriter once and stops it immediately when the user edits', async () => {
  const { user } = await renderLogin();
  const letters = document.querySelectorAll('.login-typewriter span');
  expect(letters).toHaveLength(7);
  fireEvent.animationEnd(letters[6]);
  expect(document.querySelector('.login-typewriter')).not.toBeInTheDocument();
  fireEvent.change(user, { target: { value: 'Observer' } });
  expect(user).toHaveValue('Observer');
  expect(document.querySelector('.login-typewriter')).not.toBeInTheDocument();
});
