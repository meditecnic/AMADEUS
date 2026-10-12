import { useEffect, useState, type RefObject } from 'react';
import { fetchLegacyFacts, type ArchiveScope, type LegacyFact } from './archiveApi';
import { scopeLabel } from './archiveState';
import { MemorySelect } from './MemorySelect';
import type { IdentityMode, Worldline } from './types';

export function LegacyFactsView({ scope, archive, visible, searchRef, onCloseFind, onIdentity, onWorldline }: {
  scope: ArchiveScope;
  archive: boolean;
  visible: boolean;
  searchRef: RefObject<HTMLInputElement | null>;
  onCloseFind: () => void;
  onIdentity: (identity: IdentityMode) => void;
  onWorldline: (worldline: Worldline) => void;
}) {
  const [facts, setFacts] = useState<LegacyFact[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [query, setQuery] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setFacts([]);
    setStatus('loading');
    void fetchLegacyFacts(scope, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      if (result.type === 'ok') {
        setFacts(result.payload);
        setStatus('ready');
      } else setStatus('failed');
    });
    return () => controller.abort();
  }, [scope, retry]);
  if (!visible) return null;
  const needle = query.trim().toLocaleLowerCase();
  const matches = facts.filter((fact) => `${fact.factKey} ${fact.text}`.toLocaleLowerCase().includes(needle));
  return (
    <section className={archive ? 'archive-view legacy-facts-view' : 'memory-space-find legacy-facts-view'}
      id={archive ? undefined : 'memory-space-find'} data-testid={archive ? 'archive-view' : 'memory-space-find'}
      aria-label={archive ? '记忆档案' : '记忆检索'}>
      <header className="archive-heading">
        <h2>{archive ? '记忆档案' : '记忆检索'}</h2>
        <span className="archive-scope-mark">{scopeLabel(scope)}</span>
        {!archive ? <button type="button" className="memory-panel-close" aria-label="关闭查找" onClick={onCloseFind}>×</button> : null}
      </header>
      <p className="memory-runtime-notice" role="status">
        这些是对话中正在使用的记忆，这里只能查看。要遗忘某个会话带来的记忆，请在会话菜单选择「清空聊天记录」并勾选遗忘选项。
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
      <div className="legacy-facts-list" aria-live="polite">
        {status === 'loading' ? <p>正在读取记忆…</p> : status === 'failed' ? (
          <p role="alert">记忆暂时无法读取。<button type="button" onClick={() => setRetry((value) => value + 1)}>重试</button></p>
        ) : <>
          <h3>事实（{matches.length}）</h3>
          {matches.length ? <ul>{matches.map((fact) => <li key={fact.id}>{fact.text}</li>)}</ul>
            : <p>{facts.length ? '没有匹配的事实。' : '这里还没有对话中正在使用的记忆。'}</p>}
        </>}
      </div>
    </section>
  );
}
