import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Choice } from './Choice';
import { ConfirmDialog } from './ConfirmDialog';
import { Stamp } from './Stamp';
import {
  ApiError, authState, createBackup, dataLocation, dataStatus, deleteBackup, diskUsage, exportArchive, formatBytes, formatStamp, formatVersion,
  restoreBackup, setPasscode, type AuthState, type Backup, type DataLocation, type DiskCategory, type DiskUsage, type ExportRequest,
} from './dataApi';
import { inShell, revealInFolder, saveBlob } from './shell';

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
};
export const errorText = (error: unknown, fallback: string) => {
  const code = error instanceof ApiError ? error.code : null;
  return code ? ERRORS[code] ?? `${fallback}（${code}）` : fallback;
};

/** Dialogs belong on the sheet, over everything, not inside the scrolling form. */
function OnSheet({ anchor, children }: { anchor: React.RefObject<HTMLElement | null>; children: ReactNode }) {
  const root = anchor.current?.closest<HTMLElement>('.ws2');
  return root ? createPortal(children, root) : <>{children}</>;
}

export function DataPanel() {
  const anchor = useRef<HTMLDivElement>(null);
  return (
    <div className="ws2-data" ref={anchor}>
      <PasscodeGroup />
      <BackupGroup anchor={anchor} />
      <ExportGroup />
      <LocationGroup />
    </div>
  );
}

/* ---------- the startup passcode ---------- */

type PasscodeMode = 'view' | 'set' | 'change' | 'off';

function PasscodeGroup() {
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
    let live = true;
    authState().then((state) => { if (live) setAuth(state); }, (failure) => { if (live) setLoadError(errorText(failure, '读不到口令的状态。')); });
    return () => { live = false; };
  }, []);
  useEffect(() => { if (mode !== 'view') firstField.current?.focus({ preventScroll: true }); }, [mode]);

  const open = (to: PasscodeMode) => { setMode(to); setCurrent(''); setNext(''); setAgain(''); setError(null); setDone(null); };
  const set = Boolean(auth?.passcode_set);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
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
                : <small>{auth ? (set ? '已设口令' : '没有口令，打开即进') : '读取中…'}</small>}
        </div>
        {auth && mode === 'view' ? (
          <span className="ws2-m-acts">
            {set ? <button type="button" className="ws2-m-link" onClick={() => open('off')}>关掉口令</button> : null}
            <button type="button" className="ws2-m-btn" onClick={() => open(set ? 'change' : 'set')}>{set ? '改口令' : '设口令'}</button>
          </span>
        ) : null}
      </div>
      {mode !== 'view' ? (
        <form className="ws2-m-form" onSubmit={submit} aria-label={mode === 'off' ? '关掉口令' : mode === 'change' ? '改口令' : '设口令'}>
          {set ? (
            <label className="ws2-m-field">
              <span>现在的口令</span>
              <input ref={firstField} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} disabled={busy} />
            </label>
          ) : null}
          {mode !== 'off' ? (
            <>
              <label className="ws2-m-field">
                <span>新口令</span>
                <input ref={set ? undefined : firstField} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} disabled={busy} />
              </label>
              <label className="ws2-m-field">
                <span>再输一次</span>
                <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} disabled={busy} />
              </label>
            </>
          ) : null}
          <div className="ws2-m-form-foot">
            {error ? <p className="ws2-m-error" role="alert">{error}</p> : <span />}
            <span className="ws2-m-acts">
              <button type="button" className="ws2-m-link" onClick={() => setMode('view')} disabled={busy}>取消</button>
              <button type="submit" className="ws2-m-btn" disabled={busy}>{busy ? '保存中…' : mode === 'off' ? '关掉口令' : '保存口令'}</button>
            </span>
          </div>
        </form>
      ) : null}
      <p className="ws2-m-note">口令只在打开 AMADEUS 时问一次，不加密数据。忘了口令：关掉 AMADEUS，删掉数据目录里的 auth\passcode.json 即可。</p>
    </section>
  );
}

/* ---------- backups ---------- */

