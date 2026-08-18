import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { CompatibilityView } from './CompatibilityView';
import { memorySpaceShortcut } from './chrome';
import { ConstellationStage, type ScreenAnchor } from './ConstellationStage';
import {
  EMPTY_CRITERIA,
  criteriaChips,
  clearChip,
  hasActiveCriteria,
  normalizeCriteria,
  type CriteriaChip,
  type CriteriaInput,
  type ScopeKey,
} from './criteria';
import { emphasisMode } from './emphasis';
import { evidenceParentId, openTopicBranch, soulLockupLines } from './graphPaths';
import { layoutProjection, readingSurfaceMode } from './layout';
import { classifyOverviewState } from './overviewState';
import { createProjectionController } from './projectionController';
import { ReadingFields, readingCardFromNode } from './readingContent';
import {
  HEALTHY_CAPABILITY,
  ambientMotionEnabled,
  applyContextLoss,
  applyCreateFailure,
  applyExplicitCompatibility,
  applyPerformanceSample,
  applyRetry3d,
  type CapabilityState,
  type FrameSample,
} from './renderCapability';
import {
  announceNode,
  neighborId,
  reconcileSelection,
  semanticOrder,
} from './semanticNavigator';
import type { IdentityMode, Worldline } from './types';

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

type TopicOption = { topic_id: string; display_label: string };

