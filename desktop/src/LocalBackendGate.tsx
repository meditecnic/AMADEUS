import { invoke } from '@tauri-apps/api/core';
import { useEffect, useState, type ReactNode } from 'react';
import { initializeLocalBackend } from './apiBase';
import './LocalBackendGate.css';
import { Atmosphere } from './workstation/Atmosphere';
import { Stamp } from './workstation/Stamp';
import { ISSUES_URL, openExternal } from './workstation/shell';

export interface StartupDiagnostics {
  log_dir: string;
  runtime_dir: string;
  app_version: string;
  platform: string;
  startup_log_tail: string;
  stderr_log_tail: string;
  stdout_log_tail: string;
  last_error_summary: string;
}

export type StartupFailureCategory =
  | 'timeout'
  | 'backend_exit'
  | 'data_dir'
  | 'port_conflict'
  | 'general';

interface FailureDiagnosis {
  category: StartupFailureCategory;
  badge: string;
  title: string;
  summary: string;
  steps: string[];
}

const DIAGNOSTIC_STRING_FIELDS: ReadonlyArray<keyof StartupDiagnostics> = [
  'log_dir',
  'runtime_dir',
  'app_version',
  'platform',
  'startup_log_tail',
  'stderr_log_tail',
  'stdout_log_tail',
  'last_error_summary',
];

export function isStartupDiagnostics(value: unknown): value is StartupDiagnostics {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  // Never accept an object carrying a backend bearer token as diagnostics.
  if ('token' in record) {
    return false;
  }
  return DIAGNOSTIC_STRING_FIELDS.every((field) => typeof record[field] === 'string');
}

