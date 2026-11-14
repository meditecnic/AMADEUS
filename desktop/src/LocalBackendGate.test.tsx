import { StrictMode, useEffect } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const initialDev = import.meta.env.DEV;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  import.meta.env.DEV = initialDev;
  vi.resetModules();
});

it('holds business components until bootstrap succeeds, shows an error and retries', async () => {
  vi.resetModules();
  const fetch = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValueOnce({
    ok: true, json: async () => ({ version: 1, http_origin: '', ws_origin: '', token: 'synthetic-gate-token' }),
  }).mockResolvedValue({ ok: true });
  vi.stubGlobal('fetch', fetch);
  const api = await import('./apiBase');
  const { LocalBackendGate } = await import('./LocalBackendGate');
  function Business() {
    useEffect(() => { void api.apiFetch('/health'); }, []);
    return <p>业务已启动</p>;
  }
  render(<StrictMode><LocalBackendGate><Business /></LocalBackendGate></StrictMode>);
  expect(screen.queryByText('业务已启动')).not.toBeInTheDocument();
  expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('业务已启动')).toBeInTheDocument();
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/health')).toBe(true));
  expect(fetch.mock.calls.filter(([url]) => url === '/__amadeus/bootstrap')).toHaveLength(2);
});

it('renders a recoverable error when packaged IPC supplies an empty token', async () => {
  vi.resetModules();
  vi.stubEnv('DEV', false);
  vi.stubGlobal('__TAURI_INTERNALS__', { invoke: vi.fn().mockResolvedValue({
    version: 1, http_origin: 'http://127.0.0.1:8000', ws_origin: 'ws://127.0.0.1:8000', token: '',
  }) });
  const { LocalBackendGate } = await import('./LocalBackendGate');
  render(<LocalBackendGate><p>业务已启动</p></LocalBackendGate>);
  expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
  expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '打开日志文件夹' })).not.toBeInTheDocument();
  expect(screen.queryByText('业务已启动')).not.toBeInTheDocument();
});

function stubTauriGateInvoke(options: {
  backendResults: Array<Error | Record<string, unknown>>;
  diagnostics?: unknown;
}) {
  let backendCall = 0;
  const invoke = vi.fn(async (command: string) => {
    if (command === 'get_local_backend') {
      const next = options.backendResults[Math.min(backendCall, options.backendResults.length - 1)];
      backendCall += 1;
      if (next instanceof Error) {
        throw next;
      }
      return next;
    }
    if (command === 'get_startup_diagnostics') {
      if (options.diagnostics instanceof Error) {
        throw options.diagnostics;
      }
      return options.diagnostics;
    }
    return undefined;
  });
  vi.stubGlobal('__TAURI_INTERNALS__', { invoke });
  return invoke;
}

it('classifies port conflict, redacts sensitive strings in UI and clipboard, opens log folder and issues page, and retries', async () => {
  vi.resetModules();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });

  const rawLogDir = 'C:\\Users\\Alice Smith\\AppData\\Local\\Amadeus\\logs';
  const rawRuntimeDir = 'C:\\Users\\Alice Smith\\AppData\\Local\\Amadeus';
  const secretSk = 'sk-proj1234567890abcdef';
  const secretJwt = 'jwt-secret-should-not-leak';
  const secretHeader = 'amadeus-secret-header-token';
  const secretProxy = 'alice:p4ssw0rd@proxy.local:8080';

  stubTauriGateInvoke({
    backendResults: [
      new Error('backend failed'),
      { version: 1, http_origin: 'http://127.0.0.1:18080', ws_origin: 'ws://127.0.0.1:18080', token: 'ok-token' },
    ],
    diagnostics: {
      log_dir: rawLogDir,
      runtime_dir: rawRuntimeDir,
      app_version: '0.1.0',
      platform: 'windows-x86_64',
      startup_log_tail: `AMADEUS_BACKEND_FAILED OSError [WinError 10048] x-amadeus-token: ${secretHeader}`,
      stderr_log_tail: `Traceback:\nOSError: [WinError 10048] Address already in use sk=${secretSk}\nhttps://${secretProxy}`,
      stdout_log_tail: `Authorization: Bearer ${secretJwt}`,
      last_error_summary: `OSError: [WinError 10048] Address already in use (Authorization: Bearer ${secretJwt})`,
    },
  });

  const shell = await import('./workstation/shell');
  const revealSpy = vi.spyOn(shell, 'revealInFolder').mockResolvedValue(true);
  const externalSpy = vi.spyOn(shell, 'openExternal').mockResolvedValue(undefined);
  const { LocalBackendGate } = await import('./LocalBackendGate');

  render(<LocalBackendGate><p>业务已启动</p></LocalBackendGate>);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('无法连接本地服务');
  expect(alert).toHaveTextContent('本地服务端口冲突或回环监听受限');

  fireEvent.click(screen.getByRole('button', { name: '查看脱敏日志片段' }));
  const pre = screen.getByLabelText('脱敏诊断日志预览');
  expect(pre).toHaveTextContent('WinError 10048');
  for (const forbidden of ['Alice Smith', secretSk, secretJwt, secretHeader, 'alice:p4ssw0rd']) {
    expect(document.body.textContent).not.toContain(forbidden);
  }

  fireEvent.click(screen.getByRole('button', { name: '打开日志文件夹' }));
  await waitFor(() => expect(revealSpy).toHaveBeenCalledWith(rawLogDir));

  fireEvent.click(screen.getByRole('button', { name: '复制诊断信息' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  const copied = writeText.mock.calls[0][0] as string;
  expect(copied).toContain('port_conflict');
  expect(copied).toContain('C:\\Users\\<USER>\\AppData\\Local\\Amadeus\\logs');
  for (const forbidden of ['Alice Smith', secretSk, secretJwt, secretHeader, 'alice:p4ssw0rd']) {
    expect(copied).not.toContain(forbidden);
  }
  expect(await screen.findByRole('status')).toHaveTextContent('已复制脱敏诊断信息');

  fireEvent.click(screen.getByRole('button', { name: '去问题页' }));
  expect(externalSpy).toHaveBeenCalledWith(shell.ISSUES_URL);

  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('业务已启动')).toBeInTheDocument();
});

