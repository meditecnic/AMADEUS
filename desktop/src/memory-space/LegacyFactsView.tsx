import { useEffect, useRef, useState, type RefObject } from 'react';
import { ConfirmDialog } from '../workstation/ConfirmDialog';
import { deleteLegacyFact, fetchLegacyFacts, type ArchiveScope, type LegacyFact } from './archiveApi';
import { scopeLabel } from './archiveState';
import { MemorySelect } from './MemorySelect';
import type { IdentityMode, Worldline } from './types';

export function LegacyFactsView({ scope, archive, visible, searchRef, onCloseFind, onIdentity, onWorldline,
  runtimeState = 'ready', onRetryRuntime }: {
  scope: ArchiveScope;
  archive: boolean;
  visible: boolean;
  searchRef: RefObject<HTMLInputElement | null>;
  onCloseFind: () => void;
  onIdentity: (identity: IdentityMode) => void;
  onWorldline: (worldline: Worldline) => void;
  runtimeState?: 'loading' | 'ready' | 'failed';
  onRetryRuntime?: () => void;
}) {
  const [facts, setFacts] = useState<LegacyFact[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [query, setQuery] = useState('');
  const [retry, setRetry] = useState(0);
  const [pending, setPending] = useState<{ fact: LegacyFact; scope: ArchiveScope; scopeKey: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const scopeKey = JSON.stringify([scope.sessionId, scope.worldline, scope.identityMode]);
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  const busyRef = useRef(false);
  const deleteController = useRef<AbortController | null>(null);
  const refreshNotice = useRef('');
  useEffect(() => {
    setPending(null);
    setDeleteError(null);
    setAnnouncement('');
    refreshNotice.current = '';
    busyRef.current = false;
    setDeleting(false);
    return () => deleteController.current?.abort();
  }, [scopeKey, visible, runtimeState]);
  useEffect(() => {
    const controller = new AbortController();
    setFacts([]);
    setStatus('loading');
    if (runtimeState !== 'ready') return () => controller.abort();
    void fetchLegacyFacts(scope, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      if (result.type === 'ok') {
        setFacts(result.payload);
        setStatus('ready');
        if (refreshNotice.current) {
          setAnnouncement(refreshNotice.current);
          refreshNotice.current = '';
        }
      } else setStatus('failed');
    });
    return () => controller.abort();
  }, [scope, retry, runtimeState]);

  const confirmDelete = async () => {
    if (!pending || busyRef.current || pending.scopeKey !== currentScope.current || runtimeState !== 'ready') return;
    busyRef.current = true;
    setDeleting(true);
    setDeleteError(null);
    const controller = new AbortController();
    deleteController.current = controller;
    const result = await deleteLegacyFact(pending.scope, pending.fact, { signal: controller.signal });
    if (controller.signal.aborted || pending.scopeKey !== currentScope.current) return;
    busyRef.current = false;
    setDeleting(false);
    if (result.type === 'ok' || result.type === 'not-found') {
      setPending(null);
      setFacts([]);
      setStatus('loading');
      if (result.type === 'not-found') refreshNotice.current = '这条记忆已经不在了，列表已刷新。';
      else setAnnouncement('已删除。');
      setRetry((value) => value + 1);
    } else setDeleteError('删除失败，请稍后重试。');
  };

  if (!visible) return null;
  const readState = runtimeState === 'ready' ? status : runtimeState;
  const needle = query.trim().toLocaleLowerCase();
  const matches = facts.filter((fact) => `${fact.factKey} ${fact.text}`.toLocaleLowerCase().includes(needle));
  const visiblePending = pending?.scopeKey === scopeKey && runtimeState === 'ready' ? pending : null;
  const worldlineLabel = scope.worldline === 'beta' ? 'β' : 'SG';
  return (
    <>
    <section className={archive ? 'archive-view legacy-facts-view' : 'memory-space-find legacy-facts-view'}
      id={archive ? undefined : 'memory-space-find'} data-testid={archive ? 'archive-view' : 'memory-space-find'}
      aria-label={archive ? '记忆档案' : '记忆检索'}>
      <header className="archive-heading">
        <h2>{archive ? '记忆档案' : '记忆检索'}</h2>
        <span className="archive-scope-mark">{scopeLabel(scope)}</span>
        {!archive ? <button type="button" className="memory-panel-close" aria-label="关闭查找" onClick={onCloseFind}>×</button> : null}
      </header>
      <p className="memory-runtime-notice" role="status">
        这些是对话中正在使用的记忆，可以逐条删除。要遗忘某个会话带来的记忆，请在会话菜单选择「清空聊天记录」并勾选遗忘选项。
      </p>
      <div className="archive-toolbar">
        <label className="memory-space-search">
          <span className="sr-only">搜索记忆</span>
          <input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索记忆" aria-label="搜索记忆" />
        </label>
        <MemorySelect aria-label="浏览身份" value={scope.identityMode} onValueChange={(value) => onIdentity(value as IdentityMode)}>
          <option value="okabe">OKABE</option><option value="self">USER</option>
        </MemorySelect>
        <MemorySelect aria-label="浏览世界线" value={scope.worldline} onValueChange={(value) => onWorldline(value as Worldline)}>
          <option value="steins_gate">SG</option><option value="beta">β</option>
        </MemorySelect>
      </div>
      {announcement ? <p role="status" className={announcement === '已删除。' ? 'sr-only' : undefined}>{announcement}</p> : null}
      <div className="legacy-facts-list" aria-live="polite">
        {readState === 'loading' ? <p>正在读取记忆…</p> : readState === 'failed' ? (
          <p role="alert">记忆暂时无法读取。<button type="button" onClick={() => {
            if (runtimeState === 'failed') onRetryRuntime?.();
            else setRetry((value) => value + 1);
          }}>重试</button></p>
        ) : <>
          <h3>事实（{matches.length}）</h3>
          {matches.length ? <ul>{matches.map((fact) => <li key={fact.id}>
            <span>{fact.text}</span>{' '}
            {fact.source === 'shared' ? <span className="archive-row-marker">SG 与 β 共用</span> : null}{' '}
            <button type="button" aria-label={`删除：${fact.text}`} disabled={deleting} onClick={() => {
              setPending({ fact, scope: { ...scope }, scopeKey });
              setDeleteError(null);
              setAnnouncement('');
            }}>删除</button>
          </li>)}</ul>
            : <p>{facts.length ? '没有匹配的事实。' : '这里还没有对话中正在使用的记忆。'}</p>}
        </>}
      </div>
    </section>
    {visiblePending ? <ConfirmDialog title="删除这条记忆？" confirmLabel="删除" danger busy={deleting}
      error={deleteError} returnFocus={() => [searchRef.current]}
      onConfirm={() => { void confirmDelete(); }} onCancel={() => { setPending(null); setDeleteError(null); }}>
      <p>{visiblePending.fact.text}</p>
      <p>{visiblePending.fact.source === 'shared' ? '这条由 SG 与 β 共用。删除后，两条世界线的 USER 对话都不再使用。'
        : `删除后，她在 ${visiblePending.fact.source === 'okabe' ? 'OKABE' : 'USER'} · ${worldlineLabel} 的对话里不再使用这条。`}</p>
      {visiblePending.fact.hasSharedFallback ? <p>SG 与 β 共用的记忆里也有这一项，删除后会显示出来，可以再删。</p> : null}
      <p>聊天记录不会删除。</p>
    </ConfirmDialog> : null}
    </>
  );
}
