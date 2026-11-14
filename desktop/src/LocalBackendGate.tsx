import { invoke } from '@tauri-apps/api/core';
import { useEffect, useState, type ReactNode } from 'react';
import { initializeLocalBackend } from './apiBase';
import './LocalBackendGate.css';
import { Atmosphere } from './workstation/Atmosphere';
import { Stamp } from './workstation/Stamp';
import { ISSUES_URL, openExternal, revealInFolder } from './workstation/shell';

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

const BACKEND_EXIT_PATTERNS = [
  /unable to start backend/i,
  /stdout\s+(?:EOF|closed|read failed)\s+before\s+READY/i,
  /AMADEUS_BACKEND_FAILED/i,
  /Traceback\s+\(most recent call last\)/i,
  /ModuleNotFoundError/i,
  /ImportError/i,
  /DLL load failed/i,
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
      badge: 'PORT / LOOPBACK',
      title: '本地服务端口冲突或回环监听受限',
      summary:
        '本地服务无法绑定 127.0.0.1 回环端口，可能是上次进程尚未完全退出，或本地安全策略拦截了回环监听。',
      steps: [
        '等待数秒，或在任务管理器中结束残留的 Amadeus / amadeus-backend.exe 进程后点击「重试」。',
        '检查防火墙、安全软件或系统代理规则是否限制了 127.0.0.1 本地端口监听。',
        '打开日志目录检查 backend-stderr.log 中的端口绑定报错，或复制脱敏诊断信息反馈。',
      ],
    };
  }

  if (DATA_DIR_PATTERNS.some((pattern) => pattern.test(haystack))) {
    return {
      category: 'data_dir',
      badge: 'DATA DIRECTORY',
      title: '数据目录不可用、权限不足或被占用',
      summary:
        '桌面端无法读取、创建或锁定本地数据目录。可能是自定义目录已移动、缺少写入权限，或数据库正被其他进程占用。',
      steps: [
        '确认目标数据目录存在且未被系统安全防护或只读属性限制写入。',
        '若自定义过数据目录，请确认原路径完整有效，或移除异常残留的锁文件后再重试。',
        '关闭可能正在同步或占用数据库文件的网盘/备份程序及重复实例。',
      ],
    };
  }

  if (TIMEOUT_PATTERNS.some((pattern) => pattern.test(haystack))) {
    return {
      category: 'timeout',
      badge: 'STARTUP TIMEOUT',
      title: '本地服务启动等待超时',
      summary:
        '本地服务未在预期时间内完成初始化，可能是首次冷启动耗时较长，或安全软件拦截了后端子进程输出。',
      steps: [
        '首次冷启动或杀毒软件实时扫描期间耗时可能较长，请点击「重试」再尝试连接。',
        '检查安全软件是否拦截或隔离了 amadeus-backend.exe。',
        '展开下方脱敏日志片段，确认启动停留在哪个阶段，必要时复制诊断信息反馈。',
      ],
    };
  }

  if (BACKEND_EXIT_PATTERNS.some((pattern) => pattern.test(haystack))) {
    return {
      category: 'backend_exit',
      badge: 'PROCESS EXIT',
      title: '本地服务进程意外退出',
      summary:
        '后端子进程在就绪前提前退出或返回了异常状态，为避免数据损坏，业务界面已保持挂起。',
      steps: [
        '点击「重试」重新拉起本地服务进程。',
        '打开日志目录查看 backend-stderr.log 末尾异常堆栈；若提示组件缺失，建议重新覆盖安装最新版本。',
        '复制下方脱敏诊断信息并前往问题页反馈，便于研发排查环境兼容性问题。',
      ],
    };
  }

  return {
    category: 'general',
    badge: 'CONNECTION FAULT',
    title: '本地服务尚未就绪或连接被拒绝',
    summary:
      '桌面端未能在本次启动中建立与本地后端的可用会话，业务页面已暂停挂载。',
    steps: [
      '点击「重试」重新检测本地服务连接。',
      '若在本地开发模式下运行，请确认后端服务已启动且配置正确。',
      '若问题持续存在，可展开脱敏诊断信息或前往问题页查找对应排查指南。',
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

async function loadStartupDiagnosticsSafely(): Promise<StartupDiagnostics | null> {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) {
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

  const handleRetry = () => {
    setFeedback('');
    setPreviewOpen(false);
    setErrorMessage('');
    setDiagnostics(null);
    setStatus('pending');
    setAttempt((value) => value + 1);
  };

  const handleOpenLogs = async () => {
    if (!diagnostics?.log_dir) {
      return;
    }
    const opened = await revealInFolder(diagnostics.log_dir);
    setFeedback(
      opened
        ? '已尝试在文件管理器中打开日志目录。'
        : '未能直接拉起文件管理器，请根据下方日志路径手动打开。',
    );
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
            <h2 className="ws2-m-group">错误摘要</h2>
            <p className="ws2-gate-summary-box">{sanitizedErrorSummary}</p>
            {diagnostics && (
              <div className="ws2-gate-meta-grid">
                <div className="ws2-gate-meta-item">
                  <span className="ws2-gate-meta-label">版本 / 平台</span>
                  <span className="ws2-gate-meta-val">
                    {sanitizeDiagnosticsText(diagnostics.app_version)} ·{' '}
                    {sanitizeDiagnosticsText(diagnostics.platform)}
                  </span>
                </div>
                <div className="ws2-gate-meta-item">
                  <span className="ws2-gate-meta-label">日志目录（已脱敏）</span>
                  <span className="ws2-gate-meta-val">{sanitizedLogDir}</span>
                </div>
                {sanitizedRuntimeDir && (
                  <div className="ws2-gate-meta-item">
                    <span className="ws2-gate-meta-label">运行目录（已脱敏）</span>
                    <span className="ws2-gate-meta-val">{sanitizedRuntimeDir}</span>
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="ws2-m-sec">
            <h2 className="ws2-m-group">建议排查步骤</h2>
            <ol className="ws2-gate-guide">
              {diagnosis.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </section>

          <section className="ws2-m-sec">
            <div className="ws2-m-group ws2-gate-diag-head">
              <h2>脱敏诊断预览</h2>
              <button
                type="button"
                className="ws2-m-link ws2-gate-toggle"
                aria-expanded={previewOpen}
                onClick={() => setPreviewOpen((open) => !open)}
              >
                {previewOpen ? '收起脱敏日志片段' : '查看脱敏日志片段'}
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
              <button type="button" className="ws2-m-btn primary" onClick={handleRetry}>
                重试
              </button>
              {diagnostics?.log_dir && (
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
