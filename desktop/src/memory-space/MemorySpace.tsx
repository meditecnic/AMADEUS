import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { memorySpaceShortcut } from './chrome';
import { ConstellationStage, type ScreenAnchor } from './ConstellationStage';
import { emphasisMode } from './emphasis';
import { applyIfCurrent, fetchMemoryProjection } from './fetchProjection';
import { evidenceParentId, openTopicBranch, soulLockupLines } from './graphPaths';
import { layoutProjection, readingSurfaceMode } from './layout';
import { ReadingFields, readingCardFromNode } from './readingContent';
import type { IdentityMode, MemoryProjection, Worldline } from './types';

export type MemorySpaceProps = {
  sessionId: string;
  chatWorldline: Worldline;
  chatIdentityMode: IdentityMode;
  onClose: () => void;
};

type JobSummary = {
  pending_count?: number;
  processing_count?: number;
  failed_count?: number;
};

export function MemorySpace({
  sessionId,
  chatWorldline,
  chatIdentityMode,
  onClose,
}: MemorySpaceProps) {
  const [worldline, setWorldline] = useState<Worldline>(chatWorldline);
  const [identityMode, setIdentityMode] = useState<IdentityMode>(chatIdentityMode);
  const [query, setQuery] = useState('');
  const [epoch, setEpoch] = useState(1);
  const epochRef = useRef(1);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [projection, setProjection] = useState<MemoryProjection | null>(null);
  const [jobs, setJobs] = useState<JobSummary | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [force2d, setForce2d] = useState(false);
  const [overviewOpen] = useState(true);
  const [expandedTopicIds, setExpandedTopicIds] = useState<string[]>([]);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [anchor, setAnchor] = useState<ScreenAnchor | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [width, setWidth] = useState(
    typeof window === 'undefined' ? 1280 : window.innerWidth,
  );
  const reducedMotion = useMemo(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }, []);

  const bumpEpoch = useCallback(() => {
    const next = epochRef.current + 1;
    epochRef.current = next;
    setEpoch(next);
    return next;
  }, []);

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    const requestEpoch = epoch;
    setStatus('loading');
    setErrorCode(null);
    void fetchMemoryProjection({
      sessionId,
      worldline,
      identityMode,
      query,
      epoch: requestEpoch,
    }).then(
      (payload) => {
        const accepted = applyIfCurrent(epochRef.current, payload.epoch, payload.projection);
        if (!accepted) return;
        setProjection(accepted);
        setStatus('ready');
      },
      (error: unknown) => {
        if (requestEpoch !== epochRef.current) return;
        setStatus('error');
        setErrorCode(error instanceof Error ? error.message : 'projection_failed');
      },
    );
    void fetch(`/api/memory/status?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}&identity_mode=${encodeURIComponent(identityMode)}`)
      .then(async (response) => (response.ok ? response.json() as Promise<JobSummary> : null))
      .then((summary) => {
        if (requestEpoch !== epochRef.current || !summary) return;
        setJobs(summary);
      })
      .catch(() => {
        /* operational summary is optional */
      });
  }, [epoch, identityMode, query, sessionId, worldline]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (selectedId) {
          setSelectedId(null);
          setExpandedTopicIds([]);
          return;
        }
        if (expandedTopicIds.length) {
          setExpandedTopicIds([]);
          return;
        }
        if (toolsOpen) {
          setToolsOpen(false);
          return;
        }
        onClose();
        return;
      }
      const shortcut = memorySpaceShortcut(event);
      if (shortcut === 'search') {
        event.preventDefault();
        setToolsOpen(true);
        window.requestAnimationFrame(() => searchRef.current?.focus());
        return;
      }
      if (shortcut === 'list') {
        setListOpen((value) => !value);
        return;
      }
      if (shortcut === 'view-mode') {
        setForce2d((value) => !value);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expandedTopicIds.length, onClose, selectedId, toolsOpen]);

  const display = projection ? layoutProjection(projection) : [];
  const selected = display.find((n) => n.id === selectedId) ?? null;
  const card = selected ? readingCardFromNode(selected) : null;
  const surface = readingSurfaceMode({ width, preferList: listOpen, reducedMotion });
  const mode: '3d' | '2d' = force2d || reducedMotion ? '2d' : '3d';
  const inspectorOpen = overviewOpen && selected?.kind === 'continuity_hub';
  const memoOpen = Boolean(card) && overviewOpen;
  const showSceneMemo = memoOpen && surface === 'scene' && mode === '3d';
  const showDomMemo = memoOpen && !showSceneMemo;
  const phase = overviewOpen ? 'overview' : 'soul';
  const lockup = soulLockupLines(
    projection?.center.label_primary ?? 'AMADEUS',
    projection?.center.label_secondary ?? 'SOUL',
  );

  const revealTopic = (topicId: string) => {
    setExpandedTopicIds((current) => openTopicBranch(topicId, current));
  };

  const handleSelect = (id: string | null) => {
    if (!id) {
      setSelectedId(null);
      setExpandedTopicIds([]);
      return;
    }
    const hit = display.find((node) => node.id === id);
    if (!hit) {
      setSelectedId(null);
      return;
    }
    if (hit.kind === 'continuity_hub') {
      setSelectedId(hit.id);
      return;
    }
    if (hit.kind === 'topic') {
      revealTopic(hit.id);
      setSelectedId(hit.id);
      return;
    }
    const parent = evidenceParentId(hit.id, projection?.edges ?? []);
    if (parent) revealTopic(parent);
    setSelectedId(hit.id);
  };
  const emphasis = emphasisMode({
    query,
    resultIds: projection?.result_ids ?? [],
    hoverId: null,
    selectedId,
  });

  const activateSoul = () => {
    setSelectedId(projection?.center.projection_id ?? null);
  };

  return (
    <section
      className="memory-space"
      data-testid="memory-space"
      data-phase={phase}
      data-expanded-topics={expandedTopicIds.join(',')}
      data-worldline={worldline}
      data-emphasis={emphasis}
      aria-label="Memory Space"
    >
      <header className="memory-space-veil" data-testid="memory-space-veil" data-chrome="compact">
        <button type="button" className="memory-space-return" onClick={onClose}>
          返回
        </button>
        <button
          type="button"
          className="memory-space-mark"
          data-testid="select-soul"
          onClick={activateSoul}
        >
          <span>MEMORY</span>
          <span className="memory-space-soul-mark">SOUL</span>
        </button>
        <span className="memory-space-scope" data-testid="memory-plaque">
          {identityMode === 'self' ? 'SELF' : 'OKABE'} · {worldline === 'steins_gate' ? 'SG' : 'β'}
        </span>
        <button
          type="button"
          className="memory-space-tools-toggle"
          aria-expanded={toolsOpen}
          aria-controls="memory-space-tools"
          onClick={() => setToolsOpen((value) => !value)}
        >
          仪器
        </button>
        <div
          id="memory-space-tools"
          className={`memory-space-tools${toolsOpen ? ' is-open' : ''}`}
          data-testid="memory-space-tools"
          data-open={String(toolsOpen)}
        >
          {projection ? (
            <span className="memory-space-count" data-testid="memory-shown-eligible">
              当前呈现 {projection.shown.results} / {projection.eligible.results}
            </span>
          ) : null}
          <label className="memory-space-search">
            <span className="sr-only">搜索记忆</span>
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                bumpEpoch();
                setSelectedId(null);
              }}
              placeholder="搜索"
              aria-label="搜索记忆"
              tabIndex={toolsOpen ? 0 : -1}
            />
          </label>
          <button type="button" aria-pressed={listOpen} onClick={() => setListOpen((v) => !v)} tabIndex={toolsOpen ? 0 : -1}>
            LIST
          </button>
          <button type="button" aria-pressed={force2d} onClick={() => setForce2d((v) => !v)} tabIndex={toolsOpen ? 0 : -1}>
            {mode === '2d' ? '2D' : '3D'}
          </button>
          <select
            aria-label="浏览身份"
            value={identityMode}
            tabIndex={toolsOpen ? 0 : -1}
            onChange={(event) => {
              setIdentityMode(event.target.value as IdentityMode);
              bumpEpoch();
              setSelectedId(null);
            }}
          >
            <option value="okabe">OKABE</option>
            <option value="self">SELF</option>
          </select>
          <select
            aria-label="浏览世界线"
            value={worldline}
            tabIndex={toolsOpen ? 0 : -1}
            onChange={(event) => {
              setWorldline(event.target.value as Worldline);
              bumpEpoch();
              setSelectedId(null);
            }}
          >
            <option value="steins_gate">SG</option>
            <option value="beta">β</option>
          </select>
        </div>
      </header>

      <p className="memory-soul-lockup is-mirror" data-testid="soul-lockup">
        <span className="memory-soul-lockup-soul">{lockup[0]}</span>
      </p>

      {status === 'loading' ? (
        <p className="memory-space-state" data-testid="memory-loading">正在读取记忆投影…</p>
      ) : null}
      {status === 'error' ? (
        <p className="memory-space-state" role="alert" data-testid="memory-error">
          投影失败（{errorCode || 'projection_failed'}）
        </p>
      ) : null}
      {status === 'ready' && projection?.empty && overviewOpen ? (
        <p className="memory-space-state" data-testid="memory-empty">这个范围里还没有可呈现的记忆。</p>
      ) : null}

      <div className="memory-constellation-host">
        {projection && status !== 'error' ? (
          <ConstellationStage
            projection={projection}
            selectedId={selectedId}
            mode={mode}
            reducedMotion={reducedMotion}
            expanded={overviewOpen}
            expandedTopicIds={expandedTopicIds}
            worldline={worldline}
            query={query}
            onSelect={handleSelect}
            onSelectedAnchor={(next) => {
              setAnchor((prev) => {
                if (!next && !prev) return prev;
                if (next && prev && next.x === prev.x && next.y === prev.y) return prev;
                return next;
              });
            }}
          />
        ) : null}

        {showSceneMemo && card ? (
          <aside
            className="memory-reading-scene"
            data-testid="reading-scene"
            data-attached="sphere"
            style={anchor ? { left: anchor.x, top: anchor.y } : undefined}
          >
            <ReadingFields card={card} />
          </aside>
        ) : null}
      </div>

      {listOpen && projection ? (
        <ol className="memory-space-list" data-testid="memory-list">
          {projection.result_ids.map((id) => {
            const node = display.find((item) => item.id === id);
            if (!node) return null;
            return (
              <li key={id}>
                <button type="button" onClick={() => {
                  const parent = evidenceParentId(id, projection.edges);
                  if (parent) revealTopic(parent);
                  setSelectedId(id);
                }}>
                  {node.label}
                </button>
              </li>
            );
          })}
        </ol>
      ) : null}

      {inspectorOpen && projection ? (
        <aside className="memory-inspector" data-testid="soul-inspector">
          <h2>AMADEUS / SOUL</h2>
          <p>SOUL 是当前 Amadeus 记忆连续性的投影中心，不是一条可编辑的记忆。</p>
          <dl data-testid="inspector-composition">
            <div><dt>稳定事实</dt><dd>{projection.composition.active_facts}</dd></div>
            <div><dt>经历</dt><dd>{projection.composition.active_experiences}</dd></div>
            <div><dt>主题</dt><dd>{projection.composition.eligible_topics}</dd></div>
            <div>
              <dt>最近变更</dt>
              <dd data-testid="inspector-latest-change">
                {projection.composition.latest_memory_change_at ?? '无'}
              </dd>
            </div>
          </dl>
          <dl data-testid="inspector-projection-counts">
            <div>
              <dt>结果</dt>
              <dd>{projection.shown.results} / {projection.eligible.results}</dd>
            </div>
            <div>
              <dt>记录</dt>
              <dd>{projection.shown.records} / {projection.eligible.records}</dd>
            </div>
            <div>
              <dt>节点</dt>
              <dd>{projection.shown.nodes} / {projection.eligible.nodes}</dd>
            </div>
            <div>
              <dt>边</dt>
              <dd>{projection.shown.edges} / {projection.eligible.edges}</dd>
            </div>
          </dl>
          {jobs ? (
            <p data-testid="inspector-jobs">
              处理中 {jobs.processing_count ?? 0} · 失败 {jobs.failed_count ?? 0} · 排队 {jobs.pending_count ?? 0}
            </p>
          ) : null}
          <details data-testid="inspector-diagnostics">
            <summary>诊断</summary>
            <dl>
              <div>
                <dt>范围</dt>
                <dd>
                  {projection.scope.session_id} / {projection.scope.worldline} / {projection.scope.identity_mode}
                </dd>
              </div>
              <div><dt>版本</dt><dd>{projection.projection_version}</dd></div>
              <div>
                <dt>预算</dt>
                <dd>
                  nodes {projection.budgets.nodes}/{projection.budgets.hard_max_nodes} ·
                  edges {projection.budgets.edges}/{projection.budgets.hard_max_edges}
                </dd>
              </div>
              <div>
                <dt>截断</dt>
                <dd>
                  nodes={String(projection.truncated.nodes)} edges={String(projection.truncated.edges)}{' '}
                  records={String(projection.truncated.records)} results={String(projection.truncated.results)}
                </dd>
              </div>
            </dl>
          </details>
        </aside>
      ) : null}

      {showDomMemo && card ? (
        <aside className="memory-reading-dom" data-testid="reading-dom">
          <ReadingFields card={card} />
        </aside>
      ) : null}
    </section>
  );
}
