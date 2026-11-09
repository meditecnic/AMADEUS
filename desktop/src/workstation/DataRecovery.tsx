import { useState } from 'react';
import { Atmosphere } from './Atmosphere';
import { ConfirmDialog } from './ConfirmDialog';
import { errorText } from './DataPanel';
import { diagnosticsBundle, formatBytes, formatStamp, formatVersion, restoreBackup, type Backup, type DataStatus } from './dataApi';
import { inShell, ISSUES_URL, openExternal, revealInFolder } from './shell';
import { Stamp } from './Stamp';

/*
 * 数据异常 (amadeus-ops design/20261108-ui-inventory.md §2.12): the database failed its check at startup, so the
 * backend answers nothing but its data routes. Instead of a login that cannot work, the sheet shows a fault report:
 * what happened, a backup to restore to at the next start, a bundle to ask for help with, and the detail.
 */

const REASON: Record<string, string> = { manual: '手动', weekly: '每周', 'pre-upgrade': '升级前', 'pre-restore': '恢复前' };

export function DataRecovery({ status, worldline }: { status: DataStatus; worldline: 'steins_gate' | 'beta' }) {
  const restorable = status.backups.filter((row) => row.can_restore);
  const [asking, setAsking] = useState<Backup | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scheduled, setScheduled] = useState<Backup | null>(null);
  const [bundle, setBundle] = useState<{ path: string; size: number } | null>(null);
  const [bundling, setBundling] = useState(false);
  const [helpError, setHelpError] = useState<string | null>(null);

  const restore = async () => {
    if (!asking) return;
    setBusy(true);
    setError(null);
    try {
      await restoreBackup(asking.id);
      setScheduled(asking);
      setAsking(null);
    } catch (failure) {
      setError(errorText(failure, '没能安排恢复。'));
    } finally {
      setBusy(false);
    }
  };
  const makeBundle = async () => {
    setBundling(true);
    setHelpError(null);
    try {
      setBundle(await diagnosticsBundle());
    } catch (failure) {
      setHelpError(errorText(failure, '反馈包没有生成。'));
    } finally {
      setBundling(false);
    }
  };
  const shell = (act: () => Promise<unknown>) => { setHelpError(null); act().catch(() => setHelpError('没能打开。')); };

  return (
    <div className="ws2 ws2-recovery" data-wl={worldline === 'beta' ? 'beta' : 'sg'}>
      <Atmosphere worldline={worldline} still />
      <div className="ws2-recovery-scroll">
        <article className="ws2-recovery-sheet" aria-labelledby="ws2-recovery-title">
          <Stamp sub="DATA FAULT" tilt={-6}>暂停</Stamp>
          <header>
            <span className="fig">FIG. 0 — DATA FAULT</span>
            <h1 id="ws2-recovery-title">数据异常</h1>
          </header>
          <p className="lead">打开数据库时，检查没有通过。为了不让数据坏得更多，AMADEUS 先停在这里，不进入对话。</p>
          <p className="ws2-m-note">原来的数据文件还在原处，没有被删除。</p>

          <section className="ws2-m-sec" aria-labelledby="ws2-recovery-backups">
            <div className="ws2-m-group" id="ws2-recovery-backups">恢复到一份备份</div>
            {scheduled ? (
              <p className="ws2-m-flag" role="status">
                已安排恢复到 {formatStamp(scheduled.time)} 的备份。关掉 AMADEUS 再打开，就会换上这份。
              </p>
            ) : null}
            {restorable.length ? (
              <ol className="ws2-backups" aria-label="能用来恢复的备份">
                {restorable.map((row) => (
                  <li key={row.id} className={scheduled?.id === row.id ? 'is-pending' : undefined}>
                    <span className="meta">
                      <span className="when">{formatStamp(row.time)}</span>
                      <span className="why">{REASON[row.reason] ?? row.reason}</span>
                      <span className="size">{formatBytes(row.size)}</span>
                      <span className="ver">{formatVersion(row.app_version)}</span>
                    </span>
                    <span className="acts">
                      {scheduled?.id === row.id ? <span className="tag">待恢复</span> : (
                        <button type="button" className="ws2-m-link" onClick={() => { setError(null); setAsking(row); }}>恢复</button>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="ws2-m-flag is-bad">没有能用来恢复的备份。生成反馈包，到问题页求助。</p>
            )}
            <p className="ws2-m-note">恢复前，会把现在还读得出的数据另备一份（恢复前）；备份之后的对话和记忆不会保留。</p>
          </section>

          <section className="ws2-m-sec" aria-labelledby="ws2-recovery-help">
            <div className="ws2-m-group" id="ws2-recovery-help">求助</div>
            <p className="ws2-m-flag is-bad">不要在问题页贴密钥和对话内容。</p>
            <p className="ws2-m-note">反馈包只有日志和一份摘要，密钥已去掉；不含对话、记忆和音频。它只存在这台电脑上。</p>
            {bundle ? (
              <div className="ws2-m-row ws2-m-path">
                <div className="ws2-m-label">
                  <b><span className="ws2-path">{bundle.path.split(/[\\/]/).pop()}</span></b>
                  <small>{formatBytes(bundle.size)} · 在数据目录的 diagnostics 文件夹里</small>
                </div>
                {inShell ? (
                  <span className="ws2-m-acts">
                    <button type="button" className="ws2-m-btn" onClick={() => shell(() => revealInFolder(bundle.path))}>打开所在文件夹</button>
                  </span>
                ) : null}
              </div>
            ) : null}
            <div className="ws2-m-foot">
              {helpError ? <p className="ws2-m-note is-bad" role="alert">{helpError}</p> : null}
              <span className="ws2-m-acts">
                {bundle ? null : (
                  <button type="button" className="ws2-m-link" onClick={() => { void makeBundle(); }} disabled={bundling}>{bundling ? '生成中…' : '生成反馈包'}</button>
                )}
                <button type="button" className="ws2-m-btn" onClick={() => shell(() => openExternal(ISSUES_URL))}>去问题页</button>
              </span>
            </div>
          </section>

          {status.detail ? (
            <details className="ws2-recovery-detail">
              <summary>技术细节</summary>
              <code>{status.detail}</code>
            </details>
          ) : null}
        </article>
      </div>
      {asking ? (
        <ConfirmDialog
          title="恢复到这份备份？"
          confirmLabel="安排恢复"
          busy={busy}
          error={error}
          onConfirm={() => { void restore(); }}
          onCancel={() => setAsking(null)}
        >
          <p>{formatStamp(asking.time)} · {REASON[asking.reason] ?? asking.reason} · {formatBytes(asking.size)}</p>
          <p>关掉 AMADEUS 再打开时，数据会换成这份备份。</p>
          <p className="ws2-dialog-hint">换之前会把现在还读得出的数据另备一份（恢复前）。</p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