export function sanitizeDiagnosticsText(input: string): string {
  if (!input) {
    return '';
  }
  let out = input;
  // Windows user profile segments (preserve drive/prefix for path debugging, strip username).
  out = out.replace(
    /([A-Za-z]:(?:\\\\|\\|\/)Users(?:\\\\|\\|\/))([^\\/\r\n"'`<>|;,]+)/gi,
    (_m, prefix: string, user: string) => (user === '<USER>' ? _m : `${prefix}<USER>`),
  );
  // POSIX / macOS home directories without altering HTTP URLs like https://example.com/home/docs.
  out = out.replace(
    /(^|[^A-Za-z0-9_-])(\/(?:home|Users)\/)([^\\/\s"'`<>|:;,)[\]{}?#]+)/g,
    (m, lead: string, root: string, user: string) =>
      user === '<USER>' ? m : `${lead}${root}<USER>`,
  );
  // URL userinfo credentials.
  out = out.replace(/(:\/\/)([^\s/@"'?#]+:[^\s/@"'?#]+)@/g, '$1[REDACTED]@');
  // Bearer credentials.
  out = out.replace(/\bBearer[ \t]+([^\s"',;)\]}<>]+)/gi, (full, tok: string) =>
    tok.startsWith('[REDACTED') ? full : 'Bearer [REDACTED]',
  );
  // Recognizable API keys and GitHub tokens.
  out = out.replace(
    /(^|[^A-Za-z0-9_-])(sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{10,}|(?:ghp|gho|github_pat)_[A-Za-z0-9_]{8,})/g,
    '$1[REDACTED_KEY]',
  );
  // Standalone 64-char hex local tokens.
  out = out.replace(
    /(^|[^A-Za-z0-9_])([0-9a-fA-F]{64})(?![A-Za-z0-9_])/g,
    '$1[REDACTED_TOKEN]',
  );
  // Sensitive headers, env vars, JSON keys, and query parameters (quoted values).
  out = out.replace(
    /(^|[^A-Za-z0-9_])((?:amadeus_local_api_token|x-amadeus-token|authorization|client_secret|refresh_token|access_token|secret_key|id_token|password|api_key|api-key|apikey|passwd|token|pwd)["']?[ \t]*[:=][ \t]*)(["'])([^"'\r\n]*)\3/gi,
    (full, lead: string, prefix: string, quote: string, val: string) =>
      !val || val.startsWith('[REDACTED')
        ? full
        : `${lead}${prefix}${quote}[REDACTED]${quote}`,
  );
  // Authorization header with scheme + unquoted credential (e.g. Basic <b64>).
  out = out.replace(
    /(^|[^A-Za-z0-9_])(authorization[ \t]*:[ \t]*)(?!Bearer[ \t]+\[REDACTED)([A-Za-z]+[ \t]+)?([^\s"',;)\]}<>]+)/gi,
    (full, lead: string, prefix: string, _scheme: string | undefined, val: string) =>
      val.startsWith('[REDACTED') ? full : `${lead}${prefix}[REDACTED]`,
  );
  // Unquoted sensitive key=value / header: value pairs.
  out = out.replace(
    /(^|[^A-Za-z0-9_])((?:amadeus_local_api_token|x-amadeus-token|authorization|client_secret|refresh_token|access_token|secret_key|id_token|password|api_key|api-key|apikey|passwd|token|pwd)[ \t]*[:=][ \t]*)([^\s"',;)\]}<>&]+)/gi,
    (full, lead: string, prefix: string, val: string) => {
      if (!val || val.startsWith('[REDACTED')) {
        return full;
      }
      if (/^authorization[ \t]*:/i.test(prefix) && /^bearer$/i.test(val)) {
        return full;
      }
      return `${lead}${prefix}[REDACTED]`;
    },
  );
  return out;
}

const PORT_CONFLICT_PATTERNS = [
  /WinError\s*10048/i,
  /WinError\s*10013/i,
  /address already in use/i,
  /only one usage of each socket address/i,
  /EADDRINUSE/i,
  /BACKEND_PORT\s+must\s+be/i,
  /bind\s+on\s+address/i,
  /Errno\s*(?:98|48)\b/i,
  /通常每个套接字地址/,
  /端口.*占用/,
];

const DATA_DIR_PATTERNS = [
  /data_location_missing/i,
  /data-location\.json/i,
  /amadeus-data\.json/i,
  /AMADEUS_DATA_DIR\s+required/i,
  /无法锁定数据目录/,
  /desktop\.lock/i,
  /database is locked/i,
  /sqlite3\.OperationalError/i,
  /PermissionError/i,
  /WinError\s*5\b/i,
  /\b(?:EACCES|EPERM)\b/,
  /permission denied/i,
  /readonly database/i,
  /directory does not exist/i,
  /unsupported data marker/i,
  /invalid absolute path/i,
  /拒绝访问/,
];

const TIMEOUT_PATTERNS = [
  /READY\s+timeout/i,
  /timed\s+out/i,
  /timeout\s+after\s+\d+/i,
];

const VC_REDIST_PATTERNS = [
  /DLL load failed/i,
  /vcruntime/i,
  /msvcp140/i,
  /WinError\s*126\b/i,
  /找不到指定的模块/,
];

const BACKEND_EXIT_PATTERNS = [
  /unable to start backend/i,
  /stdout\s+(?:EOF|closed|read failed)\s+before\s+READY/i,
  /AMADEUS_BACKEND_FAILED/i,
  /Traceback\s+\(most recent call last\)/i,
  /ModuleNotFoundError/i,
  /ImportError/i,
  ...VC_REDIST_PATTERNS,
  /READY\s+(?:version mismatch|pid mismatch|port out of range|invalid JSON)/i,
  /exited unexpectedly/i,
  /amadeus-backend\.exe/i,
];

export function classifyStartupFailure(params: {
  errorMessage?: string;
  diagnostics: StartupDiagnostics | null;
}): FailureDiagnosis {
  const haystack = [
    params.diagnostics?.last_error_summary ?? '',
    params.diagnostics?.startup_log_tail ?? '',
    params.diagnostics?.stderr_log_tail ?? '',
    params.diagnostics?.stdout_log_tail ?? '',
    params.errorMessage ?? '',
  ].join('\n');

  if (PORT_CONFLICT_PATTERNS.some((pattern) => pattern.test(haystack))) {
    return {
      category: 'port_conflict',
      badge: '后台连接受阻',
      title: '上次退出可能还没完全结束，或安全软件拦住了后台连接',
      summary:
        '上次退出可能还没完全结束，或本机安全软件拦住了后台服务连接。',
      steps: [
        '等几秒钟直接点下方「重试」；若仍不行，打开任务管理器结束残留的 AMADEUS 相关程序后再试。',
        '检查防火墙、杀毒软件或代理工具，确认没有拦截 AMADEUS 在本机启动后台服务。',
        '若反复出现，点下方「复制诊断信息」并到问题页反馈。',
      ],
    };
  }

  if (DATA_DIR_PATTERNS.some((pattern) => pattern.test(haystack))) {
    return {
      category: 'data_dir',
      badge: '数据文件夹异常',
      title: '无法读写数据文件夹，可能没有权限或正被其他程序占用',
      summary:
        'AMADEUS 打不开或写不了本地数据文件夹，常见原因是移动过目录、文件夹只读，或网盘同步与重复打开的程序占用了文件。',
      steps: [
        '如果同时打开了另一个 AMADEUS，或正在用网盘、备份软件同步数据文件夹，先关掉它们再点「重试」。',
        '如果改过数据保存位置，确认那个文件夹还在、磁盘已连接，并且当前账号有读写权限。',
        '仍无法打开时，点「打开日志文件夹」或「复制诊断信息」到问题页反馈。',
      ],
    };
  }

  if (TIMEOUT_PATTERNS.some((pattern) => pattern.test(haystack))) {
    return {
      category: 'timeout',
      badge: '启动超时',
      title: '后台服务启动较慢，等待超时',
      summary:
        '后台服务没有在限定时间内准备好，首次打开或杀毒软件正在扫描时常会这样。',
      steps: [
        '直接点下方「重试」，首次启动或电脑较忙时通常再试一次即可进入。',
        '检查安全软件是否正在扫描或拦截了 AMADEUS 的后台程序。',
        '如果多次重试都超时，点「复制诊断信息」并到问题页反馈。',
      ],
    };
  }

  if (BACKEND_EXIT_PATTERNS.some((pattern) => pattern.test(haystack))) {
    if (VC_REDIST_PATTERNS.some((pattern) => pattern.test(haystack))) {
      return {
        category: 'backend_exit',
        badge: '缺少系统组件',
        title: '缺少系统运行库或安装文件不完整，后台未能启动',
        summary:
          'Windows 缺少必要的运行库（Microsoft Visual C++ Redistributable）或安装文件缺失，后台程序一启动就退出了。',
        steps: [
          '安装或修复微软官方的「Microsoft Visual C++ Redistributable (x64)」运行库后，重新打开 AMADEUS。',
          '若已安装过该运行库，建议重新下载最新安装包覆盖安装一次，并确认杀毒软件没有误删文件。',
          '仍无法解决时，点「复制诊断信息」并到问题页反馈。',
        ],
      };
    }

    return {
      category: 'backend_exit',
      badge: '后台异常退出',
      title: '后台服务启动中途退出',
      summary:
        '后台服务在准备完成前意外停止了，为保护本地数据，暂时停留在当前页面。',
      steps: [
        '先点下方「重试」重新启动后台服务。',
        '若重试仍然退出，建议重新覆盖安装最新版本，并确认安全软件没有隔离程序文件。',
        '点「复制诊断信息」并到问题页反馈，方便定位原因。',
      ],
    };
  }

  return {
    category: 'general',
    badge: '尚未连上',
    title: '暂时没连上本地后台服务',
    summary:
      'AMADEUS 还没能连上本机后台服务，请先试着重新连接。',
    steps: [
      '点下方「重试」重新连接后台服务。',
      '如果完全退出后重新打开仍停在这里，检查安全软件是否拦截了 AMADEUS。',
      '需要帮助时，点「复制诊断信息」并到问题页反馈。',
    ],
  };
}

function buildSanitizedPreview(
  diagnosis: FailureDiagnosis,
  errorMessage: string,
  diagnostics: StartupDiagnostics | null,
): string {
  const lines: string[] = [
    `分类: ${diagnosis.badge} (${diagnosis.category})`,
  ];

  if (diagnostics) {
    lines.push(`版本: ${sanitizeDiagnosticsText(diagnostics.app_version) || '未知'}`);
    lines.push(`平台: ${sanitizeDiagnosticsText(diagnostics.platform) || '未知'}`);
    lines.push(`运行目录: ${sanitizeDiagnosticsText(diagnostics.runtime_dir) || '未知'}`);
    lines.push(`日志目录: ${sanitizeDiagnosticsText(diagnostics.log_dir) || '未知'}`);
    const summary = sanitizeDiagnosticsText(diagnostics.last_error_summary.trim());
    if (summary) {
      lines.push(`错误摘要: ${summary}`);
    }
    const tails: Array<[string, string]> = [
      ['backend-startup.log', sanitizeDiagnosticsText(diagnostics.startup_log_tail.trim())],
      ['backend-stderr.log', sanitizeDiagnosticsText(diagnostics.stderr_log_tail.trim())],
      ['backend-stdout.log', sanitizeDiagnosticsText(diagnostics.stdout_log_tail.trim())],
    ];
    for (const [label, body] of tails) {
      if (body) {
        lines.push('', `--- ${label} ---`, body);
      }
    }
    if (tails.every(([, body]) => !body)) {
      lines.push('', '--- 日志片段 ---', '暂未读取到非空日志文件片段。');
    }
  } else {
    const cleanErr = sanitizeDiagnosticsText(errorMessage.trim()) || '无法连接本地服务';
    lines.push(`错误信息: ${cleanErr}`);
    lines.push('提示: 当前运行环境未提供桌面壳层诊断上下文。');
  }

  return sanitizeDiagnosticsText(lines.join('\n'));
}

async function copyTextSafely(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to execCommand fallback below.
    }
  }
  if (typeof document === 'undefined' || !document.body) {
    return false;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '0';
  document.body.appendChild(textarea);
  try {
    textarea.select();
    return typeof document.execCommand === 'function' && document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
}

function hasTauriInternals(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

async function loadStartupDiagnosticsSafely(): Promise<StartupDiagnostics | null> {
  if (!hasTauriInternals()) {
    return null;
  }
  try {
    const raw = await invoke<unknown>('get_startup_diagnostics');
    return isStartupDiagnostics(raw) ? raw : null;
  } catch {
    return null;
  }
}

export function LocalBackendGate({ children }: { children: ReactNode }) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<'pending' | 'ready' | 'error'>('pending');
  const [errorMessage, setErrorMessage] = useState('');
  const [diagnostics, setDiagnostics] = useState<StartupDiagnostics | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    let cancelled = false;
    void initializeLocalBackend().then(
      () => {
        if (!cancelled) {
          setStatus('ready');
        }
      },
      async (err: unknown) => {
        if (cancelled) {
          return;
        }
        const message = err instanceof Error ? err.message : String(err ?? '无法连接本地服务');
        const diag = await loadStartupDiagnosticsSafely();
        if (cancelled) {
          return;
        }
        setErrorMessage(message);
        setDiagnostics(diag);
        setStatus('error');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (status === 'ready') {
    return children;
  }

  if (status === 'pending') {
    return (
      <div className="ws2 ws2-recovery ws2-gate" data-wl="sg">
        <Atmosphere worldline="steins_gate" still />
        <div className="ws2-recovery-scroll">
          <section className="ws2-recovery-sheet ws2-gate-sheet is-pending">
            <Stamp sub="自检中">INIT</Stamp>
            <header>
              <span className="fig">STARTUP GATE / LOCAL BACKEND</span>
              <h1>本地服务初始化</h1>
            </header>
            <div className="ws2-gate-pending-body">
              <span className="ws2-gate-dot" aria-hidden="true" />
              <p role="status">正在连接本地服务…</p>
            </div>
          </section>
        </div>
      </div>
    );
  }

  const diagnosis = classifyStartupFailure({ errorMessage, diagnostics });
  const sanitizedErrorSummary = sanitizeDiagnosticsText(
    (diagnostics?.last_error_summary || errorMessage || '无法连接本地服务').trim(),
  );
  const sanitizedLogDir = diagnostics?.log_dir ? sanitizeDiagnosticsText(diagnostics.log_dir) : '';
  const sanitizedRuntimeDir = diagnostics?.runtime_dir
    ? sanitizeDiagnosticsText(diagnostics.runtime_dir)
    : '';
  const previewText = buildSanitizedPreview(diagnosis, errorMessage, diagnostics);

  const handleRetry = async () => {
    if (retrying) {
      return;
    }
    setFeedback('');
    if (hasTauriInternals()) {
      setRetrying(true);
      try {
        await invoke('restart_app');
      } catch {
        setRetrying(false);
        setFeedback('未能自动重启应用，请手动关闭后重新打开 AMADEUS。');
      }
      return;
    }
    setPreviewOpen(false);
    setErrorMessage('');
    setDiagnostics(null);
    setStatus('pending');
    setAttempt((value) => value + 1);
  };

  const handleOpenLogs = async () => {
    if (!hasTauriInternals()) {
      return;
    }
    try {
      await invoke('open_startup_log_dir');
      setFeedback('已尝试在文件管理器中打开日志目录。');
    } catch {
      setFeedback('未能直接拉起文件管理器，请根据上方日志路径手动打开。');
    }
  };

  const handleCopy = async () => {
    const ok = await copyTextSafely(previewText);
    if (ok) {
      setFeedback('已复制脱敏诊断信息，可直接粘贴到反馈中。');
      return;
    }
    setPreviewOpen(true);
    setFeedback('自动复制不可用，已展开脱敏诊断文本供手动复制。');
  };

  const handleOpenIssues = () => {
    void openExternal(ISSUES_URL);
  };

  return (
    <div className="ws2 ws2-recovery ws2-gate" data-wl="beta">
      <Atmosphere worldline="beta" still />
      <div className="ws2-recovery-scroll">
        <article className="ws2-recovery-sheet ws2-gate-sheet" role="alert">
          <Stamp sub="诊断模式">FAULT</Stamp>
          <header>
            <span className="fig">STARTUP GATE / DIAGNOSTIC SHEET</span>
            <h1>无法连接本地服务</h1>
          </header>

          <div className="ws2-gate-cat">
            <span className="ws2-m-flag is-bad">{diagnosis.badge}</span>
            <strong className="ws2-gate-cat-title">{diagnosis.title}</strong>
          </div>
          <p className="lead">{diagnosis.summary}</p>

          <section className="ws2-m-sec">
            <h2 className="ws2-m-group">建议排查步骤</h2>
            <ol className="ws2-gate-guide">
              {diagnosis.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </section>

          <section className="ws2-m-sec">
            <h2 className="ws2-m-group">错误摘要</h2>
            <p className="ws2-gate-summary-box">{sanitizedErrorSummary}</p>
            {diagnostics && (
              <dl className="ws2-gate-meta-grid">
                <div className="ws2-gate-meta-item">
                  <dt className="ws2-gate-meta-label">版本 / 平台</dt>
                  <dd className="ws2-gate-meta-val">
                    {sanitizeDiagnosticsText(diagnostics.app_version)} ·{' '}
                    {sanitizeDiagnosticsText(diagnostics.platform)}
                  </dd>
                </div>
                <div className="ws2-gate-meta-item">
                  <dt className="ws2-gate-meta-label">日志目录（已脱敏）</dt>
                  <dd className="ws2-gate-meta-val">{sanitizedLogDir}</dd>
                </div>
                {sanitizedRuntimeDir && (
                  <div className="ws2-gate-meta-item">
                    <dt className="ws2-gate-meta-label">运行目录（已脱敏）</dt>
                    <dd className="ws2-gate-meta-val">{sanitizedRuntimeDir}</dd>
                  </div>
                )}
              </dl>
            )}
          </section>

          <section className="ws2-m-sec">
            <div className="ws2-m-group ws2-gate-diag-head">
              <h2>诊断日志片段</h2>
              <button
                type="button"
                className="ws2-m-link ws2-gate-toggle"
                aria-expanded={previewOpen}
                onClick={() => setPreviewOpen((open) => !open)}
              >
                {previewOpen ? '收起诊断日志片段' : '查看诊断日志片段'}
              </button>
            </div>
            {previewOpen && (
              <pre className="ws2-gate-pre" aria-label="脱敏诊断日志预览">
                {previewText}
              </pre>
            )}
          </section>

          <footer className="ws2-m-foot">
            <div className="ws2-m-acts">
              <button
                type="button"
                className="ws2-m-btn primary"
                disabled={retrying}
                onClick={() => void handleRetry()}
              >
                {retrying ? '正在重启…' : '重试'}
              </button>
              {hasTauriInternals() && diagnostics !== null && (
                <button
                  type="button"
                  className="ws2-m-btn"
                  onClick={() => void handleOpenLogs()}
                >
                  打开日志文件夹
                </button>
              )}
              <button
                type="button"
                className="ws2-m-btn"
                onClick={() => void handleCopy()}
              >
                复制诊断信息
              </button>
              <button type="button" className="ws2-m-link" onClick={handleOpenIssues}>
                去问题页
              </button>
            </div>
            {feedback && (
              <p className="ws2-gate-feedback" role="status">
                {feedback}
              </p>
            )}
          </footer>
        </article>
      </div>
    </div>
  );
}
