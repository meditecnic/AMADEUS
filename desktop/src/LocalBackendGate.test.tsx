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
  onRestart?: () => Promise<void>;
  openLogDirError?: Error;
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
    if (command === 'restart_app') {
      if (options.onRestart) {
        return options.onRestart();
      }
      return undefined;
    }
    if (command === 'open_startup_log_dir') {
      if (options.openLogDirError) {
        throw options.openLogDirError;
      }
      return undefined;
    }
    return undefined;
  });
  vi.stubGlobal('__TAURI_INTERNALS__', { invoke });
  return invoke;
}

const FORBIDDEN_JARGON = [
  '回环端口',
  '回环监听',
  '套接字',
  '子进程',
  '业务页面已暂停挂载',
  '业务界面已保持挂起',
  'backend-stderr.log',
];

it('classifies port conflict, collapses preview by default, puts steps before metadata, opens log folder via IPC, and restarts app on Tauri retry', async () => {
  vi.resetModules();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  });

  const redactedLogDir = '%LOCALAPPDATA%\\Amadeus\\logs';
  const redactedRuntimeDir = '%LOCALAPPDATA%\\Amadeus';
  const secretSk = 'sk-proj1234567890abcdef';
  const secretJwt = 'jwt-secret-should-not-leak';
  const secretHeader = 'amadeus-secret-header-token';
  const secretProxy = 'alice:p4ssw0rd@proxy.local:8080';

  let resolveRestart: (() => void) | undefined;
  const invoke = stubTauriGateInvoke({
    backendResults: [new Error('backend failed')],
    onRestart: () =>
      new Promise<void>((resolve) => {
        resolveRestart = resolve;
      }),
    diagnostics: {
      log_dir: redactedLogDir,
      runtime_dir: redactedRuntimeDir,
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
  expect(alert).toHaveTextContent('上次退出可能还没完全结束，或安全软件拦住了后台连接');
  for (const jargon of FORBIDDEN_JARGON) {
    expect(alert.textContent).not.toContain(jargon);
  }

  // Suggested troubleshooting steps must appear before technical path metadata (<dl>).
  const stepsHeading = screen.getByRole('heading', { name: '建议排查步骤' });
  const metaList = alert.querySelector('dl');
  expect(metaList).not.toBeNull();
  expect(Boolean(stepsHeading.compareDocumentPosition(metaList!) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);

  // Preview is collapsed by default; expanding shows redacted paths/logs while keeping secrets masked.
  const toggleBtn = screen.getByRole('button', { name: '查看诊断日志片段' });
  expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByLabelText('脱敏诊断日志预览')).not.toBeInTheDocument();
  for (const forbidden of [secretSk, secretJwt, secretHeader, 'alice:p4ssw0rd']) {
    expect(document.body.textContent).not.toContain(forbidden);
  }

  fireEvent.click(toggleBtn);
  const pre = screen.getByLabelText('脱敏诊断日志预览');
  expect(pre).toBeInTheDocument();
  expect(pre).toHaveTextContent('WinError 10048');
  expect(pre).toHaveTextContent(redactedLogDir);
  for (const forbidden of [secretSk, secretJwt, secretHeader, 'alice:p4ssw0rd']) {
    expect(document.body.textContent).not.toContain(forbidden);
  }

  fireEvent.click(screen.getByRole('button', { name: '收起诊断日志片段' }));
  expect(screen.queryByLabelText('脱敏诊断日志预览')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看诊断日志片段' }));
  expect(screen.getByLabelText('脱敏诊断日志预览')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '打开日志文件夹' }));
  await waitFor(() =>
    expect(invoke.mock.calls.some(([cmd]) => cmd === 'open_startup_log_dir')).toBe(true),
  );
  expect(revealSpy).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: '复制诊断信息' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  const copied = writeText.mock.calls[0][0] as string;
  expect(copied).toContain('port_conflict');
  expect(copied).toContain(redactedLogDir);
  for (const forbidden of [secretSk, secretJwt, secretHeader, 'alice:p4ssw0rd']) {
    expect(copied).not.toContain(forbidden);
  }
  expect(await screen.findByRole('status')).toHaveTextContent('已复制脱敏诊断信息');

  fireEvent.click(screen.getByRole('button', { name: '去问题页' }));
  expect(externalSpy).toHaveBeenCalledWith(shell.ISSUES_URL);

  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await waitFor(() =>
    expect(invoke.mock.calls.some(([cmd]) => cmd === 'restart_app')).toBe(true),
  );
  expect(screen.getByRole('button', { name: '正在重启…' })).toBeDisabled();
  resolveRestart?.();
});