it('classifies data_dir, timeout, backend_exit, and general failures into distinct Chinese guidance', async () => {
  const cases: Array<{ summary: string; expectedTitle: string }> = [
    {
      summary: 'data_location_missing: C:\\Users\\bob\\AppData\\Local\\Amadeus\\data-location.json',
      expectedTitle: '数据目录不可用、权限不足或被占用',
    },
    {
      summary: 'READY timeout after 120 seconds',
      expectedTitle: '本地服务启动等待超时',
    },
    {
      summary: 'stdout EOF before READY; AMADEUS_BACKEND_FAILED RuntimeError',
      expectedTitle: '本地服务进程意外退出',
    },
  ];

  for (const testCase of cases) {
    cleanup();
    vi.resetModules();
    stubTauriGateInvoke({
      backendResults: [new Error('backend failed')],
      diagnostics: {
        log_dir: '/home/bob/.local/share/amadeus/logs',
        runtime_dir: '/home/bob/.local/share/amadeus',
        app_version: '0.1.0',
        platform: 'linux-x86_64',
        startup_log_tail: testCase.summary,
        stderr_log_tail: '',
        stdout_log_tail: '',
        last_error_summary: testCase.summary,
      },
    });
    const { LocalBackendGate } = await import('./LocalBackendGate');
    render(<LocalBackendGate><p>业务已启动</p></LocalBackendGate>);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('无法连接本地服务');
    expect(alert).toHaveTextContent(testCase.expectedTitle);
    expect(document.body.textContent).not.toContain('bob');
  }
});

it('ignores invalid diagnostic shapes carrying token fields and falls back gracefully when clipboard fails', async () => {
  vi.resetModules();
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
  });
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    writable: true,
    value: vi.fn().mockReturnValue(false),
  });

  stubTauriGateInvoke({
    backendResults: [new Error('unable to connect')],
    diagnostics: {
      log_dir: 'C:\\Users\\alice\\logs',
      runtime_dir: 'C:\\Users\\alice',
      app_version: '0.1.0',
      platform: 'windows-x86_64',
      startup_log_tail: '',
      stderr_log_tail: '',
      stdout_log_tail: '',
      last_error_summary: '',
      token: 'must-never-leak-token',
    },
  });

  const { LocalBackendGate } = await import('./LocalBackendGate');
  render(<LocalBackendGate><p>业务已启动</p></LocalBackendGate>);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('无法连接本地服务');
  expect(alert).toHaveTextContent('本地服务尚未就绪或连接被拒绝');
  expect(screen.queryByRole('button', { name: '打开日志文件夹' })).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain('must-never-leak-token');

  fireEvent.click(screen.getByRole('button', { name: '复制诊断信息' }));
  expect(await screen.findByLabelText('脱敏诊断日志预览')).toBeInTheDocument();
  expect(await screen.findByRole('status')).toHaveTextContent('自动复制不可用');
});
