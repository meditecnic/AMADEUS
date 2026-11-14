import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import * as assetPackMod from '../assetPack';
import { ConfirmDialog } from './ConfirmDialog';
import { Stamp } from './Stamp';
import { ApiError, formatBytes, formatVersion } from './dataApi';
import { inShell, pickPath } from './shell';
import {
  assetPackStatus,
  inspectAssetPack,
  installAssetPack,
  uninstallAssetPack,
  type AssetPackInspectResult,
  type AssetPackStatus,
} from './assetPackApi';
import './AssetPackGroup.css';

/*
 * 04 角色与对话 · 可选原版素材包: inspects and installs/removes the user's own
 * portrait + BGM asset pack from a local directory or .zip archive, reloading
 * AvatarViewer, AudioService, and CSS vars live without restarting the app.
 */

const EMOTION_LABELS: Record<string, string> = {
  neutral: '平静 neutral',
  tsundere: '别扭 tsundere',
  embarrassed: '害羞 embarrassed',
  intellectual: '思考 intellectual',
  happy: '开心 happy',
  surprised: '惊讶 surprised',
  annoyed: '微恼 annoyed',
  disappointed: '失落 disappointed',
  sad: '难过 sad',
};

const ERRORS: Record<string, string> = {
  unreachable: '连不上本地后端服务。',
  data_unavailable: '本地数据目录当前不可用，请先完成工作区初始化。',
  asset_pack_not_movable: '当前通过环境变量 AMADEUS_ASSET_PACK_DIR 固定素材目录，不支持从界面安装或卸载。',
  asset_pack_busy: '素材包正在处理中，请稍后再试。',
  unsafe_target_path: '目标素材目录路径不安全，无法写入。',
  payload_too_large: '素材压缩包体积超出上限。',
  invalid_request: '请求参数无效。',
  unsupported_content_type: '上传格式不支持，请提供 .zip 压缩包或本地目录路径。',
  empty_upload: '上传的 .zip 文件是空的。',
  path_not_absolute: '请填写绝对路径，或使用选择按钮挑选目录与压缩包。',
  source_not_found: '找不到指定的目录或压缩包路径。',
  unsafe_source_path: '源路径不安全，无法读取。',
  invalid_archive: '这不是有效的 .zip 压缩包或文件已损坏。',
  unsafe_archive: '压缩包内包含不安全路径，已拒绝读取。',
  invalid_asset_pack: '素材包校验未通过，请查看缺失或异常文件明细。',
  invalid_delete_request: '卸载请求格式无效。',
  asset_pack_install_failed: '素材包安装写入失败。',
  asset_pack_remove_failed: '素材包卸载失败。',
  asset_pack_io_failed: '读写素材文件时发生 I/O 错误。',
};

function assetPackErrorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (ERRORS[error.code]) return ERRORS[error.code];
    const message = typeof error.detail?.message === 'string' ? error.detail.message.trim() : '';
    if (message) return `${message}（${error.code}）`;
    return `${fallback}（${error.code}）`;
  }
  return fallback;
}

function isZipFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return (
    lower.endsWith('.zip') ||
    file.type === 'application/zip' ||
    file.type === 'application/x-zip-compressed'
  );
}

function isInspectResult(value: unknown): value is AssetPackInspectResult {
  return Boolean(
    value &&
    typeof value === 'object' &&
    Array.isArray((value as AssetPackInspectResult).missing_files) &&
    Array.isArray((value as AssetPackInspectResult).previews),
  );
}

function OnSheet({ anchor, children }: { anchor: React.RefObject<HTMLElement | null>; children: ReactNode }) {
  const root = anchor.current?.closest<HTMLElement>('.ws2');
  return root ? createPortal(children, root) : <>{children}</>;
}

type InspectState = {
  source: string | File;
  label: string;
  result: AssetPackInspectResult;
};