it('shows manual restart fallback when restart_app rejects and allows opening logs when tails are empty', async () => {
  vi.resetModules();
  const invoke = stubTauriGateInvoke({
    backendResults: [new Error('startup failed')],
    onRestart: async () => {
      throw new Error('restart unavailable');
    },
    diagnostics: {
      log_dir: '/home/<USER>/.local/share/amadeus/logs',
      runtime_dir: '/home/<USER>/.local/share/amadeus',
      app_version: '0.1.0',
      platform: 'linux-x86_64',
      startup_log_tail: '',
      stderr_log_tail: '',
      stdout_log_tail: '',
      last_error_summary: '',
    },
  });

  const { LocalBackendGate } = await import('./LocalBackendGate');
  render(<LocalBackendGate><p>业务已启动</p></LocalBackendGate>);
  await screen.findByRole('alert');

  const openLogsButton = screen.getByRole('button', { name: '打开日志文件夹' });
  expect(openLogsButton).toBeInTheDocument();
  fireEvent.click(openLogsButton);
  await waitFor(() =>
    expect(invoke.mock.calls.some(([cmd]) => cmd === 'open_startup_log_dir')).toBe(true),
  );

  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await waitFor(() =>
    expect(invoke.mock.calls.some(([cmd]) => cmd === 'restart_app')).toBe(true),
  );
  expect(await screen.findByRole('status')).toHaveTextContent('未能自动重启应用');
  expect(screen.getByRole('button', { name: '重试' })).toBeEnabled();
});

it('classifies data_dir, timeout, vc_redist, backend_exit, and general failures into distinct plain-Chinese guidance', async () => {
  const cases: Array<{ summary: string; expectedTitle: string }> = [
    {
      summary: 'data_location_missing: C:\\Users\\bob\\AppData\\Local\\Amadeus\\data-location.json',
      expectedTitle: '无法读写数据文件夹，可能没有权限或正被其他程序占用',
    },
    {
      summary: 'READY timeout after 120 seconds',
      expectedTitle: '后台服务启动较慢，等待超时',
    },
    {
      summary: 'ImportError: DLL load failed while importing _sqlite3: vcruntime140.dll missing',
      expectedTitle: '缺少系统运行库或安装文件不完整，后台未能启动',
    },
    {
      summary: 'stdout EOF before READY; AMADEUS_BACKEND_FAILED RuntimeError',
      expectedTitle: '后台服务启动中途退出',
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
    for (const jargon of FORBIDDEN_JARGON) {
      expect(alert.textContent).not.toContain(jargon);
    }
  }
});

it('ignores invalid diagnostic shapes carrying token fields and expands collapsed preview when clipboard fails', async () => {
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
  expect(alert).toHaveTextContent('暂时没连上本地后台服务');
  for (const jargon of FORBIDDEN_JARGON) {
    expect(alert.textContent).not.toContain(jargon);
  }
  expect(screen.queryByRole('button', { name: '打开日志文件夹' })).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain('must-never-leak-token');

  expect(screen.queryByLabelText('脱敏诊断日志预览')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '复制诊断信息' }));
  expect(await screen.findByLabelText('脱敏诊断日志预览')).toBeInTheDocument();
  expect(await screen.findByRole('status')).toHaveTextContent('自动复制不可用');
});