export function MemorySpace({
  sessionId,
  chatWorldline,
  chatIdentityMode,
  onClose,
}: MemorySpaceProps) {
  const controllerRef = useRef(createProjectionController());
  const [browseSessionId, setBrowseSessionId] = useState(sessionId);
  const [worldline, setWorldline] = useState<Worldline>(chatWorldline);
  const [identityMode, setIdentityMode] = useState<IdentityMode>(chatIdentityMode);
  const [criteria, setCriteria] = useState<CriteriaInput>(EMPTY_CRITERIA);
  const [loading, setLoading] = useState(true);
  const [scopeSwitching, setScopeSwitching] = useState(false);
  const [error, setError] = useState<{ kind: 'unavailable' | 'generic'; code: string } | null>(null);
  const [projection, setProjection] = useState<ReturnType<typeof classifyOverviewState>['envelope']>(null);
  const [previousTruth, setPreviousTruth] = useState<typeof projection>(null);
  const [jobs, setJobs] = useState<JobSummary | null>(null);
  const [topics, setTopics] = useState<TopicOption[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [listOwnsFocus, setListOwnsFocus] = useState(false);
  const [forceCompat, setForceCompat] = useState(false);
  const [capability, setCapability] = useState<CapabilityState>(HEALTHY_CAPABILITY);
  const [runtimeGeneration, setRuntimeGeneration] = useState(0);
  const [overviewOpen] = useState(true);
  const [expandedTopicIds, setExpandedTopicIds] = useState<string[]>([]);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [anchor, setAnchor] = useState<ScreenAnchor | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const searchRef = useRef<HTMLInputElement | null>(null);
  const graphRef = useRef<HTMLDivElement | null>(null);
  const framesRef = useRef<FrameSample[]>([]);
  const [width, setWidth] = useState(typeof window === 'undefined' ? 1280 : window.innerWidth);
  const [reducedMotion, setReducedMotion] = useState(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });

  useEffect(() => {
    setBrowseSessionId(sessionId);
  }, [sessionId]);

  const scope: ScopeKey = useMemo(
    () => ({ sessionId: browseSessionId, worldline, identityMode }),
    [browseSessionId, identityMode, worldline],
  );

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReducedMotion(media.matches);
    media.addEventListener?.('change', onChange);
    return () => media.removeEventListener?.('change', onChange);
  }, []);

  useEffect(() => () => controllerRef.current.dispose(), []);

  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;

  const applyEnvelope = useCallback((next: NonNullable<typeof projection>, keepError = false) => {
    setProjection(next);
    setPreviousTruth(next);
    if (!keepError) setError(null);
    const reconciled = reconcileSelection(selectedRef.current, next);
    setSelectedId(reconciled.selectedId);
    if (reconciled.fellBack) {
      setFocusedId(reconciled.selectedId);
      setAnnouncement(reconciled.announced ?? '');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const prior = controllerRef.current.lastSuccessful();
    setScopeSwitching(Boolean(prior && (
      prior.scope.sessionId !== scope.sessionId
      || prior.scope.worldline !== scope.worldline
      || prior.scope.identityMode !== scope.identityMode
    )));
    void controllerRef.current.load(scope, criteria).then((outcome) => {
      if (cancelled) return;
      if (outcome.type === 'stale') return;
      if (outcome.type === 'ok') {
        applyEnvelope(outcome.envelope);
        setLoading(false);
        setScopeSwitching(false);
        return;
      }
      if (outcome.restore) {
        setBrowseSessionId(outcome.restore.scope.sessionId);
        setWorldline(outcome.restore.scope.worldline);
        setIdentityMode(outcome.restore.scope.identityMode);
        setCriteria(outcome.restore.criteria);
        applyEnvelope(outcome.restore.envelope, true);
        setError(outcome.error);
        setLoading(false);
        setScopeSwitching(false);
        setAnnouncement('范围切换失败，已恢复先前范围。');
        return;
      }
      if (outcome.previous) {
        applyEnvelope(outcome.previous.envelope, true);
      }
      setError(outcome.error);
      setLoading(false);
      setScopeSwitching(false);
    });
    void fetch(`/api/memory/status?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}&identity_mode=${encodeURIComponent(identityMode)}`)
      .then(async (response) => (response.ok ? response.json() as Promise<JobSummary> : null))
      .then((summary) => {
        if (cancelled || !summary) return;
        setJobs(summary);
      })
      .catch(() => {
        /* operational summary is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [applyEnvelope, criteria, scope]);

  useEffect(() => {
    if (!toolsOpen) return undefined;
    let cancelled = false;
    void fetch(`/api/memory/topics?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}&identity_mode=${encodeURIComponent(identityMode)}`)
      .then(async (response) => (response.ok ? response.json() as Promise<{ topics?: TopicOption[] }> : null))
      .then((body) => {
        if (cancelled || !body?.topics) return;
        setTopics(body.topics);
      })
      .catch(() => {
        /* picker is optional; never invent a second graph */
      });
    return () => {
      cancelled = true;
    };
  }, [identityMode, sessionId, toolsOpen, worldline]);

  const presentation = classifyOverviewState({
    loading,
    scopeSwitching,
    envelope: projection,
    previousTruth,
    error,
    requestedCriteria: criteria,
  });
  const liveProjection = presentation.envelope;
  const display = liveProjection ? layoutProjection(liveProjection) : [];
  const selected = display.find((node) => node.id === selectedId) ?? null;
  const card = selected ? readingCardFromNode(selected) : null;
  const surface = readingSurfaceMode({ width, preferList: listOpen, reducedMotion });
  const cameraMode: '3d' | '2d' = forceCompat ? '2d' : '3d';
  const renderSurface = capability.surface;
  const inspectorOpen = overviewOpen && selected?.kind === 'continuity_hub';
  const memoOpen = Boolean(card) && overviewOpen;
  const showSceneMemo = memoOpen && surface === 'scene' && renderSurface === '3d' && cameraMode === '3d';
  const showDomMemo = memoOpen && !showSceneMemo;
  const phase = overviewOpen ? 'overview' : 'soul';
  const lockup = soulLockupLines(
    liveProjection?.center.label_primary ?? 'AMADEUS',
    liveProjection?.center.label_secondary ?? 'SOUL',
  );
  const chips = criteriaChips(liveProjection ? {
    query: liveProjection.criteria.query ?? '',
    kinds: liveProjection.criteria.kinds,
    topicId: liveProjection.criteria.topic_id,
    pinnedOnly: liveProjection.criteria.pinned_only,
    updatedFrom: liveProjection.criteria.updated_from,
    updatedTo: liveProjection.criteria.updated_to,
  } : criteria);
  const order = liveProjection ? semanticOrder(liveProjection) : [];

  const revealTopic = (topicId: string) => {
    setExpandedTopicIds((current) => openTopicBranch(topicId, current));
  };

  const handleSelect = (id: string | null) => {
    if (!id) {
      setSelectedId(null);
      setFocusedId(null);
      setExpandedTopicIds([]);
      return;
    }
    const hit = display.find((node) => node.id === id);
    if (!hit) {
      const fallback = reconcileSelection(id, liveProjection);
      setSelectedId(fallback.selectedId);
      setFocusedId(fallback.selectedId);
      if (fallback.announced) setAnnouncement(fallback.announced);
      return;
    }
    setFocusedId(hit.id);
    if (hit.kind === 'continuity_hub') {
      setSelectedId(hit.id);
      return;
    }
    if (hit.kind === 'topic') {
      revealTopic(hit.id);
      setSelectedId(hit.id);
      return;
    }
    const parent = evidenceParentId(hit.id, liveProjection?.edges ?? []);
    if (parent) revealTopic(parent);
    setSelectedId(hit.id);
    if (liveProjection) setAnnouncement(announceNode(liveProjection, hit.id));
  };

  const moveSemantic = (delta: number) => {
    const next = neighborId(order, focusedId ?? selectedId, delta);
    if (next) handleSelect(next);
  };

  const emphasis = emphasisMode({
    query: criteria.query,
    resultIds: liveProjection?.result_ids ?? [],
    hoverId: null,
    selectedId,
  });

  const changeCriteria = (next: CriteriaInput) => {
    setCriteria(normalizeCriteria(next));
  };

  const retryProjection = () => {
    setError(null);
    setLoading(true);
    setCriteria((current) => ({ ...current }));
  };

  const unwind = () => {
    if (toolsOpen || hasActiveCriteria(criteria)) {
      if (hasActiveCriteria(criteria)) {
        changeCriteria(EMPTY_CRITERIA);
        return;
      }
      setToolsOpen(false);
      return;
    }
    if (selectedId) {
      setSelectedId(null);
      setFocusedId(null);
      setExpandedTopicIds([]);
      return;
    }
    if (expandedTopicIds.length) {
      setExpandedTopicIds([]);
      return;
    }
    onClose();
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as { tagName?: string } | null;
      const tag = target && 'tagName' in target ? target.tagName : undefined;
      const inField = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
      if (event.key === 'Escape') {
        unwind();
        return;
      }
      if (inField) return;
      const shortcut = memorySpaceShortcut(event);
      if (shortcut === 'search') {
        event.preventDefault();
        setToolsOpen(true);
        window.requestAnimationFrame(() => searchRef.current?.focus());
        return;
      }
      if (shortcut === 'list') {
        setListOpen((value) => {
          const next = !value;
          setListOwnsFocus(next);
          return next;
        });
        return;
      }
      if (shortcut === 'view-mode') {
        setForceCompat((value) => !value);
        setCapability((current) => applyExplicitCompatibility(current, !forceCompat));
        return;
      }
      if (event.key === 'Home') {
        event.preventDefault();
        handleSelect(liveProjection?.center.projection_id ?? null);
        setListOwnsFocus(false);
        return;
      }
      if (event.key === 'Enter' && focusedId) {
        event.preventDefault();
        handleSelect(focusedId);
        return;
      }
      if (listOwnsFocus) return;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        event.preventDefault();
        moveSemantic(1);
      }
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        event.preventDefault();
        moveSemantic(-1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const truncated = Boolean(
    liveProjection
    && (liveProjection.truncated.results || liveProjection.shown.results < liveProjection.eligible.results),
  );

  return (
    <section
      className="memory-space"
      data-testid="memory-space"
      data-phase={phase}
      data-expanded-topics={expandedTopicIds.join(',')}
      data-worldline={worldline}
      data-emphasis={emphasis}
      data-surface={renderSurface}
      data-reduced-motion={String(reducedMotion)}
      aria-label="Memory Space"
    >
      <div className="sr-only" role="status" aria-live="polite" data-testid="memory-announce">{announcement}</div>
      <header className="memory-space-veil" data-testid="memory-space-veil" data-chrome="compact">
        <button type="button" className="memory-space-return" onClick={onClose}>
          返回
        </button>
        <button
          type="button"
          className="memory-space-mark"
          data-testid="select-soul"
          onClick={() => handleSelect(liveProjection?.center.projection_id ?? null)}
        >
          <span>MEMORY</span>
          <span className="memory-space-soul-mark">SOUL</span>
        </button>
        <span className="memory-space-scope" data-testid="memory-plaque">
          {identityMode === 'self' ? 'USER' : 'OKABE'} · {worldline === 'steins_gate' ? 'SG' : 'β'}
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
          hidden={!toolsOpen}
        >
          {liveProjection ? (
            <span className="memory-space-count" data-testid="memory-shown-eligible">
              当前呈现 {liveProjection.shown.results} / {liveProjection.eligible.results}
            </span>
          ) : null}
          <label className="memory-space-search">
            <span className="sr-only">搜索记忆</span>
            <input
              ref={searchRef}
              value={criteria.query}
              onChange={(event) => changeCriteria({ ...criteria, query: event.target.value })}
              placeholder="搜索"
              aria-label="搜索记忆"
              tabIndex={toolsOpen ? 0 : -1}
            />
          </label>
          <fieldset className="memory-space-kinds" data-testid="memory-kind-filter">
            <legend className="sr-only">种类</legend>
            {(['topic', 'fact', 'experience'] as const).map((kind) => (
              <label key={kind}>
                <input
                  type="checkbox"
                  checked={criteria.kinds.includes(kind)}
                  tabIndex={toolsOpen ? 0 : -1}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...criteria.kinds, kind]
                      : criteria.kinds.filter((item) => item !== kind);
                    changeCriteria({ ...criteria, kinds: next });
                  }}
                />
                {kind === 'topic' ? '主题' : kind === 'fact' ? '事实' : '经历'}
              </label>
            ))}
          </fieldset>
          <label>
            <span className="sr-only">主题筛选</span>
            <select
              aria-label="主题筛选"
              value={criteria.topicId ?? ''}
              tabIndex={toolsOpen ? 0 : -1}
              onChange={(event) => changeCriteria({ ...criteria, topicId: event.target.value || null })}
            >
              <option value="">全部主题</option>
              {topics.map((topic) => (
                <option key={topic.topic_id} value={topic.topic_id}>{topic.display_label}</option>
              ))}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={criteria.pinnedOnly}
              tabIndex={toolsOpen ? 0 : -1}
              onChange={(event) => changeCriteria({ ...criteria, pinnedOnly: event.target.checked })}
            />
            仅置顶
          </label>
          <label>
            <span className="sr-only">起始时间</span>
            <input
              type="datetime-local"
              aria-label="起始时间"
              tabIndex={toolsOpen ? 0 : -1}
              onChange={(event) => changeCriteria({
                ...criteria,
                updatedFrom: event.target.value ? new Date(event.target.value).toISOString() : null,
              })}
            />
          </label>
          <label>
            <span className="sr-only">结束时间</span>
            <input
              type="datetime-local"
              aria-label="结束时间"
              tabIndex={toolsOpen ? 0 : -1}
              onChange={(event) => changeCriteria({
                ...criteria,
                updatedTo: event.target.value ? new Date(event.target.value).toISOString() : null,
              })}
            />
          </label>
          <button type="button" aria-pressed={listOpen} onClick={() => {
            setListOpen((value) => {
              const next = !value;
              setListOwnsFocus(next);
              if (next) graphRef.current?.blur();
              return next;
            });
          }} tabIndex={toolsOpen ? 0 : -1}>
            LIST
          </button>
          <button
            type="button"
            aria-pressed={renderSurface === 'compatibility'}
            onClick={() => {
              const next = renderSurface !== 'compatibility';
              setForceCompat(next);
              setCapability((current) => applyExplicitCompatibility(current, next));
            }}
            tabIndex={toolsOpen ? 0 : -1}
          >
            {renderSurface === 'compatibility' || cameraMode === '2d' ? '2D' : '3D'}
          </button>
          <select
            aria-label="浏览身份"
            value={identityMode}
            tabIndex={toolsOpen ? 0 : -1}
            onChange={(event) => setIdentityMode(event.target.value as IdentityMode)}
          >
            <option value="okabe">OKABE</option>
            <option value="self">USER</option>
          </select>
          <select
            aria-label="浏览世界线"
            value={worldline}
            tabIndex={toolsOpen ? 0 : -1}
            onChange={(event) => setWorldline(event.target.value as Worldline)}
          >
            <option value="steins_gate">SG</option>
            <option value="beta">β</option>
          </select>
        </div>
      </header>

      {chips.length ? (
        <ul className="memory-space-chips" data-testid="memory-criteria-chips">
          {chips.map((chip: CriteriaChip) => (
            <li key={chip.id}>
              <button type="button" onClick={() => changeCriteria(clearChip(criteria, chip))}>
                {chip.label}
              </button>
            </li>
          ))}
          <li>
            <button type="button" onClick={() => changeCriteria(EMPTY_CRITERIA)}>清除筛选</button>
          </li>
        </ul>
      ) : null}

      <p className="memory-soul-lockup is-mirror" data-testid="soul-lockup">
        <span className="memory-soul-lockup-soul">{lockup[0]}</span>
      </p>

      {presentation.phase === 'loading' && !liveProjection ? (
        <p className="memory-space-state" data-testid="memory-loading">正在读取记忆投影…</p>
      ) : null}
      {presentation.phase === 'scope-switching' && liveProjection ? (
        <p className="memory-space-state" data-testid="memory-stale">{presentation.copy}</p>
      ) : null}
      {presentation.stale && presentation.phase !== 'scope-switching' && liveProjection ? (
        <p className="memory-space-state" data-testid="memory-stale">{presentation.copy ?? '同步中'}</p>
      ) : null}
      {presentation.phase === 'unavailable' ? (
        <p className="memory-space-state" role="alert" data-testid="memory-unavailable">
          {presentation.copy}
          {presentation.showRetry ? (
            <button type="button" data-testid="memory-retry" onClick={retryProjection}>重试</button>
          ) : null}
        </p>
      ) : null}
      {presentation.phase === 'error' ? (
        <p className="memory-space-state" role="alert" data-testid="memory-error">
          {presentation.copy}
          {presentation.showRetry ? (
            <button type="button" data-testid="memory-retry" onClick={retryProjection}>重试</button>
          ) : null}
        </p>
      ) : null}
      {presentation.phase === 'true-empty' ? (
        <p className="memory-space-state" data-testid="memory-empty">{presentation.copy}</p>
      ) : null}
      {presentation.phase === 'criteria-empty' ? (
        <p className="memory-space-state" data-testid="memory-criteria-empty">{presentation.copy}</p>
      ) : null}
      {truncated && liveProjection ? (
        <p className="memory-space-truncation" data-testid="memory-truncation">
          当前呈现 {liveProjection.shown.results} / {liveProjection.eligible.results}，并非全部记忆。
        </p>
      ) : null}
      {renderSurface === 'compatibility' ? (
        <p className="memory-space-state" data-testid="memory-compat-label">兼容视图</p>
      ) : null}
      {capability.reason !== 'healthy' && renderSurface === 'compatibility' ? (
        <button type="button" data-testid="memory-retry-3d" onClick={() => {
          setCapability((current) => applyRetry3d(current, true));
          setForceCompat(false);
        }}>
          重试 3D
        </button>
      ) : null}

      <div
        className="memory-constellation-host"
        ref={graphRef}
        data-testid="memory-graph"
        tabIndex={listOwnsFocus ? -1 : 0}
        role="group"
        aria-label="记忆星座仪"
        onFocus={() => setListOwnsFocus(false)}
      >
        {liveProjection && renderSurface === '3d' ? (
          <ConstellationStage
            key={runtimeGeneration}
            projection={liveProjection}
            selectedId={selectedId}
            mode={cameraMode}
            reducedMotion={!ambientMotionEnabled(reducedMotion, renderSurface)}
            decoration={capability.decoration}
            expanded={overviewOpen}
            expandedTopicIds={expandedTopicIds}
            worldline={worldline}
            query={criteria.query}
            onSelect={handleSelect}
            onSelectedAnchor={(next) => {
              setAnchor((prev) => {
                if (!next && !prev) return prev;
                if (next && prev && next.x === prev.x && next.y === prev.y) return prev;
                return next;
              });
            }}
            onCreateFail={() => setCapability((current) => applyCreateFailure(current))}
            onContextLost={() => {
              setCapability((current) => {
                const next = applyContextLoss(current);
                if (next.retrying) setRuntimeGeneration((value) => value + 1);
                return next;
              });
            }}
            onFrame={(sample) => {
              framesRef.current = [...framesRef.current.slice(-240), sample];
              setCapability((current) => applyPerformanceSample(current, framesRef.current, sample.at));
            }}
          />
        ) : null}
        {liveProjection && renderSurface === 'compatibility' ? (
          <CompatibilityView
            projection={liveProjection}
            selectedId={selectedId}
            focusedId={focusedId}
            onSelect={handleSelect}
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

      {listOpen && liveProjection ? (
        <ol
          className="memory-space-list"
          data-testid="memory-list"
          role="listbox"
          aria-label="记忆列表"
          tabIndex={listOwnsFocus ? 0 : -1}
          onFocus={() => setListOwnsFocus(true)}
        >
          {liveProjection.result_ids.map((id, index) => {
            const node = display.find((item) => item.id === id);
            if (!node) return null;
            const selectedItem = selectedId === id;
            return (
              <li key={id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selectedItem}
                  className={selectedItem ? 'is-selected' : undefined}
                  tabIndex={listOwnsFocus && selectedItem ? 0 : -1}
                  onClick={() => {
                    const parent = evidenceParentId(id, liveProjection.edges);
                    if (parent) revealTopic(parent);
                    setSelectedId(id);
                    setFocusedId(id);
                    setListOwnsFocus(true);
                    setAnnouncement(announceNode(liveProjection, id));
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'ArrowDown') {
                      event.preventDefault();
                      const next = liveProjection.result_ids[index + 1];
                      if (next) handleSelect(next);
                    }
                    if (event.key === 'ArrowUp') {
                      event.preventDefault();
                      const prev = liveProjection.result_ids[index - 1];
                      if (prev) handleSelect(prev);
                    }
                  }}
                >
                  {node.label}
                </button>
              </li>
            );
          })}
        </ol>
      ) : null}

      {inspectorOpen && liveProjection ? (
        <aside className="memory-inspector" data-testid="soul-inspector">
          <h2>AMADEUS / SOUL</h2>
          <p>SOUL 是当前 Amadeus 记忆连续性的投影中心，不是一条可编辑的记忆。</p>
          <dl data-testid="inspector-composition">
            <div><dt>稳定事实</dt><dd>{liveProjection.composition.active_facts}</dd></div>
            <div><dt>经历</dt><dd>{liveProjection.composition.active_experiences}</dd></div>
            <div><dt>主题</dt><dd>{liveProjection.composition.eligible_topics}</dd></div>
            <div>
              <dt>最近变更</dt>
              <dd data-testid="inspector-latest-change">
                {liveProjection.composition.latest_memory_change_at ?? '无'}
              </dd>
            </div>
          </dl>
          <dl data-testid="inspector-projection-counts">
            <div>
              <dt>结果</dt>
              <dd>{liveProjection.shown.results} / {liveProjection.eligible.results}</dd>
            </div>
            <div>
              <dt>记录</dt>
              <dd>{liveProjection.shown.records} / {liveProjection.eligible.records}</dd>
            </div>
            <div>
              <dt>节点</dt>
              <dd>{liveProjection.shown.nodes} / {liveProjection.eligible.nodes}</dd>
            </div>
            <div>
              <dt>边</dt>
              <dd>{liveProjection.shown.edges} / {liveProjection.eligible.edges}</dd>
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
                  {liveProjection.scope.session_id} / {liveProjection.scope.worldline} / {liveProjection.scope.identity_mode}
                </dd>
              </div>
              <div><dt>版本</dt><dd>{liveProjection.projection_version}</dd></div>
              <div>
                <dt>预算</dt>
                <dd>
                  nodes {liveProjection.budgets.nodes}/{liveProjection.budgets.hard_max_nodes} ·
                  edges {liveProjection.budgets.edges}/{liveProjection.budgets.hard_max_edges}
                </dd>
              </div>
              <div>
                <dt>截断</dt>
                <dd>
                  nodes={String(liveProjection.truncated.nodes)} edges={String(liveProjection.truncated.edges)}{' '}
                  records={String(liveProjection.truncated.records)} results={String(liveProjection.truncated.results)}
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