export function AssetPackGroup({
  onNotice,
  relocating = false,
}: {
  onNotice?: (message: string, kind?: 'info' | 'error') => void;
  relocating?: boolean;
}) {
  const anchor = useRef<HTMLElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [status, setStatus] = useState<AssetPackStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sourcePath, setSourcePath] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const [picking, setPicking] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [uninstalling, setUninstalling] = useState(false);
  const [confirmUninstall, setConfirmUninstall] = useState(false);

  const [inspectState, setInspectState] = useState<InspectState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [uninstallError, setUninstallError] = useState<string | null>(null);

  const busy = picking || inspecting || installing || uninstalling;
  const movable = status?.movable !== false;

  const loadStatus = useCallback(async (): Promise<AssetPackStatus | null> => {
    try {
      const next = await assetPackStatus();
      setStatus(next);
      setLoadError(null);
      return next;
    } catch (failure) {
      setLoadError(assetPackErrorText(failure, '读不到素材包状态。'));
      return null;
    }
  }, []);

  useEffect(() => {
    if (relocating) return;
    void loadStatus();
  }, [loadStatus, relocating]);

  const choosePickedPath = async (kind: 'directory' | 'zip') => {
    if (busy || !movable || relocating) return;
    setPicking(true);
    setError(null);
    setDone(null);
    try {
      const picked = await pickPath(kind);
      if (!picked) return;
      setSelectedFile(null);
      setSourcePath(picked);
      setInspectState(null);
    } catch {
      setError('无法打开系统文件选择器，也可以直接填写路径或选取 .zip 文件。');
    } finally {
      setPicking(false);
    }
  };

  const applyZipFile = (file: File | null) => {
    if (relocating || !file) return;
    if (!isZipFile(file)) {
      setError('仅支持 .zip 压缩包文件；如需导入已解压目录，请在桌面端选择目录或填写路径。');
      return;
    }
    setError(null);
    setDone(null);
    setSelectedFile(file);
    setInspectState(null);
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    e.currentTarget.value = '';
    applyZipFile(file);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (busy || !movable || relocating) return;
    const file = e.dataTransfer?.files?.[0] ?? null;
    applyZipFile(file);
  };

  const activeSource: string | File | null = selectedFile ?? (sourcePath.trim() || null);
  const activeLabel = selectedFile ? selectedFile.name : sourcePath.trim();

  const handleInspect = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!activeSource || busy || !movable || relocating) return;
    setInspecting(true);
    setError(null);
    setDone(null);
    try {
      const result = await inspectAssetPack(activeSource);
      setInspectState({ source: activeSource, label: activeLabel, result });
    } catch (failure) {
      if (failure instanceof ApiError && isInspectResult(failure.detail?.inspect)) {
        setInspectState({
          source: activeSource,
          label: activeLabel,
          result: failure.detail.inspect,
        });
      } else {
        setInspectState(null);
      }
      const msg = assetPackErrorText(failure, '检查素材包失败。');
      setError(msg);
      onNotice?.(msg, 'error');
    } finally {
      setInspecting(false);
    }
  };

  const handleInstall = async () => {
    if (!inspectState || !inspectState.result.valid || busy || !movable || relocating) return;
    setInstalling(true);
    setError(null);
    setDone(null);
    try {
      const nextStatus = await installAssetPack(inspectState.source);
      await assetPackMod.reloadAssetPack?.();
      setStatus(nextStatus);
      setInspectState(null);
      setSelectedFile(null);
      const msg = '原版素材包已安装，立绘与配乐已立即生效。';
      setDone(msg);
      onNotice?.(msg, 'info');
    } catch (failure) {
      if (failure instanceof ApiError && isInspectResult(failure.detail?.inspect)) {
        setInspectState({
          source: inspectState.source,
          label: inspectState.label,
          result: failure.detail.inspect,
        });
      }
      const msg = assetPackErrorText(failure, '安装素材包失败。');
      setError(msg);
      onNotice?.(msg, 'error');
    } finally {
      setInstalling(false);
    }
  };

  const handleConfirmUninstall = async () => {
    if (busy || !movable || relocating) return;
    setUninstalling(true);
    setUninstallError(null);
    setError(null);
    setDone(null);
    try {
      const nextStatus = await uninstallAssetPack();
      await assetPackMod.reloadAssetPack?.();
      setStatus(nextStatus);
      setInspectState(null);
      setConfirmUninstall(false);
      const msg = '原版素材包已卸载。';
      setDone(msg);
      onNotice?.(msg, 'info');
    } catch (failure) {
      const msg = assetPackErrorText(failure, '卸载素材包失败。');
      const [statusRes] = await Promise.allSettled([
        loadStatus(),
        Promise.resolve(assetPackMod.reloadAssetPack?.()),
      ]);
      if (statusRes.status === 'fulfilled' && statusRes.value && !statusRes.value.installed) {
        setInspectState(null);
        setConfirmUninstall(false);
        setUninstallError(null);
        const syncMsg = `${msg} 已同步刷新当前状态（本地素材包目录已移除）。`;
        setError(syncMsg);
        onNotice?.(syncMsg, 'error');
      } else {
        setUninstallError(msg);
        onNotice?.(msg, 'error');
      }
    } finally {
      setUninstalling(false);
    }
  };

  const statusTone = !status
    ? ''
    : !status.installed
      ? ''
      : status.complete
        ? 'is-ok'
        : 'is-warn';

  const statusLabel = !status
    ? (relocating ? '待重启后读取' : '读取中…')
    : !status.installed
      ? '未安装'
      : status.complete
        ? `已完整安装（${status.present_count}/${status.required_count}）`
        : `缺失（${status.present_count}/${status.required_count}）`;

  return (
    <section className="ws2-m-sec ws2-pack-sec" ref={anchor} aria-label="原版素材包">
      <div className="ws2-m-group">
        <span>原版素材包</span>
        <span className="aside">OPTIONAL PACK</span>
      </div>

      <p className="ws2-m-note">
        Amadeus 不自带任何原版立绘或原声音乐。请使用你个人合法持有的素材目录或 .zip 压缩包在此检查并安装；安装或卸载后立即生效，无需重启应用。
      </p>

      {relocating ? (
        <p className="ws2-m-note is-bad" role="status" data-testid="pack-relocating-note">数据已迁移至新位置，重启后才能管理素材包。</p>
      ) : null}

      {loadError ? (
        <p className="ws2-m-flag is-bad" role="alert">
          {loadError}
          <button
            type="button"
            className="ws2-m-link"
            style={{ marginLeft: 12 }}
            disabled={relocating}
            onClick={() => { void loadStatus(); }}
          >
            重试
          </button>
        </p>
      ) : (
        <>
          <div className="ws2-m-row">
            <div className="ws2-m-label">
              <b>当前状态</b>
              <small className="ws2-pack-badge-wrap">
                <span className={`ws2-pack-badge ${statusTone}`}>{statusLabel}</span>
              </small>
            </div>
            <div className="ws2-m-acts">
              {status?.installed && (
                <button
                  type="button"
                  className="ws2-m-link is-danger"
                  disabled={busy || !movable || relocating}
                  onClick={() => {
                    setUninstallError(null);
                    setConfirmUninstall(true);
                  }}
                >
                  卸载素材包
                </button>
              )}
            </div>
          </div>

          {status && (
            <dl className="ws2-pack-meta">
              <div>
                <dt>名称 / 版本</dt>
                <dd>
                  {status.installed
                    ? `${status.name || '未命名素材包'}${status.version ? ` · ${formatVersion(status.version)}` : ''}`
                    : '—'}
                </dd>
              </div>
              <div>
                <dt>占用体积</dt>
                <dd>{status.installed ? formatBytes(status.size_bytes) : '—'}</dd>
              </div>
              <div>
                <dt>目录路径</dt>
                <dd className="ws2-path">{status.path || '—'}</dd>
              </div>
            </dl>
          )}

          {status && !status.movable && (
            <p className="ws2-m-flag" data-testid="asset-pack-env-lock">
              当前通过环境变量 <code>AMADEUS_ASSET_PACK_DIR</code> 固定素材目录，不支持从界面安装、覆盖或卸载。
            </p>
          )}

          {status?.installed && !status.complete && status.missing_files.length > 0 && (
            <details className="ws2-pack-missing">
              <summary>当前已安装目录缺少 {status.missing_files.length} 个文件</summary>
              <ul>
                {status.missing_files.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      <form className="ws2-pack-form" onSubmit={(e) => { void handleInspect(e); }}>
        <div
          className={`ws2-pack-drop${dragOver ? ' is-over' : ''}${!movable || relocating ? ' is-disabled' : ''}`}
          data-testid="asset-pack-dropzone"
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy && movable && !relocating) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <div className="ws2-pack-drop-copy">
            <strong>拖入 .zip 压缩包，或选择本地目录 / 文件</strong>
            <small>支持浏览器直接上传 .zip，或填写本机素材目录绝对路径</small>
          </div>
          <div className="ws2-m-acts">
            {inShell && (
              <>
                <button
                  type="button"
                  className="ws2-m-btn"
                  disabled={busy || !movable || relocating}
                  aria-busy={picking || undefined}
                  onClick={() => { void choosePickedPath('directory'); }}
                >
                  选择目录
                </button>
                <button
                  type="button"
                  className="ws2-m-btn"
                  disabled={busy || !movable || relocating}
                  aria-busy={picking || undefined}
                  onClick={() => { void choosePickedPath('zip'); }}
                >
                  选择 ZIP 包
                </button>
              </>
            )}
            <button
              type="button"
              className="ws2-m-btn"
              disabled={busy || !movable || relocating}
              onClick={() => fileInputRef.current?.click()}
            >
              上传 ZIP 文件
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              className="ws2-pack-file-input"
              aria-label="选择本地 .zip 素材包"
              data-testid="asset-pack-file-input"
              disabled={busy || !movable || relocating}
              onChange={handleFileChange}
            />
          </div>
        </div>

        {selectedFile && (
          <div className="ws2-pack-file-pill" data-testid="asset-pack-selected-file">
            <span>
              待检查压缩包：{selectedFile.name} ({formatBytes(selectedFile.size)})
            </span>
            <button
              type="button"
              className="ws2-m-link"
              disabled={busy || relocating}
              onClick={() => {
                setSelectedFile(null);
                setInspectState(null);
              }}
            >
              清除文件
            </button>
          </div>
        )}

        <div className="ws2-pack-input-row">
          <label className="ws2-pack-input-wrap">
            <span>或填写素材目录 / .zip 绝对路径</span>
            <input
              type="text"
              className="ws2-pack-input"
              value={sourcePath}
              disabled={busy || !movable || relocating}
              placeholder="/path/to/amadeus-asset-pack 或 .zip 文件路径"
              onChange={(e) => {
                setSelectedFile(null);
                setSourcePath(e.target.value);
                setInspectState(null);
                setError(null);
                setDone(null);
              }}
            />
          </label>
          <button
            type="submit"
            className="ws2-m-btn"
            disabled={busy || !movable || relocating || !activeSource}
          >
            {inspecting ? '检查中…' : '检查素材'}
          </button>
        </div>
      </form>

      {error && (
        <p className="ws2-m-error" role="alert">
          {error}
        </p>
      )}
      {done && (
        <p className="ws2-m-note is-done" role="status">
          {done}
        </p>
      )}

      {inspectState && (
        <div className="ws2-pack-preview" data-testid="asset-pack-inspect-preview">
          <div className="ws2-pack-preview-head">
            <h4>
              检查结果：{inspectState.result.name || inspectState.label}
              {inspectState.result.version ? ` (${formatVersion(inspectState.result.version) || inspectState.result.version})` : ''}
            </h4>
            <span className={`ws2-pack-badge ${inspectState.result.valid ? 'is-ok' : 'is-warn'}`}>
              {inspectState.result.valid ? '校验通过' : '存在阻塞问题'}
            </span>
          </div>

          <div className="ws2-pack-preview-stats">
            <span>来源：{inspectState.result.source_type === 'zip' ? 'ZIP 压缩包' : '本地目录'}</span>
            <span>
              覆盖度：{inspectState.result.found_count} / {inspectState.result.required_count}
            </span>
            <span>体积：{formatBytes(inspectState.result.total_bytes)}</span>
            {inspectState.result.stripped_root && (
              <span>已识别外层目录：{inspectState.result.stripped_root}</span>
            )}
          </div>

          {inspectState.result.previews.length > 0 && (
            <div className="ws2-pack-previews" data-testid="asset-pack-previews">
              {inspectState.result.previews.map((item) => (
                <figure key={item.emotion} className="ws2-pack-preview-card">
                  <img src={item.data_url} alt={EMOTION_LABELS[item.emotion] ?? item.emotion} />
                  <figcaption title={item.path}>{EMOTION_LABELS[item.emotion] ?? item.emotion}</figcaption>
                </figure>
              ))}
            </div>
          )}

          {inspectState.result.errors.length > 0 && (
            <ul className="ws2-pack-issues is-bad" aria-label="检查错误">
              {inspectState.result.errors.map((item, idx) => (
                <li key={`${idx}-${item}`}>{item}</li>
              ))}
            </ul>
          )}

          {inspectState.result.invalid_files.length > 0 && (
            <ul className="ws2-pack-issues is-bad" aria-label="异常文件">
              {inspectState.result.invalid_files.map((item) => (
                <li key={item.path}>
                  <code>{item.path}</code>：{item.reason}
                </li>
              ))}
            </ul>
          )}

          {inspectState.result.warnings.length > 0 && (
            <ul className="ws2-pack-issues is-warn" aria-label="检查警告">
              {inspectState.result.warnings.map((item, idx) => (
                <li key={`${idx}-${item}`}>{item}</li>
              ))}
            </ul>
          )}

          {inspectState.result.missing_files.length > 0 && (
            <details className="ws2-pack-missing" open>
              <summary>缺失文件（{inspectState.result.missing_files.length} 项）</summary>
              <ul>
                {inspectState.result.missing_files.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </details>
          )}

          <div className="ws2-pack-preview-foot">
            <button
              type="button"
              className="ws2-m-btn"
              disabled={busy || !movable || relocating || !inspectState.result.valid}
              onClick={() => { void handleInstall(); }}
            >
              {installing ? '安装中…' : '确认安装素材包'}
            </button>
          </div>
        </div>
      )}

      {confirmUninstall && (
        <OnSheet anchor={anchor}>
          <ConfirmDialog
            title="卸载原版素材包"
            stamp={<Stamp>UNINSTALL</Stamp>}
            confirmLabel={uninstalling ? '卸载中…' : '确认卸载'}
            danger
            busy={uninstalling}
            error={uninstallError}
            onConfirm={() => { void handleConfirmUninstall(); }}
            onCancel={() => {
              if (!uninstalling) setConfirmUninstall(false);
            }}
          >
            <p>
              卸载后会移除已复制到本地数据目录的原版立绘与原声音乐，舞台与音频会立即恢复为无素材包状态。之后可随时重新导入。
            </p>
          </ConfirmDialog>
        </OnSheet>
      )}
    </section>
  );
}