function BackupGroup({ anchor }: { anchor: React.RefObject<HTMLElement | null> }) {
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
    let live = true;
    dataStatus().then((status) => {
      if (!live) return;
      setBackups(status.backups);
      if (status.status === 'restore_failed') setRestoreFailed(status.detail ?? '');
    }, (failure) => { if (live) setLoadError(errorText(failure, '读不到备份。')); });
    return () => { live = false; };
  }, []);

  const make = async () => {
    setMaking(true);
    setNote(null);
    try {
      const made = await createBackup();
      const status = await dataStatus();
      setBackups(status.backups);
      setNote({ text: `已备份（${formatStamp(status.backups.find((row) => row.id === made.id)?.time ?? null)}）。` });
    } catch (failure) {
      setNote({ text: errorText(failure, '备份没有完成。'), bad: true });
    } finally {
      setMaking(false);
    }
  };

  const answer = async () => {
    if (!asking) return;
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
      setAskError(errorText(failure, asking.kind === 'restore' ? '没能安排恢复。' : '没能删除。'));
    } finally {
      setBusy(false);
    }
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
        </p>
      ) : null}
      {scheduled ? (
        <p className="ws2-m-flag" role="status">
          已安排：下次打开 AMADEUS 时，数据换成 {formatStamp(scheduled.time)} 的备份。现在关掉再打开即可。
        </p>
      ) : null}
      <div className="ws2-m-row">
        <div className="ws2-m-label">
          <b>现在备份一份</b>
          {note ? <small className={note.bad ? 'is-bad' : 'is-done'} role="status">{note.text}</small>
            : <small>手动的备份不会被自动删除</small>}
        </div>
        <span className="ws2-m-acts">
          <button type="button" className="ws2-m-btn" onClick={() => { void make(); }} disabled={making || !backups}>{making ? '备份中…' : '立即备份'}</button>
        </span>
      </div>
      {loadError ? <p className="ws2-m-note is-bad">{loadError}</p> : null}
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
                      >
                        恢复
                      </button>
                    ) : <span className="tag">不完整</span>}
                  <button
                    type="button"
                    className="ws2-m-link is-danger"
                    aria-label={`删除 ${formatStamp(row.time ?? idTime(row.id))} 的备份`}
                    onClick={() => { setAskError(null); setAsking({ kind: 'delete', backup: row }); }}
                    disabled={pending}
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

function ExportGroup() {
  const [kind, setKind] = useState<'conversations' | 'memory'>('conversations');
  const [format, setFormat] = useState<'md' | 'json'>('md');
  const [worldline, setWorldline] = useState<Worldlines>('all');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);

  const run = async () => {
    setBusy(true);
    setNote(null);
    try {
      const request: ExportRequest = kind === 'memory' ? { kind, format: 'json', worldline } : { kind, format, worldline };
      const { blob, filename } = await exportArchive(request);
      saveBlob(blob, filename);
      setNote({ text: `已导出 ${filename}` });
    } catch (failure) {
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
          : <p className="ws2-m-note">{kind === 'memory' ? '导出为一个 zip 文件，里面是记忆的 JSON。' : '导出为一个 zip 文件，每个会话一份。'}</p>}
        <button type="button" className="ws2-m-btn" onClick={() => { void run(); }} disabled={busy}>{busy ? '导出中…' : '导出'}</button>
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

function LocationGroup() {
  const [location, setLocation] = useState<DataLocation | null | 'absent'>(null);
  const [disk, setDisk] = useState<DiskUsage | null>(null);
  const [revealError, setRevealError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    // A backend without these routes (before report 212) simply leaves the group out.
    dataLocation().then((value) => { if (live) setLocation(value); }, () => { if (live) setLocation('absent'); });
    diskUsage().then((value) => { if (live) setDisk(value); }, () => undefined);
    return () => { live = false; };
  }, []);
  if (location === 'absent' || location === null) return null;

  const rows = disk ? DISK.map((row) => ({ ...row, bytes: disk.categories[row.key] ?? 0 })).filter((row) => row.bytes > 0) : [];
  const most = Math.max(1, ...rows.map((row) => row.bytes));
  return (
    <section className="ws2-m-sec" aria-labelledby="ws2-data-location">
      <div className="ws2-m-group" id="ws2-data-location">数据位置</div>
      <div className="ws2-m-row ws2-m-path">
        <div className="ws2-m-label">
          <b><span className="ws2-path">{location.path}</span></b>
          <small>{revealError ?? SOURCE[location.source] ?? location.source}</small>
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
      ) : <p className="ws2-m-note">正在量占用…</p>}
    </section>
  );
}
