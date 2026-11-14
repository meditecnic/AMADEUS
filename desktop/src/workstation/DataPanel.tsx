import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Choice } from './Choice';
import { ConfirmDialog } from './ConfirmDialog';
import { Stamp } from './Stamp';
import {
  ApiError, authState, checkDataLocation, createBackup, dataLocation, dataStatus, deleteBackup, deletePreviousDataLocation,
  diskUsage, exportArchive, formatBytes, formatStamp, formatVersion, moveDataLocation,
  ackRestoreResult, cancelRestore, restoreBackup, setPasscode,
  type AuthState, type Backup, type DataLocation, type DataLocationCheckResult, type DataLocationIssue,
  type DiskCategory, type DiskUsage, type ExportRequest,
} from './dataApi';
import { inShell, pickPath, restartApp, revealInFolder, saveBlob } from './shell';

/*
 * 05 数据: the startup passcode, backups (make one, restore to one, delete one), export, and where the data lives with
 * what takes the room. Each is a group of ruled rows on the system page; its acts are outline plates, and the one
 * that cannot be undone (deleting a backup) is confirmed on a stamped slip.
 */

const REASON: Record<string, string> = { manual: '手动', weekly: '每周', 'pre-upgrade': '升级前', 'pre-restore': '恢复前' };
/** A backup's id starts with its UTC time (20261108T224512…Z), which still dates one whose manifest cannot be read. */
const idTime = (id: string) => {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(id);
  return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z` : null;
};

const ERRORS: Record<string, string> = {
  unreachable: '连不上本地服务。',
  invalid_passcode: '现在的口令不对。',
  empty_passcode: '口令不能是空白。',
  passcode_write_failed: '口令文件写不进去。',
  conversation_active: '她正在回话。等这一句说完再备份。',
  backup_busy: '数据库正忙，过一会儿再试。',
  insufficient_space: '磁盘空间不够，备份没有开始。',
  backup_failed: '备份没有完成。',
  backup_stopped: '备份被中断了。',
  restore_pending: '有一次恢复还没做完。先关掉 AMADEUS 再打开。',
  backup_not_found: '这份备份已经不在了。',
  invalid_backup: '这份备份不完整，不能用来恢复。',
  data_io_failed: '读写数据目录失败。',
  invalid_export_scope: '这种导出不支持。',
  export_unavailable: '现在导不出来。',
  data_unavailable: '数据库现在打不开。',
  data_relocating: '数据已迁移至新位置，需立即重启 AMADEUS 完成切换。',
};
export const errorText = (error: unknown, fallback: string) => {
  const code = error instanceof ApiError ? error.code : null;
  return code ? ERRORS[code] ?? `${fallback}（${code}）` : fallback;
};

export type DataRelocationState = {
  path?: string;
  restartNote?: string;
};

export function isDataRelocatingFailure(failure: unknown): boolean {
  return failure instanceof ApiError && failure.code === 'data_relocating';
}

function defaultRestartNote(inDesktopShell: boolean): string {
  return inDesktopShell
    ? '请点击「立即重启」切换到新目录；如未能自动重启，请手动关闭并重新打开 AMADEUS。'
    : '当前不在桌面端外壳中，无法自动重启；请手动重启本地服务并刷新页面以切换到新目录。';
}

/** Persistent banner rendered once relocation completes and awaits an app restart. */
export function RelocationRestartBanner({
  state,
  onUpdate,
}: {
  state: DataRelocationState;
  onUpdate?: (next: DataRelocationState) => void;
}) {
  const [restarting, setRestarting] = useState(false);

  const triggerRestart = async () => {
    if (restarting) return;
    if (!inShell) {
      onUpdate?.({
        ...state,
        restartNote: defaultRestartNote(false),
      });
      return;
    }
    setRestarting(true);
    onUpdate?.({
      ...state,
      restartNote: '正在尝试重启 AMADEUS 以切换到新目录…',
    });
    try {
      await restartApp();
    } catch {
      onUpdate?.({
        ...state,
        restartNote: '自动重启失败，请点击「立即重启」重试，或手动关闭并重新打开 AMADEUS。',
      });
    } finally {
      setRestarting(false);
    }
  };

  return (
    <div className="ws2-loc-restart" role="status" data-testid="location-restart-required">
      <div className="ws2-loc-restart-copy">
        <strong>
          {state.path ? (
            <>
              数据已迁移到 <span className="ws2-path">{state.path}</span>，需要重启 AMADEUS 才能生效（相关操作已锁定）。
            </>
          ) : (
            '数据已迁移至新位置，需要重启 AMADEUS 才能生效（相关操作已锁定）。'
          )}
        </strong>
        <p className="ws2-m-note">{state.restartNote || defaultRestartNote(inShell)}</p>
      </div>
      <button
        type="button"
        className="ws2-m-btn"
        onClick={() => { void triggerRestart(); }}
        disabled={restarting}
      >
        {restarting ? '正在重启…' : '立即重启'}
      </button>
    </div>
  );
}

/** Dialogs belong on the sheet, over everything, not inside the scrolling form. */
function OnSheet({ anchor, children }: { anchor: React.RefObject<HTMLElement | null>; children: ReactNode }) {
  const root = anchor.current?.closest<HTMLElement>('.ws2');
  return root ? createPortal(children, root) : <>{children}</>;
}

export function DataPanel({
  relocation,
  onRelocationChange,
}: {
  relocation?: DataRelocationState | null;
  onRelocationChange?: (next: DataRelocationState) => void;
} = {}) {
  const anchor = useRef<HTMLDivElement>(null);
  const controlled = Boolean(onRelocationChange || relocation !== undefined);
  const [localRelocation, setLocalRelocation] = useState<DataRelocationState | null>(null);
  const activeRelocation = relocation !== undefined ? relocation : localRelocation;
  const reportRelocation = (next: DataRelocationState) => {
    if (onRelocationChange) onRelocationChange(next);
    else setLocalRelocation(next);
  };
  const flagRelocating = (path?: string) => {
    reportRelocation({
      ...(activeRelocation ?? {}),
      ...(path && !activeRelocation?.path ? { path } : {}),
      restartNote: activeRelocation?.restartNote ?? '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
    });
  };
  const relocated = Boolean(activeRelocation);
  return (
    <div className="ws2-data" ref={anchor}>
      <PasscodeGroup relocated={relocated} onRelocating={() => flagRelocating()} />
      <BackupGroup anchor={anchor} relocated={relocated} onRelocating={() => flagRelocating()} />
      <ExportGroup relocated={relocated} onRelocating={() => flagRelocating()} />
      <LocationGroup
        anchor={anchor}
        relocation={activeRelocation}
        onRelocate={reportRelocation}
        renderBannerInline={!controlled}
      />
    </div>
  );
}

/* ---------- the startup passcode ---------- */

type PasscodeMode = 'view' | 'set' | 'change' | 'off';

function PasscodeGroup({ relocated, onRelocating }: { relocated: boolean; onRelocating: () => void }) {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<PasscodeMode>('view');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const firstField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (relocated) return;
    let live = true;
    authState().then(
      (state) => { if (live) setAuth(state); },
      (failure) => {
        if (!live) return;
        if (isDataRelocatingFailure(failure)) { onRelocating(); return; }
        setLoadError(errorText(failure, '读不到口令的状态。'));
      },
    );
    return () => { live = false; };
  }, [relocated]);
  useEffect(() => { if (mode !== 'view') firstField.current?.focus({ preventScroll: true }); }, [mode]);

  const open = (to: PasscodeMode) => { setMode(to); setCurrent(''); setNext(''); setAgain(''); setError(null); setDone(null); };
  const set = Boolean(auth?.passcode_set);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || relocated) return;
    if (mode !== 'off') {
      if (!next.trim()) { setError('口令不能是空白。'); return; }
      if (next !== again) { setError('两次输入的不一样。'); return; }
    }
    setBusy(true);
    setError(null);
    try {
      await setPasscode(set ? current : null, mode === 'off' ? null : next);
      setAuth({ passcode_set: mode !== 'off' });
      setDone(mode === 'set' ? '口令已设好，下次打开时会问。' : mode === 'change' ? '口令已改好。' : '口令已关掉，下次打开不再问。');
      setMode('view');
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) onRelocating();
      setError(errorText(failure, '口令没有改成。'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ws2-m-sec" aria-labelledby="ws2-data-passcode">
      <div className="ws2-m-group" id="ws2-data-passcode">启动口令</div>
      <div className="ws2-m-row">
        <div className="ws2-m-label">
          <b>打开时先问口令</b>
          {loadError ? <small className="is-bad">{loadError}</small>
            : auth?.passcode_error ? <small className="is-bad">口令文件读不出来，现在打开时不会问口令；重新设一个即可。</small>
              : done ? <small className="is-done">{done}</small>
                : <small>{auth ? (set ? '已设口令' : '没有口令，打开即进') : relocated ? '数据已迁移，重启 AMADEUS 后可查看或修改。' : '读取中…'}</small>}
        </div>
        {auth && mode === 'view' ? (
          <span className="ws2-m-acts">
            {set ? <button type="button" className="ws2-m-link" onClick={() => open('off')} disabled={relocated}>关掉口令</button> : null}
            <button type="button" className="ws2-m-btn" onClick={() => open(set ? 'change' : 'set')} disabled={relocated}>{set ? '改口令' : '设口令'}</button>
          </span>
        ) : null}
      </div>
      {mode !== 'view' ? (
        <form className="ws2-m-form" onSubmit={submit} aria-label={mode === 'off' ? '关掉口令' : mode === 'change' ? '改口令' : '设口令'}>
          {set ? (
            <label className="ws2-m-field">
              <span>现在的口令</span>
              <input ref={firstField} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} disabled={busy || relocated} />
            </label>
          ) : null}
          {mode !== 'off' ? (
            <>
              <label className="ws2-m-field">
                <span>新口令</span>
                <input ref={set ? undefined : firstField} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} disabled={busy || relocated} />
              </label>
              <label className="ws2-m-field">
                <span>再输一次</span>
                <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} disabled={busy || relocated} />
              </label>
            </>
          ) : null}
          <div className="ws2-m-form-foot">
            {error ? <p className="ws2-m-error" role="alert">{error}</p> : <span />}
            <span className="ws2-m-acts">
              <button type="button" className="ws2-m-link" onClick={() => setMode('view')} disabled={busy}>取消</button>
              <button type="submit" className="ws2-m-btn" disabled={busy || relocated}>{busy ? '保存中…' : mode === 'off' ? '关掉口令' : '保存口令'}</button>
            </span>
          </div>
        </form>
      ) : null}
      <p className="ws2-m-note">口令只在打开 AMADEUS 时问一次，不加密数据。忘了口令：关掉 AMADEUS，删掉数据目录里的 auth\passcode.json 即可。</p>
    </section>
  );
}

/* ---------- backups ---------- */

function BackupGroup({ anchor, relocated, onRelocating }: { anchor: React.RefObject<HTMLElement | null>; relocated: boolean; onRelocating: () => void }) {
  const [backups, setBackups] = useState<Backup[] | null>(null);
  const [restoreFailed, setRestoreFailed] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [scheduled, setScheduled] = useState<Backup | null>(null);
  const [asking, setAsking] = useState<{ kind: 'restore' | 'delete'; backup: Backup } | null>(null);
  const [busy, setBusy] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  useEffect(() => {
    if (relocated) return;
    let live = true;
    dataStatus().then((status) => {
      if (!live) return;
      setBackups(status.backups);
      if (status.status === 'restore_failed') setRestoreFailed(status.detail ?? '');
      // A restore scheduled earlier (before this page was opened, or before a reload) is still waiting.
      setScheduled(status.backups.find((row) => row.id === status.restore_pending) ?? null);
    }, (failure) => {
      if (!live) return;
      if (isDataRelocatingFailure(failure)) { onRelocating(); return; }
      setLoadError(errorText(failure, '读不到备份。'));
    });
    return () => { live = false; };
  }, [relocated]);

  const make = async () => {
    if (making || relocated) return;
    setMaking(true);
    setNote(null);
    try {
      const made = await createBackup();
      const status = await dataStatus();
      setBackups(status.backups);
      setNote({ text: `已备份（${formatStamp(status.backups.find((row) => row.id === made.id)?.time ?? null)}）。` });
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) onRelocating();
      setNote({ text: errorText(failure, '备份没有完成。'), bad: true });
    } finally {
      setMaking(false);
    }
  };

  const answer = async () => {
    if (!asking || relocated) return;
    setBusy(true);
    setAskError(null);
    try {
      if (asking.kind === 'restore') {
        await restoreBackup(asking.backup.id);
        setScheduled(asking.backup);
      } else {
        await deleteBackup(asking.backup.id);
        setBackups((rows) => rows?.filter((row) => row.id !== asking.backup.id) ?? rows);
      }
      setAsking(null);
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) onRelocating();
      setAskError(errorText(failure, asking.kind === 'restore' ? '没能安排恢复。' : '没能删除。'));
    } finally {
      setBusy(false);
    }
  };

  const [calling, setCalling] = useState(false);
  const callOff = async () => {
    if (calling || relocated) return;
    setCalling(true);
    setNote(null);
    try {
      await cancelRestore();
      setScheduled(null);
      setNote({ text: '已取消恢复，数据保持现在的样子。' });
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) onRelocating();
      // restore_not_pending: it was already called off, or has already run.
      if (failure instanceof ApiError && failure.code === 'restore_not_pending') setScheduled(null);
      setNote({ text: errorText(failure, '没能取消。'), bad: true });
    } finally {
      setCalling(false);
    }
  };
  const readFailure = () => {
    void ackRestoreResult().catch(() => undefined);
    setRestoreFailed(null);
  };

  const total = backups?.reduce((sum, row) => sum + row.size, 0) ?? 0;
  return (
    <section className="ws2-m-sec" aria-labelledby="ws2-data-backups">
      <div className="ws2-m-group" id="ws2-data-backups">
        备份
        {backups?.length ? <span className="aside">{backups.length} 份 · {formatBytes(total)}</span> : null}
      </div>
      {restoreFailed !== null ? (
        <p className="ws2-m-flag is-bad" role="status">
          上次的恢复没有做完，数据还是恢复前的样子。
          {restoreFailed ? <code>{restoreFailed}</code> : null}
          <button type="button" className="ws2-m-link" onClick={readFailure}>知道了</button>
        </p>
      ) : null}
      {scheduled ? (
        <p className="ws2-m-flag" role="status">
          已安排：下次打开 AMADEUS 时，数据换成 {formatStamp(scheduled.time)} 的备份。现在关掉再打开即可。
          <button type="button" className="ws2-m-link" onClick={() => { void callOff(); }} disabled={calling || relocated}>{calling ? '取消中…' : '取消恢复'}</button>
        </p>
      ) : null}
      <div className="ws2-m-row">
        <div className="ws2-m-label">
          <b>现在备份一份</b>
          {note ? <small className={note.bad ? 'is-bad' : 'is-done'} role="status">{note.text}</small>
            : <small>手动的备份不会被自动删除</small>}
        </div>
        <span className="ws2-m-acts">
          <button type="button" className="ws2-m-btn" onClick={() => { void make(); }} disabled={making || !backups || relocated}>{making ? '备份中…' : '立即备份'}</button>
        </span>
      </div>
      {loadError ? <p className="ws2-m-note is-bad">{loadError}</p> : null}
      {!backups && relocated && !loadError ? <p className="ws2-m-note">数据已迁移，重启 AMADEUS 后可查看与操作备份。</p> : null}
      {backups && backups.length === 0 ? <p className="ws2-m-note">还没有备份。</p> : null}
      {backups && backups.length > 0 ? (
        <ol className="ws2-backups" aria-label="备份">
          {backups.map((row) => {
            const pending = scheduled?.id === row.id;
            return (
              <li key={row.id} className={`${row.can_restore ? '' : 'is-broken'}${pending ? ' is-pending' : ''}`}>
                <span className="meta">
                  <span className="when">{formatStamp(row.time ?? idTime(row.id))}</span>
                  <span className="why">{REASON[row.reason] ?? row.reason}</span>
                  <span className="size">{row.can_restore ? formatBytes(row.size) : '—'}</span>
                  <span className="ver">{formatVersion(row.app_version)}</span>
                </span>
                <span className="acts">
                  {pending ? <span className="tag">待恢复</span>
                    : row.can_restore ? (
                      <button
                        type="button"
                        className="ws2-m-link"
                        aria-label={`恢复到 ${formatStamp(row.time)} 的备份`}
                        onClick={() => { setAskError(null); setAsking({ kind: 'restore', backup: row }); }}
                        disabled={relocated}
                      >
                        恢复
                      </button>
                    ) : <span className="tag">不完整</span>}
                  <button
                    type="button"
                    className="ws2-m-link is-danger"
                    aria-label={`删除 ${formatStamp(row.time ?? idTime(row.id))} 的备份`}
                    onClick={() => { setAskError(null); setAsking({ kind: 'delete', backup: row }); }}
                    disabled={pending || relocated}
                  >
                    删除
                  </button>
                </span>
                {!row.can_restore && row.detail ? <code className="detail">{row.detail}</code> : null}
              </li>
            );
          })}
        </ol>
      ) : null}
      <p className="ws2-m-note">每周自动备份一次，升级前、恢复前也各备一份；自动的各留最近三份。</p>
      {asking ? (
        <OnSheet anchor={anchor}>
          {asking.kind === 'restore' ? (
            <ConfirmDialog
              title="恢复到这份备份？"
              confirmLabel="安排恢复"
              busy={busy}
              error={askError}
              onConfirm={() => { void answer(); }}
              onCancel={() => setAsking(null)}
            >
              <p>{formatStamp(asking.backup.time)} · {REASON[asking.backup.reason] ?? asking.backup.reason} · {formatBytes(asking.backup.size)}</p>
              <p>下次打开 AMADEUS 时，数据会换成这份备份，之后的对话和记忆不再保留。</p>
              <p className="ws2-dialog-hint">换之前会把现在的数据另备一份（恢复前），需要时可以再恢复回来。</p>
            </ConfirmDialog>
          ) : (
            <ConfirmDialog
              title="删除这份备份？"
              confirmLabel="删除"
              danger
              stamp={<Stamp sub={formatStamp(asking.backup.time ?? idTime(asking.backup.id)).slice(5, 10)}>不可撤回</Stamp>}
              busy={busy}
              error={askError}
              onConfirm={() => { void answer(); }}
              onCancel={() => setAsking(null)}
            >
              <p>{formatStamp(asking.backup.time ?? idTime(asking.backup.id))} · {REASON[asking.backup.reason] ?? asking.backup.reason}{asking.backup.can_restore ? ` · ${formatBytes(asking.backup.size)}` : ' · 不完整'}</p>
              <p>{asking.backup.can_restore ? '删除后，就不能再恢复到这一刻。' : '这份备份本来就不能用来恢复，删掉只是腾出地方。'}</p>
            </ConfirmDialog>
          )}
        </OnSheet>
      ) : null}
    </section>
  );
}

/* ---------- export ---------- */

type Worldlines = ExportRequest['worldline'];

function ExportGroup({ relocated, onRelocating }: { relocated: boolean; onRelocating: () => void }) {
  const [kind, setKind] = useState<'conversations' | 'memory'>('conversations');
  const [format, setFormat] = useState<'md' | 'json'>('md');
  const [worldline, setWorldline] = useState<Worldlines>('all');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  const run = async () => {
    if (busy || relocated) return;
    setBusy(true);
    setNote(null);
    try {
      const request: ExportRequest = kind === 'memory' ? { kind, format: 'json', worldline } : { kind, format, worldline };
      const { blob, filename } = await exportArchive(request);
      saveBlob(blob, filename);
      setNote({ text: `已导出 ${filename}` });
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) onRelocating();
      setNote({ text: errorText(failure, '没有导出来。'), bad: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ws2-m-sec" aria-labelledby="ws2-data-export">
      <div className="ws2-m-group" id="ws2-data-export">导出</div>
      <div className="ws2-m-row is-choice">
        <div className="ws2-m-label"><b>内容</b></div>
        <Choice label="导出内容" value={kind} onChange={(value) => { setKind(value); setNote(null); }} options={[
          { value: 'conversations', label: '对话' }, { value: 'memory', label: '记忆' },
        ]} />
      </div>
      <div className="ws2-m-row is-choice">
        <div className="ws2-m-label"><b>格式</b>{kind === 'memory' ? <small>记忆只导出 JSON</small> : null}</div>
        <Choice label="导出格式" value={kind === 'memory' ? 'json' : format} onChange={setFormat} options={[
          { value: 'md', label: 'Markdown', disabled: kind === 'memory' }, { value: 'json', label: 'JSON' },
        ]} />
      </div>
      <div className="ws2-m-row is-choice">
        <div className="ws2-m-label"><b>世界线</b></div>
        <Choice label="导出哪条世界线" value={worldline} onChange={setWorldline} options={[
          { value: 'all', label: '两条都要' }, { value: 'sg', label: 'SG 线' }, { value: 'beta', label: 'β 线' },
        ]} />
      </div>
      <div className="ws2-m-foot">
        {note ? <p className={`ws2-m-note ${note.bad ? 'is-bad' : 'is-done'}`} role="status">{note.text}</p>
          : relocated ? <p className="ws2-m-note is-bad" role="status">数据已迁移，重启 AMADEUS 后才能导出。</p>
            : <p className="ws2-m-note">{kind === 'memory' ? '导出为一个 zip 文件，里面是记忆的 JSON。' : '导出为一个 zip 文件，每个会话一份。'}</p>}
        <button type="button" className="ws2-m-btn" onClick={() => { void run(); }} disabled={busy || relocated}>{busy ? '导出中…' : '导出'}</button>
      </div>
    </section>
  );
}

/* ---------- where the data lives ---------- */

const DISK: { key: DiskCategory; label: string }[] = [
  { key: 'asset_pack', label: '素材包' },
  { key: 'voice_models', label: '语音参考' },
  { key: 'speech_models', label: '语音模型' },
  { key: 'embedding_models', label: '记忆模型' },
  { key: 'pi_components', label: 'pi 组件' },
  { key: 'backups', label: '备份' },
  { key: 'conversations_memory', label: '对话与记忆' },
  { key: 'logs', label: '日志' },
  { key: 'other', label: '其他' },
];
const SOURCE: Record<string, string> = { default: '默认位置', env: '由环境变量 AMADEUS_DATA_DIR 指定', file: '搬过的位置' };

const LOCATION_ERRORS: Record<string, string> = {
  unreachable: '连不上本地服务。',
  path_not_absolute: '请输入或选择完整的绝对路径（例如 D:\\AmadeusData）。',
  path_overlap: '目标目录不能与当前数据目录相同或互相包含。',
  system_directory: '不能放在系统目录或程序安装目录里。',
  path_link: '目标路径不能包含符号链接或目录联接。',
  target_not_empty: '目标目录必须是空目录，或还不存在的新目录。',
  path_unwritable: '目标目录不可写，请检查权限或更换位置。',
  insufficient_space: '目标磁盘可用空间不足（需要至少为当前数据总量的 1.2 倍）。',
  filesystem_fat32: 'FAT32 分区不支持超过 4 GB 的单个文件，请改用 NTFS 或 exFAT 分区。',
  sync_directory: '目标位置在同步盘（如 OneDrive / Dropbox）内，同步软件并发写入可能损坏数据库。',
  network_drive: '目标位置在网络驱动器上，网络波动可能导致数据库写入失败或损坏。',
  removable_drive: '目标位置在可移动设备（如 U 盘 / 移动硬盘）上，拔除后 AMADEUS 将无法启动。',
  location_not_movable: '当前数据位置由环境变量或外部配置指定，不能在应用内迁移。',
  data_unavailable: '当前数据库无法打开，不能进行迁移或清理。',
  warnings_not_acknowledged: '目标目录存在风险提示，请先勾选确认后再继续迁移。',
  data_relocating: '数据已迁移至新位置，需立即重启 AMADEUS 完成切换。',
  conversation_active: '她正在回话。等这一句说完再迁移数据。',
  restore_pending: '有一次恢复还没做完。先关掉 AMADEUS 再打开。',
  backup_busy: '正在进行备份，请等备份完成后再迁移。',
  move_checksum_failed: '文件校验未通过，迁移已回滚，原数据目录保持完好。',
  move_link_unsupported: '当前数据目录含有符号链接或目录联接，无法直接迁移。',
  move_cleanup_failed: '迁移失败且部分临时文件未能清理，原数据目录保持完好。',
  move_failed: '迁移没有完成，已回滚，原数据目录保持完好。',
  location_file_invalid: '数据位置配置文件损坏或无法读取。',
  previous_not_found: '旧数据目录已不存在或未记录。',
  previous_path_unsafe: '记录的旧目录路径不安全，已拒绝删除。',
  previous_unrecognized: '旧目录不像有效的 AMADEUS 数据目录，已拒绝自动删除。',
  previous_delete_failed: '旧目录没能完全删除（可能有文件正在占用），记录已保留。',
  previous_record_failed: '旧目录文件已删除，但位置记录未能更新。',
  invalid_delete_request: '删除旧目录请求参数无效。',
};

const locationErrorText = (error: unknown, fallback: string) => {
  const code = error instanceof ApiError ? error.code : null;
  return code ? LOCATION_ERRORS[code] ?? `${fallback}（${code}）` : fallback;
};

const issueText = (issue: DataLocationIssue) =>
  LOCATION_ERRORS[issue.code] ?? (issue.message ? `${issue.message}（${issue.code}）` : issue.code);

const isCheckDetail = (detail: Record<string, unknown>) =>
  Array.isArray(detail.errors) || Array.isArray(detail.warnings);

const toIssueList = (value: unknown): DataLocationIssue[] => {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is { code?: unknown; message?: unknown } => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      code: typeof item.code === 'string' ? item.code : 'unknown',
      message: typeof item.message === 'string' ? item.message : '',
    }));
};

const toCheckResult = (detail: Record<string, unknown>): DataLocationCheckResult => {
  const errors = toIssueList(detail.errors);
  const warnings = toIssueList(detail.warnings);
  return {
    ok: typeof detail.ok === 'boolean' ? detail.ok : errors.length === 0,
    errors,
    warnings,
    needed_bytes: typeof detail.needed_bytes === 'number' ? detail.needed_bytes : 0,
    free_bytes: typeof detail.free_bytes === 'number' ? detail.free_bytes : 0,
  };
};

function LocationGroup({
  anchor,
  relocation,
  onRelocate,
  renderBannerInline,
}: {
  anchor: React.RefObject<HTMLElement | null>;
  relocation: DataRelocationState | null;
  onRelocate: (next: DataRelocationState) => void;
  renderBannerInline: boolean;
}) {
  const [location, setLocation] = useState<DataLocation | null | 'absent'>(null);
  const [disk, setDisk] = useState<DiskUsage | null>(null);
  const [revealError, setRevealError] = useState<string | null>(null);
  const [previousRevealError, setPreviousRevealError] = useState<string | null>(null);
  const [targetPath, setTargetPath] = useState('');
  const [picking, setPicking] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<DataLocationCheckResult | null>(null);
  const [checkedPath, setCheckedPath] = useState<string | null>(null);
  const [ackWarnings, setAckWarnings] = useState(false);
  const [moving, setMoving] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [confirmingDeletePrevious, setConfirmingDeletePrevious] = useState(false);
  const [deletingPrevious, setDeletingPrevious] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [cleanupToast, setCleanupToast] = useState<{ text: string; bad?: boolean } | null>(null);

  const relocated = Boolean(relocation);

  useEffect(() => {
    if (relocated) return;
    let live = true;
    // A backend without these routes (before report 212) simply leaves the group out.
    dataLocation().then(
      (value) => { if (live) setLocation(value); },
      (failure) => {
        if (!live) return;
        if (isDataRelocatingFailure(failure)) {
          onRelocate({
            ...(relocation ?? {}),
            restartNote: '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
          });
          return;
        }
        setLocation('absent');
      },
    );
    diskUsage().then(
      (value) => { if (live) setDisk(value); },
      (failure) => {
        if (live && isDataRelocatingFailure(failure)) {
          onRelocate({
            ...(relocation ?? {}),
            restartNote: '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
          });
        }
      },
    );
    return () => { live = false; };
  }, [relocated]);

  if ((location === 'absent' || location === null) && !relocated) return null;

  if (location === 'absent' || location === null) {
    return (
      <section className="ws2-m-sec" aria-labelledby="ws2-data-location">
        <div className="ws2-m-group" id="ws2-data-location">数据位置</div>
        <div className="ws2-m-row ws2-m-path">
          <div className="ws2-m-label">
            <b><span className="ws2-path">{relocation?.path || '新数据目录'}</span></b>
            <small>已迁移至此位置（重启 AMADEUS 后生效）</small>
          </div>
        </div>
        <div className="ws2-loc-move" data-testid="location-relocation-form">
          <div className="ws2-loc-move-head">
            <b>迁移数据位置</b>
            <small>数据已迁移至新位置，需重启 AMADEUS 后才能继续管理目录或清理旧文件。</small>
          </div>
          <div className="ws2-loc-input-row">
            <input
              type="text"
              className="ws2-loc-input ws2-path"
              aria-label="目标数据目录"
              value={relocation?.path ?? ''}
              readOnly
              disabled
            />
            {inShell ? <button type="button" className="ws2-m-btn" disabled>选择目录</button> : null}
            <button type="button" className="ws2-m-btn" disabled>检查目标目录</button>
          </div>
          {renderBannerInline && relocation ? (
            <RelocationRestartBanner state={relocation} onUpdate={onRelocate} />
          ) : null}
          <div className="ws2-loc-actions">
            <p className="ws2-m-note">相关数据操作已锁定，请先重启 AMADEUS。</p>
            <button type="button" className="ws2-m-btn" disabled>开始迁移并重启</button>
          </div>
        </div>
        <p className="ws2-m-note">重启 AMADEUS 后再统计新位置占用。</p>
      </section>
    );
  }

  const trimmedPath = targetPath.trim();
  const moveLocked = moving || relocated;
  const currentCheck = checkResult && checkedPath === trimmedPath ? checkResult : null;
  const hasErrors = Boolean(currentCheck && (!currentCheck.ok || currentCheck.errors.length > 0));
  const hasWarnings = Boolean(currentCheck && currentCheck.warnings.length > 0);
  const canStartMove = Boolean(
    trimmedPath &&
    currentCheck &&
    !hasErrors &&
    (!hasWarnings || ackWarnings) &&
    !checking &&
    !moveLocked,
  );

  const handlePathChange = (next: string) => {
    setTargetPath(next);
    setCheckResult(null);
    setCheckedPath(null);
    setAckWarnings(false);
    setMoveError(null);
    setCleanupToast(null);
  };

  const handlePickDirectory = async () => {
    if (picking || checking || moveLocked) return;
    setPicking(true);
    setMoveError(null);
    try {
      const picked = await pickPath('directory');
      if (picked) handlePathChange(picked);
    } catch {
      setMoveError('没能打开目录选择器，请手动输入目标路径。');
    } finally {
      setPicking(false);
    }
  };

  const handleCheck = async () => {
    if (!trimmedPath || checking || moveLocked) return;
    setChecking(true);
    setMoveError(null);
    setCheckResult(null);
    setCheckedPath(null);
    setAckWarnings(false);
    setCleanupToast(null);
    try {
      const result = await checkDataLocation(trimmedPath);
      setCheckResult(result);
      setCheckedPath(trimmedPath);
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) {
        onRelocate({
          ...(relocation ?? {}),
          ...(trimmedPath && !relocation?.path ? { path: trimmedPath } : {}),
          restartNote: '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
        });
        return;
      }
      setMoveError(locationErrorText(failure, '没能检查这个目标目录。'));
    } finally {
      setChecking(false);
    }
  };

  const handleMove = async () => {
    if (!canStartMove || !trimmedPath) return;
    setMoving(true);
    setMoveError(null);
    setCleanupToast(null);
    try {
      const result = await moveDataLocation(trimmedPath, ackWarnings);
      setMoving(false);
      if (result.restart_required) {
        onRelocate({
          path: result.path,
          restartNote: inShell ? '正在尝试重启 AMADEUS 以切换到新目录…' : defaultRestartNote(false),
        });
        if (inShell) {
          try {
            await restartApp();
          } catch {
            onRelocate({
              path: result.path,
              restartNote: '自动重启失败，请点击「立即重启」重试，或手动关闭并重新打开 AMADEUS。',
            });
          }
        }
      } else {
        setCleanupToast({ text: `数据已迁移到 ${result.path}。` });
        const next = await dataLocation().catch(() => null);
        if (next) setLocation(next);
      }
    } catch (failure) {
      setMoving(false);
      if (failure instanceof ApiError && failure.detail && isCheckDetail(failure.detail)) {
        setCheckResult(toCheckResult(failure.detail));
        setCheckedPath(trimmedPath);
        if (failure.code === 'warnings_not_acknowledged') setAckWarnings(false);
        setMoveError(
          failure.code === 'warnings_not_acknowledged'
            ? LOCATION_ERRORS.warnings_not_acknowledged
            : '目标目录检查未通过，请查看下方错误后再试。',
        );
        return;
      }
      if (isDataRelocatingFailure(failure)) {
        onRelocate({
          path: relocation?.path || trimmedPath,
          restartNote: '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
        });
        return;
      }
      setMoveError(locationErrorText(failure, '数据迁移没有完成，原目录数据保持不变。'));
    }
  };

  const handleDeletePrevious = async () => {
    const oldPath = location.previous_path;
    if (!oldPath || deletingPrevious || moveLocked) return;
    setDeletingPrevious(true);
    setDeleteError(null);
    try {
      await deletePreviousDataLocation();
      setConfirmingDeletePrevious(false);
      setCleanupToast({ text: `已删除旧数据目录（${oldPath}）。` });
      try {
        const refreshed = await dataLocation();
        setLocation(refreshed);
      } catch (refreshFailure) {
        if (isDataRelocatingFailure(refreshFailure)) {
          onRelocate({
            ...(relocation ?? {}),
            restartNote: '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
          });
          return;
        }
        setLocation((prev) => (prev && prev !== 'absent' ? { ...prev, previous_path: null, previous_exists: false } : prev));
      }
    } catch (failure) {
      if (isDataRelocatingFailure(failure)) {
        setConfirmingDeletePrevious(false);
        onRelocate({
          ...(relocation ?? {}),
          restartNote: '数据迁移已在进行或已完成，需要重启 AMADEUS 才能继续使用。',
        });
        return;
      }
      setDeleteError(locationErrorText(failure, '没能删除旧数据目录。'));
    } finally {
      setDeletingPrevious(false);
    }
  };

  const rows = disk ? DISK.map((row) => ({ ...row, bytes: disk.categories[row.key] ?? 0 })).filter((row) => row.bytes > 0) : [];
  const most = Math.max(1, ...rows.map((row) => row.bytes));
  return (
    <section className="ws2-m-sec" aria-labelledby="ws2-data-location">
      <div className="ws2-m-group" id="ws2-data-location">数据位置</div>
      <div className="ws2-m-row ws2-m-path">
        <div className="ws2-m-label">
          <b><span className="ws2-path">{location.path}</span></b>
          <small className={revealError ? 'is-bad' : undefined}>{revealError ?? SOURCE[location.source] ?? location.source}</small>
        </div>
        {inShell ? (
          <span className="ws2-m-acts">
            <button type="button" className="ws2-m-btn" onClick={() => {
              setRevealError(null);
              revealInFolder(location.path).catch(() => setRevealError('没能打开这个文件夹。'));
            }}>打开文件夹</button>
          </span>
        ) : null}
      </div>

      {location.movable ? (
        <div className="ws2-loc-move" data-testid="location-relocation-form">
          <div className="ws2-loc-move-head">
            <b>迁移数据位置</b>
            <small>
              {inShell
                ? '选择或输入新的空目录绝对路径；先检查目录，迁移完成后需重启 AMADEUS。'
                : '浏览器模式下请直接输入新的空目录绝对路径；先检查目录，迁移完成后需重启生效。'}
            </small>
          </div>
          <div className="ws2-loc-input-row">
            <input
              type="text"
              className="ws2-loc-input ws2-path"
              aria-label="目标数据目录"
              placeholder={inShell ? '例如 D:\\AmadeusData，或点右侧选择目录' : '例如 D:\\AmadeusData'}
              value={targetPath}
              onChange={(e) => handlePathChange(e.target.value)}
              disabled={checking || moveLocked}
            />
            {inShell ? (
              <button
                type="button"
                className="ws2-m-link"
                onClick={() => { void handlePickDirectory(); }}
                disabled={picking || checking || moveLocked}
              >
                {picking ? '选择中…' : '选择目录'}
              </button>
            ) : null}
            <button
              type="button"
              className="ws2-m-btn"
              onClick={() => { void handleCheck(); }}
              disabled={checking || moveLocked || !trimmedPath}
            >
              {checking ? '检查中…' : '检查目标目录'}
            </button>
          </div>

          {currentCheck ? (
            <div className="ws2-loc-check" data-testid="location-check-result">
              <div className="ws2-loc-space" data-testid="location-space-summary">
                <span>预计需要空间：<b>{formatBytes(currentCheck.needed_bytes)}</b></span>
                <span>目标可用空间：<b>{formatBytes(currentCheck.free_bytes)}</b></span>
              </div>
              {currentCheck.errors.length > 0 ? (
                <ul className="ws2-loc-issues is-bad" role="alert" aria-label="目标目录错误">
                  {currentCheck.errors.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>{issueText(issue)}</li>
                  ))}
                </ul>
              ) : null}
              {currentCheck.warnings.length > 0 ? (
                <div className="ws2-loc-warnings">
                  <ul className="ws2-loc-issues is-warn" role="status" aria-label="目标目录风险提示">
                    {currentCheck.warnings.map((issue, index) => (
                      <li key={`${issue.code}-${index}`}>{issueText(issue)}</li>
                    ))}
                  </ul>
                  <label className="ws2-loc-ack">
                    <input
                      type="checkbox"
                      checked={ackWarnings}
                      disabled={checking || moveLocked}
                      onChange={(e) => setAckWarnings(e.target.checked)}
                    />
                    <span>我已了解以上提示，仍要继续迁移</span>
                  </label>
                </div>
              ) : null}
              {currentCheck.ok && currentCheck.errors.length === 0 && currentCheck.warnings.length === 0 ? (
                <p className="ws2-m-note is-done" role="status">目标目录检查通过，可以开始迁移。</p>
              ) : null}
            </div>
          ) : null}

          {moveError ? <p className="ws2-m-error" role="alert">{moveError}</p> : null}

          {moving ? (
            <p className="ws2-m-flag" role="status" data-testid="location-moving-notice">
              正在复制并校验数据，期间已锁定数据操作，请勿关闭 AMADEUS…
            </p>
          ) : null}

          {renderBannerInline && relocation ? (
            <RelocationRestartBanner state={relocation} onUpdate={onRelocate} />
          ) : null}

          <div className="ws2-loc-actions">
            <p className="ws2-m-note">迁移前会复制并校验全部数据库与文件，原目录会保留直至你手动删除。</p>
            <button
              type="button"
              className="ws2-m-btn"
              onClick={() => { void handleMove(); }}
              disabled={!canStartMove}
            >
              {moving ? '迁移中…' : '开始迁移并重启'}
            </button>
          </div>
        </div>
      ) : (
        <div className="ws2-m-row" data-testid="location-non-movable">
          <div className="ws2-m-label">
            <b>迁移数据位置</b>
            <small>
              {location.source === 'env'
                ? '当前由环境变量 AMADEUS_DATA_DIR 指定位置，应用内迁移已禁用。'
                : '当前数据位置由外部指定，应用内迁移已禁用。'}
            </small>
          </div>
          <span className="ws2-m-acts">
            <button type="button" className="ws2-m-btn" disabled>开始迁移并重启</button>
          </span>
        </div>
      )}

      {location.previous_path && location.previous_exists ? (
        <div className="ws2-m-row ws2-m-path ws2-loc-previous" data-testid="location-previous-dir">
          <div className="ws2-m-label">
            <b>旧数据目录</b>
            <span className="ws2-path">{location.previous_path}</span>
            <small className={previousRevealError ? 'is-bad' : undefined}>
              {previousRevealError ?? '确认新位置一切正常后，可打开核对或删除旧目录释放空间。'}
            </small>
          </div>
          <span className="ws2-m-acts">
            {inShell ? (
              <button
                type="button"
                className="ws2-m-link"
                onClick={() => {
                  setPreviousRevealError(null);
                  revealInFolder(location.previous_path!).catch(() => setPreviousRevealError('没能打开旧目录。'));
                }}
              >
                打开旧目录
              </button>
            ) : null}
            <button
              type="button"
              className="ws2-m-btn is-danger"
              onClick={() => {
                setDeleteError(null);
                setConfirmingDeletePrevious(true);
              }}
              disabled={moveLocked}
            >
              删除旧目录
            </button>
          </span>
        </div>
      ) : null}

      {cleanupToast ? (
        <div
          className={`ws2-loc-toast ${cleanupToast.bad ? 'is-bad' : 'is-done'}`}
          role="status"
          data-testid="location-cleanup-toast"
        >
          <span>{cleanupToast.text}</span>
          <button type="button" className="ws2-m-link" onClick={() => setCleanupToast(null)}>知道了</button>
        </div>
      ) : null}

      {confirmingDeletePrevious && location.previous_path ? (
        <OnSheet anchor={anchor}>
          <ConfirmDialog
            title="删除旧数据目录？"
            confirmLabel="删除旧目录"
            danger
            stamp={<Stamp sub="PERMANENT">不可撤回</Stamp>}
            busy={deletingPrevious}
            error={deleteError}
            onConfirm={() => { void handleDeletePrevious(); }}
            onCancel={() => { if (!deletingPrevious) setConfirmingDeletePrevious(false); }}
          >
            <p>将永久删除以下旧数据目录及其中的全部文件：</p>
            <p><code className="ws2-path">{location.previous_path}</code></p>
            <p className="ws2-dialog-hint">当前正在使用的数据目录（{location.path}）不受影响；删除后无法恢复。</p>
          </ConfirmDialog>
        </OnSheet>
      ) : null}

      {disk ? (
        <>
          <ul className="ws2-disk" aria-label="占用">
            {rows.map((row) => (
              <li key={row.key}>
                <span className="name">{row.label}</span>
                <span className="dim" aria-hidden="true"><i style={{ width: `max(3px, ${(row.bytes / most) * 100}%)` }} /></span>
                <span className="size">{formatBytes(row.bytes)}</span>
              </li>
            ))}
            <li className="total"><span className="name">合计</span><span className="dim" /><span className="size">{formatBytes(disk.total_bytes)}</span></li>
          </ul>
          <p className="ws2-m-note">记忆用的嵌入模型装在程序目录里，不算在这里。</p>
        </>
      ) : relocation ? (
        <p className="ws2-m-note">重启 AMADEUS 后再统计新位置占用。</p>
      ) : (
        <p className="ws2-m-note">正在量占用…</p>
      )}
    </section>
  );
}
