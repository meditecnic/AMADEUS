import { MemorySelect } from './MemorySelect';
import { motion } from 'motion/react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { ArchiveView } from './ArchiveView';
import { parseMemoryStatusBody } from './archiveApi';
import { createArchiveModule, type ArchiveModule } from './archiveState';
import { MemoryResultOutline } from './MemoryResultOutline';
import { deriveOverviewPresentation, outlineIds } from './overviewPresentation';
import { memorySpaceShortcut } from './chrome';
import { ConstellationStage } from './ConstellationStage';
import { SoulInteriorPrototype } from './SoulInteriorPrototype';
import type { PresentationChangeCause } from './overviewMotion';
import {
  arbitrateMotionDelivery,
  splitOrderedCauses,
  type MotionPresentationSnapshot,
} from './motionCause';
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
import { nextMemoryUnwind } from './memoryUnwind';
import { layoutProjection } from './layout';
import { classifyOverviewState } from './overviewState';
import { createProjectionController } from './projectionController';
import { ReadingProvenance, readingCardFromNode } from './readingContent';
import {
  createDetailsController,
  deriveExperienceReading,
  deriveFactReading,
  formatTimeHuman,
  NOT_FOUND_NOTICE,
  type DetailsCache,
  type DetailsOutcome,
  type ExperienceReadingModel,
  type FactReadingModel,
} from './readingDetails';
import {
  HEALTHY_CAPABILITY,
  applyContextLoss,
  applyCreateFailure,
  applyFrameSample,
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
import type { IdentityMode, MemoryProjection, Worldline } from './types';

export type MemorySpaceProps = {
  sessionId: string;
  chatWorldline: Worldline;
  chatIdentityMode: IdentityMode;
  onClose: () => void;
};

// S4-ARCHIVE Gate 2: Memory Space gains two mutually exclusive peer modes.
// Constellation stays the default; Archive is the exact-scope record browser
// and governance owner. Each mode keeps its own per-scope in-session state.
export type MemorySpaceMode = 'constellation' | 'archive';

type JobSummary = {
  runtime_mode?: 'legacy' | 'shadow' | 'v11';
  // The v11 status payload echoes the exact scope as top-level fields; the
  // direct /api/memory/status read is fail-closed against this echo.
  session_id?: string;
  worldline?: string;
  identity_mode?: string;
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
  // S4-ARCHIVE Gate 2: one shared P1R-3 details cache. Archive governance
  // purges the SAME entries the Constellation reading controller retains.
  const detailsCacheRef = useRef<DetailsCache>(new Map());
  const detailsControllerRef = useRef(
    createDetailsController({ cache: detailsCacheRef.current }),
  );
  // The Archive module survives mode switches so committed Archive state and
  // unsaved drafts are never lost by moving to Constellation and back.
  const archiveModuleRef = useRef<ArchiveModule | null>(null);
  // P1R-3 outcome mirror: lets the Archive erasure callback inspect the
  // active reading request without effect timing.
  const detailsOutcomeRef = useRef<DetailsOutcome>({ type: 'idle' });
  // Backend-authoritative projection refresh counter: bumped when Archive
  // erases a record so Constellation re-reads the projection seam. The
  // result_ids are never edited client-side.
  const [projectionRefreshTick, setProjectionRefreshTick] = useState(0);
  // Erasure refresh barrier: until the authoritative post-erasure projection
  // arrives, the previous envelope is NOT rendered — no old LIST row, reading
  // card, or projection summary may survive a backend erasure. A failed
  // refresh drops the stale envelope instead of restoring it.
  const [erasureSyncing, setErasureSyncing] = useState(false);
  const erasureSyncRef = useRef(false);
  const handleArchiveRecordErased = useCallback(
    (kind: 'fact' | 'experience', recordId: string) => {
      const active = detailsOutcomeRef.current;
      if (
        active.type !== 'idle'
        && active.request.kind === kind
        && active.request.recordId === recordId
      ) {
        detailsControllerRef.current.invalidate();
        detailsOutcomeRef.current = { type: 'idle' };
        setDetails({ type: 'idle' });
        setActivationKey((current) =>
          current !== null && current.endsWith(`|${kind}|${recordId}`) ? null : current,
        );
      }
      setProjectionRefreshTick((tick) => tick + 1);
      erasureSyncRef.current = true;
      setErasureSyncing(true);
    },
    [],
  );
  if (!archiveModuleRef.current) {
    archiveModuleRef.current = createArchiveModule({
      detailsCache: detailsCacheRef.current,
      onRecordErased: handleArchiveRecordErased,
      // §4.14: the peer shell may only switch away after Archive's explicit
      // discard confirmation resolved the pending draft.
      onExitArchive: () => setMode('constellation'),
      onScopeChangeSettled: () => {
        archiveOpenedRef.current = scopeKeyRef.current;
      },
    });
  }
  const archiveOpenedRef = useRef<string | null>(null);
  const [mode, setMode] = useState<MemorySpaceMode>('constellation');
  const [details, setDetails] = useState<DetailsOutcome>({ type: 'idle' });
  // P1 spec-1/2 (round 2): activation is owned by explicit user gestures
  // (Enter / Space / click), never by focus movement. The key encodes
  // {scope, kind, recordId} of the LAST activated evidence record.
  const [activationKey, setActivationKey] = useState<string | null>(null);
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
  const [soulOpen, setSoulOpen] = useState(false);
  const [soulReading, setSoulReading] = useState(false);
  useEffect(() => { if (!soulOpen) setSoulReading(false); }, [soulOpen]);
  const [dismissedPanel, setDismissedPanel] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [listOwnsFocus, setListOwnsFocus] = useState(false);
  const [fallbackDetailOpen, setFallbackDetailOpen] = useState(false);
  const [capability, setCapability] = useState<CapabilityState>(HEALTHY_CAPABILITY);
  const [runtimeGeneration, setRuntimeGeneration] = useState(0);
  const [overviewOpen] = useState(true);
  const [expandedTopicIds, setExpandedTopicIds] = useState<string[]>([]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const searchRef = useRef<HTMLInputElement | null>(null);
  const findToggleRef = useRef<HTMLButtonElement | null>(null);
  const readingPanelRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const listWasShown = useRef(false);
  const [narrowPrefer, setNarrowPrefer] = useState<'list' | 'detail'>('detail');
  const framesRef = useRef<FrameSample[]>([]);
  const [width, setWidth] = useState(typeof window === 'undefined' ? 1280 : window.innerWidth);
  const [reducedMotion, setReducedMotion] = useState(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });
  const [motionCause, setMotionCause] = useState<PresentationChangeCause>('viewport');
  const [motionCauses, setMotionCauses] = useState<PresentationChangeCause[]>([]);
  const [motionEpoch, setMotionEpoch] = useState(0);
  const motionNotes = useRef<PresentationChangeCause[]>([]);
  const scopeNote = useRef(false);
  const prevMotionSnap = useRef<MotionPresentationSnapshot | null>(null);
  const noteMotion = (cause: PresentationChangeCause) => {
    motionNotes.current.push(cause);
  };

  useEffect(() => {
    setBrowseSessionId(sessionId);
  }, [sessionId]);

  const scope: ScopeKey = useMemo(
    () => ({ sessionId: browseSessionId, worldline, identityMode }),
    [browseSessionId, identityMode, worldline],
  );

  useEffect(() => {
    const onResize = () => {
      noteMotion('viewport');
      setWidth(window.innerWidth);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => {
      noteMotion('motion-preference');
      setReducedMotion(media.matches);
    };
    media.addEventListener?.('change', onChange);
    return () => media.removeEventListener?.('change', onChange);
  }, []);

  useEffect(() => () => {
    controllerRef.current.dispose();
    detailsControllerRef.current.deactivate();
    archiveModuleRef.current?.dispose();
    // Disposing invalidates the module's open lifecycle: forget the open
    // marker so a StrictMode remount (or any remount reusing this ref)
    // re-issues open() and re-arms the SAME module instance.
    archiveOpenedRef.current = null;
  }, []);

  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;
  const focusedRef = useRef<string | null>(null);
  focusedRef.current = focusedId;

  const applyEnvelope = useCallback((next: NonNullable<typeof projection>, keepError = false) => {
    setProjection(next);
    setPreviousTruth(next);
    if (!keepError) setError(null);
    const previousSelected = selectedRef.current;
    const reconciled = reconcileSelection(selectedRef.current, next);
    // P1 (round 3): an envelope-driven selection change invalidates the
    // previous evidence activation.
    if (reconciled.selectedId !== previousSelected) setActivationKey(null);
    setSelectedId(reconciled.selectedId);
    const focused = reconcileSelection(focusedRef.current, next);
    setFocusedId(focused.selectedId);
    if (reconciled.fellBack && reconciled.announced) {
      setAnnouncement(reconciled.announced);
    }
    noteMotion(reconciled.fellBack ? 'post-reconcile-replacement' : 'disclosure');
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const prior = controllerRef.current.lastSuccessful();
    const switched = Boolean(prior && (
      prior.scope.sessionId !== scope.sessionId
      || prior.scope.worldline !== scope.worldline
      || prior.scope.identityMode !== scope.identityMode
    ));
    setScopeSwitching(switched);
    if (switched) scopeNote.current = true;
    void controllerRef.current.load(scope, criteria).then((outcome) => {
      if (cancelled) return;
      if (outcome.type === 'stale') return;
      if (outcome.type === 'ok') {
        // The authoritative envelope arrived: the erasure barrier lifts and
        // the NEW envelope replaces every readable trace.
        erasureSyncRef.current = false;
        setErasureSyncing(false);
        applyEnvelope(outcome.envelope);
        setLoading(false);
        setScopeSwitching(false);
        return;
      }
      if (erasureSyncRef.current) {
        // Barrier active and the refresh did NOT deliver a new envelope:
        // never restore the stale previous/restore envelope — the deleted
        // record would resurrect. Drop the stale envelope and surface the
        // error honestly instead.
        erasureSyncRef.current = false;
        setErasureSyncing(false);
        setProjection(null);
        setPreviousTruth(null);
        setError(outcome.error);
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
      .then(async (response) => (response.ok ? (response.json() as Promise<unknown>) : null))
      .then((body) => {
        if (cancelled || !body) return;
        // Fail-closed (hard invariant 1): the SAME strict shared validator
        // used by Archive checks the scope echo AND the payload shape
        // (failed_jobs array, count fields, exact failed-job row contract).
        // A rejected summary is discarded, never defaulted to zeros.
        const parsed = parseMemoryStatusBody(body, {
          sessionId,
          worldline,
          identityMode,
        });
        if (!parsed) return;
        setJobs({
          runtime_mode: parsed.runtimeMode,
          session_id: sessionId,
          worldline,
          identity_mode: identityMode,
          pending_count: parsed.pendingCount,
          processing_count: parsed.processingCount,
          failed_count: parsed.failedCount,
        });
      })
      .catch(() => {
        /* operational summary is optional */
      });
    return () => {
      cancelled = true;
    };
  }, [applyEnvelope, criteria, scope, projectionRefreshTick]);

  useEffect(() => {
    if (!listOpen && capability.surface !== 'list') return undefined;
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
  }, [capability.surface, identityMode, listOpen, sessionId, worldline]);

  const presentation = classifyOverviewState({
    loading,
    scopeSwitching,
    envelope: projection,
    previousTruth,
    error,
    requestedCriteria: criteria,
  });

  // While the post-erasure refresh is pending the previous envelope is
  // withheld from every reader: LIST, reading card, inspector and summary
  // all derive from liveProjection, so they cannot show stale content.
  const liveProjection = erasureSyncing ? null : presentation.envelope;
  // P1-A (round 4): derivations are memoized so a parent re-render (e.g. an
  // anchor update during drag) does not hand ConstellationStage fresh
  // object identities and retrigger its apply/publish effect chain.
  const disclosurePlan = useMemo(
    () => (liveProjection
      ? deriveOverviewPresentation(liveProjection, {
        expandedTopicIds,
        selectedId,
        focusedId,
        query: criteria.query,
      })
      : null),
    [liveProjection, expandedTopicIds, selectedId, focusedId, criteria.query],
  );
  const planReady = Boolean(disclosurePlan?.valid);
  const display = useMemo(
    () => (liveProjection ? layoutProjection(liveProjection) : []),
    [liveProjection],
  );
  const selected = display.find((node) => node.id === selectedId) ?? null;
  const card = useMemo(
    () => (selected ? readingCardFromNode(selected, liveProjection) : null),
    [selected, liveProjection],
  );
  const scopeKey = `${browseSessionId}|${worldline}|${identityMode}`;
  useEffect(() => { setSoulOpen(false); }, [scopeKey, mode]);

  // S4-ARCHIVE Gate 2: opening Archive happens once per (mode, scope) pair;
  // deep-link opens mark the pair themselves so a focus open is never
  // clobbered by the restore open.
  const scopeKeyRef = useRef(scopeKey);
  scopeKeyRef.current = scopeKey;
  useEffect(() => {
    if (mode !== 'archive') return;
    if (archiveOpenedRef.current === scopeKey) return;
    // A pending draft defers the scope switch behind the explicit discard
    // confirmation; the module settles it via onScopeChangeSettled.
    if (!archiveModuleRef.current?.open(scope, { restore: true })) return;
    archiveOpenedRef.current = scopeKey;
  }, [mode, scope, scopeKey]);

  const openArchiveForRecord = (kind: 'fact' | 'experience', recordId: string) => {
    archiveOpenedRef.current = scopeKey;
    setMode('archive');
    archiveModuleRef.current?.open(scope, { focus: { kind, recordId } });
  };


  // P1R-3 §5: activated Fact/Experience selections lazily load scoped
  // details through the frozen endpoints. Pure rove (focus without
  // activation) never reaches this effect because selectedId is unchanged.
  const selectedProjectionNode = selectedId
    ? liveProjection?.nodes.find((node) => node.projection_id === selectedId) ?? null
    : null;
  const detailsRecordId =
    selectedProjectionNode?.kind === 'fact'
      ? selectedProjectionNode.fact_id
      : selectedProjectionNode?.kind === 'experience'
        ? selectedProjectionNode.experience_id
        : null;
  const detailsKey = detailsRecordId && selectedProjectionNode
    ? `${scopeKey}|${selectedProjectionNode.kind}|${detailsRecordId}`
    : null;

  // P1 spec-2: never combine the new browse scope with a stale envelope.
  const scopeFresh = Boolean(
    liveProjection
    && liveProjection.scope.session_id === browseSessionId
    && liveProjection.scope.worldline === worldline
    && liveProjection.scope.identity_mode === identityMode,
  );

  // P1 spec-2 (round 2 / round 3): render-time alignment. A payload only
  // renders when its request identity equals the CURRENT activated
  // {scope, kind, recordId} AND that activation still matches the current
  // selection-derived details key; any mismatch is treated as idle
  // synchronously, without waiting for the effect to clean up.
  const detailsAligned = (() => {
    if (details.type === 'idle') return true;
    const req = details.request;
    const requestId = `${req.scope.sessionId}|${req.scope.worldline}|${req.scope.identityMode}|${req.kind}|${req.recordId}`;
    return scopeFresh
      && activationKey !== null
      && requestId === activationKey
      && activationKey === detailsKey;
  })();
  const effectiveDetails: DetailsOutcome = detailsAligned ? details : { type: 'idle' };

  const renderSurface = capability.surface;
  const fallbackList = renderSurface === 'list';
  const listVisible = listOpen || fallbackList;
  const panelVisible = dismissedPanel !== `${scopeKey}|${selectedId}`;
  const dismissPanel = () => {
    setDismissedPanel(`${scopeKey}|${selectedId}`);
    if (width < 900 && listVisible) setNarrowPrefer('list');
  };
  const inspectorOpen = panelVisible && overviewOpen && selected?.kind === 'continuity_hub';
  const memoOpen = panelVisible && Boolean(card) && overviewOpen;
  const phase = overviewOpen ? 'overview' : 'soul';
  const lockup = soulLockupLines(
    liveProjection?.center.label_primary ?? 'AMADEUS',
    liveProjection?.center.label_secondary ?? 'SOUL',
  );
  const chips = useMemo(() => criteriaChips(liveProjection ? {
    query: liveProjection.criteria.query ?? '',
    kinds: liveProjection.criteria.kinds,
    topicId: liveProjection.criteria.topic_id,
    pinnedOnly: liveProjection.criteria.pinned_only,
    updatedFrom: liveProjection.criteria.updated_from,
    updatedTo: liveProjection.criteria.updated_to,
  } : criteria), [liveProjection, criteria]);
  const order = useMemo(
    () => (liveProjection ? semanticOrder(liveProjection) : []),
    [liveProjection],
  );

  const revealTopic = (topicId: string) => {
    setExpandedTopicIds((current) => openTopicBranch(topicId, current));
  };

  const handleSelect = (id: string | null, mode: 'rove' | 'activate' = 'activate') => {
    if (soulOpen) return;
    if (mode === 'activate') setDismissedPanel(null);
    // P1 spec-1: rove moves focus only. Activation (Enter / Space / click)
    // is the sole owner of selectedId and therefore of details loading.
    if (mode === 'rove') {
      if (!id) return;
      const roveHit = display.find((node) => node.id === id);
      if (!roveHit) return;
      setFocusedId(roveHit.id);
      if (liveProjection) setAnnouncement(announceNode(liveProjection, roveHit.id));
      return;
    }
    if (mode === 'activate') {
      setNarrowPrefer('detail');
    }
    if (!id) {
      setSelectedId(null);
      setFocusedId(null);
      setExpandedTopicIds([]);
      setActivationKey(null);
      noteMotion('disclosure');
      return;
    }
    const hit = display.find((node) => node.id === id);
    if (!hit) {
      const fallback = reconcileSelection(id, liveProjection);
      setSelectedId(fallback.selectedId);
      setFocusedId(fallback.selectedId);
      setActivationKey(null);
      if (fallback.announced) setAnnouncement(fallback.announced);
      noteMotion('post-reconcile-replacement');
      return;
    }
    noteMotion(id === selectedId ? 'reselect' : 'selection');
    setFocusedId(hit.id);
    if (hit.kind === 'continuity_hub') {
      setSelectedId(hit.id);
      setActivationKey(null);
      return;
    }
    if (hit.kind === 'topic') {
      revealTopic(hit.id);
      noteMotion('disclosure');
      setSelectedId(hit.id);
      setActivationKey(null);
      return;
    }
    const parent = evidenceParentId(hit.id, liveProjection?.edges ?? []);
    if (parent) {
      revealTopic(parent);
      noteMotion('disclosure');
    }
    setSelectedId(hit.id);
    const activatedRecordId = hit.node.kind === 'fact'
      ? hit.node.fact_id
      : hit.node.kind === 'experience'
        ? hit.node.experience_id
        : null;
    setActivationKey(activatedRecordId ? `${scopeKey}|${hit.kind}|${activatedRecordId}` : null);
    if (liveProjection) setAnnouncement(announceNode(liveProjection, hit.id));
  };

  const moveSemantic = (delta: number) => {
    // P1 spec-1 (round 2): canvas arrows rove only; Enter / Space activate.
    const next = neighborId(order, focusedId ?? selectedId, delta);
    if (next) handleSelect(next, 'rove');
  };

  const roveList = (nextId: string) => {
    if (!nextId) return;
    handleSelect(nextId, 'rove');
    setListOwnsFocus(true);
    listRef.current?.querySelector<HTMLElement>(`[data-outline-id="${nextId}"]`)?.focus();
  };

  const emphasis = useMemo(() => emphasisMode({
    query: criteria.query,
    resultIds: liveProjection?.result_ids ?? [],
    hoverId: null,
    selectedId,
  }), [criteria.query, liveProjection, selectedId]);

  const changeCriteria = (next: CriteriaInput) => {
    setCriteria(normalizeCriteria(next));
  };

  const toggleList = () => {
    noteMotion('auxiliary-surface');
    if (fallbackList) {
      setFallbackDetailOpen(false);
      setNarrowPrefer('list');
      return;
    }
    if (listOpen && width < 900 && narrowPrefer !== 'list') {
      setListOpen(true);
      setNarrowPrefer('list');
      return;
    }
    setListOpen((value) => !value);
    setNarrowPrefer('list');
  };

  const retryProjection = () => {
    setError(null);
    setLoading(true);
    setCriteria((current) => ({ ...current }));
  };

  const returnToOverview = () => {
    setSoulOpen(false);
    setSelectedId(null);
    setFocusedId(null);
    setExpandedTopicIds([]);
    setActivationKey(null);
    setListOpen(false);
    setFiltersOpen(false);
    noteMotion('reselect');
    noteMotion('disclosure');
  };

  const unwind = (opts?: { closeAtRoot?: boolean }) => {
    if (soulOpen && soulReading) { setSoulReading(false); return; }
    if (soulOpen) { setSoulOpen(false); return; }
    // Semantic Back in Archive mode: editor/confirm → detail → list → close.
    if (mode === 'archive') {
      if (archiveModuleRef.current?.handleBack()) return;
      if (opts?.closeAtRoot !== false) onClose();
      return;
    }
    const selectedKind = selected?.kind
      ?? liveProjection?.nodes.find((node) => node.projection_id === selectedId)?.kind
      ?? null;

    if (width < 900 && listVisible && narrowPrefer !== 'list' && (memoOpen || inspectorOpen || fallbackDetailOpen)) {
      setFallbackDetailOpen(false);
      setNarrowPrefer('list');
      noteMotion('auxiliary-surface');
      return;
    }

    if (fallbackList && !hasActiveCriteria(criteria)) {
      if (narrowSheet !== 'list') {
        setFallbackDetailOpen(false);
        setNarrowPrefer('list');
        noteMotion('auxiliary-surface');
        return;
      }
      if (opts?.closeAtRoot !== false) onClose();
      return;
    }

    const action = nextMemoryUnwind({
      toolsOpen: false,
      hasActiveCriteria: hasActiveCriteria(criteria),
      listVisible: Boolean(listOpen && !fallbackList && (width >= 900 || narrowPrefer === 'list')),
      selectedKind: selectedKind === 'fact' || selectedKind === 'experience' || selectedKind === 'topic' || selectedKind === 'continuity_hub'
        ? selectedKind
        : null,
      expanded: expandedTopicIds.length > 0,
      closeAtRoot: opts?.closeAtRoot !== false,
    });
    if (action === 'clear-criteria') {
      changeCriteria(EMPTY_CRITERIA);
      noteMotion('auxiliary-surface');
      return;
    }
    if (action === 'close-tools') {
      setFiltersOpen(false);
      noteMotion('auxiliary-surface');
      return;
    }
    if (action === 'close-list') {
      setListOpen(false);
      noteMotion('auxiliary-surface');
      return;
    }
    if (action === 'evidence-to-topic') {
      const parent = selectedId
        ? evidenceParentId(selectedId, liveProjection?.edges ?? [])
        : null;
      setSelectedId(parent);
      setFocusedId(parent);
      setExpandedTopicIds(parent ? [parent] : []);
      setActivationKey(null);
      noteMotion('selection');
      noteMotion('disclosure');
      return;
    }
    if (action === 'close-inspector') {
      setSelectedId(null);
      setFocusedId(null);
      noteMotion('selection');
      return;
    }
    if (action === 'topic-to-overview') {
      setSelectedId(null);
      setFocusedId(null);
      setExpandedTopicIds([]);
      setActivationKey(null);
      noteMotion('selection');
      noteMotion('disclosure');
      return;
    }
    if (action === 'collapse-branch') {
      setExpandedTopicIds([]);
      setActivationKey(null);
      noteMotion('disclosure');
      return;
    }
    if (action === 'close-memory') onClose();
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
      if (soulOpen) return;
      if (inField) return;
      const shortcut = memorySpaceShortcut(event);
      if (shortcut === 'search') {
        event.preventDefault();
        setListOpen(true);
        setNarrowPrefer('list');
        window.requestAnimationFrame(() => searchRef.current?.focus());
        return;
      }
      if (shortcut === 'list') {
        toggleList();
        return;
      }
      const targetEl = event.target instanceof Element ? event.target : null;
      const nativeInteractive = Boolean(
        targetEl?.closest('button, summary, a, input, select, textarea, [contenteditable="true"]'),
      );
      if (nativeInteractive) {
        if (event.key === 'Home' || event.key === 'Enter'
          || event.key === 'ArrowDown' || event.key === 'ArrowUp'
          || event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          return;
        }
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
      // P1 spec-1: Space activates the focused node outside native controls;
      // native buttons keep their own Space-click activation path.
      if (event.key === ' ' && focusedId && !nativeInteractive) {
        event.preventDefault();
        handleSelect(focusedId);
        return;
      }
      const fromListOption = Boolean(
        targetEl?.getAttribute('role') === 'option'
        && listRef.current?.contains(targetEl),
      );
      if (fromListOption && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        return;
      }
      if (fromListOption && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
        return;
      }
      const optionFocused = document.activeElement?.getAttribute('role') === 'option'
        && Boolean(listRef.current?.contains(document.activeElement));
      if (listOwnsFocus && optionFocused) return;
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

  const listIds = disclosurePlan ? outlineIds(disclosurePlan) : [];
  const listRovingId = (selectedId && listIds.includes(selectedId))
    ? selectedId
    : (focusedId && listIds.includes(focusedId) ? focusedId : (listIds[0] ?? null));

  const narrowSheet = (() => {
    if (fallbackList) {
      if (fallbackDetailOpen && inspectorOpen) return 'inspector';
      if (fallbackDetailOpen && memoOpen) return 'reading';
      return 'list';
    }
    if (width < 900) {
      if (listVisible && narrowPrefer === 'list') return 'list';
      if (inspectorOpen) return 'inspector';
      if (memoOpen) return 'reading';
      if (listVisible) return 'list';
      return 'none';
    }
    if (inspectorOpen) return 'inspector';
    if (memoOpen) return 'reading';
    if (listVisible) return 'list';
    return 'none';
  })();

  const findMounted = Boolean(mode === 'constellation' && listVisible);
  const showList = Boolean(
    findMounted
    && (width >= 900 || narrowSheet === 'list'),
  );
  const showInspector = Boolean(
    mode === 'constellation'
    && inspectorOpen
    && liveProjection
    && (width >= 900 || narrowSheet === 'inspector'),
  );
  const showReading = Boolean(
    mode === 'constellation'
    && memoOpen
    && card
    && (width >= 900 || narrowSheet === 'reading'),
  );
  const readingResults = planReady
    ? disclosurePlan?.primaryResults.filter((row) => row.kind === 'fact' || row.kind === 'experience') ?? []
    : [];
  const readingIndex = readingResults.findIndex((row) => row.id === selectedId);
  useLayoutEffect(() => {
    if (readingPanelRef.current) readingPanelRef.current.scrollTop = 0;
  }, [selectedId]);

  // P2-B (round 4): honest terminal announcement. When the reading surface
  // is visible at resolution time we say "证据已读取"; otherwise we say the
  // payload is loaded and where to read it. One announcement per
  // (record, outcome) — never a misleading "read" while the surface is
  // covered by LIST/tools/inspector.
  const lastAnnouncedRef = useRef<string | null>(null);
  useEffect(() => {
    const eff = effectiveDetails;
    if (eff.type === 'loading') {
      // P2 (round 5): a fresh attempt re-arms the terminal announcement so a
      // repeated failure after retry is never swallowed by the dedupe marker.
      lastAnnouncedRef.current = null;
      return;
    }
    if (
      eff.type !== 'ok'
      && eff.type !== 'not-found'
      && eff.type !== 'backend-failure'
      && eff.type !== 'transport-error'
    ) {
      return;
    }
    const req = eff.request;
    const marker = `${req.scope.sessionId}|${req.scope.worldline}|${req.scope.identityMode}|${req.kind}|${req.recordId}|${eff.type}`;
    if (lastAnnouncedRef.current === marker) return;
    lastAnnouncedRef.current = marker;
    if (eff.type === 'ok') {
      setAnnouncement(showReading ? '证据已读取' : '证据已载入，可在阅读面查看');
    } else if (eff.type === 'not-found') {
      setAnnouncement(NOT_FOUND_NOTICE);
    } else {
      setAnnouncement('证据暂时不可用');
    }
    // effectiveDetails identity changes with details/scope; visibility flags
    // intentionally do not retrigger a second announcement.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveDetails]);

  useEffect(() => {
    const opened = showList && !listWasShown.current;
    listWasShown.current = showList;
    if (!showList) {
      setListOwnsFocus(false);
      return undefined;
    }
    if (!opened) return undefined;
    const preferred = listRovingId;
    const frame = window.requestAnimationFrame(() => {
      if (!preferred) {
        listRef.current?.focus();
        return;
      }
      const option = listRef.current?.querySelector<HTMLElement>(`[data-outline-id="${preferred}"]`);
      option?.focus();
      if (option && document.activeElement === option) {
        setFocusedId(preferred);
        setListOwnsFocus(true);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [showList, listRovingId]);

  const truncated = Boolean(
    liveProjection
    && (liveProjection.truncated.results || liveProjection.shown.results < liveProjection.eligible.results),
  );

  // P1 spec-4: details transitions enter the live region.
  // P2-B (round 4): loading is announced here; terminal wording is decided
  // by the announcement effect below, which knows whether the reading
  // surface is actually visible when the outcome lands.
  const updateDetails = useCallback((outcome: DetailsOutcome) => {
    detailsOutcomeRef.current = outcome;
    setDetails(outcome);
    // P2 spec-3 (round 2): loading is announced quietly as well.
    if (outcome.type === 'loading') setAnnouncement('正在读取证据');
  }, []);

  useEffect(() => {
    const controller = detailsControllerRef.current;
    if (
      !scopeFresh
      || !activationKey
      || activationKey !== detailsKey
      || !detailsKey
      || !selectedProjectionNode
      || !detailsRecordId
      || (selectedProjectionNode.kind !== 'fact' && selectedProjectionNode.kind !== 'experience')
    ) {
      controller.deactivate();
      setDetails({ type: 'idle' });
      return;
    }
    const request = {
      scope: { sessionId: browseSessionId, worldline, identityMode },
      kind: selectedProjectionNode.kind,
      recordId: detailsRecordId,
    };
    const initial = controller.activate(request, updateDetails);
    updateDetails(initial);
    // detailsKey already encodes scope, kind, and canonical record id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailsKey, activationKey, scopeFresh]);

  // P1 (round 3): any scope change invalidates every previous activation;
  // a new scope always requires a fresh Enter / Space / click gesture.
  // P2-A (round 4): the retained payload is dropped on scope change too.
  useEffect(() => {
    setActivationKey(null);
    detailsControllerRef.current.invalidate();
  }, [scopeKey]);

  const retryDetails = () => {
    if (details.type !== 'backend-failure' && details.type !== 'transport-error') return;
    // Explicit retry bypasses the retained payload per contract.
    const initial = detailsControllerRef.current.activate(details.request, updateDetails, { force: true });
    updateDetails(initial);
  };

  const readingModel: FactReadingModel | ExperienceReadingModel | null = (() => {
    if (effectiveDetails.type !== 'ok' || !selectedProjectionNode || !liveProjection) return null;
    const payload = effectiveDetails.payload;
    if (selectedProjectionNode.kind === 'fact' && 'fact' in payload) {
      return deriveFactReading(payload, liveProjection, {
        projectionId: selectedProjectionNode.projection_id,
        topicId: selectedProjectionNode.topic_id,
      });
    }
    if (selectedProjectionNode.kind === 'experience' && 'experience' in payload) {
      return deriveExperienceReading(payload);
    }
    return null;
  })();
  const viewportHeight = typeof window === 'undefined' ? 800 : window.innerHeight;
  const narrowFindHeight = viewportHeight <= 680 ? Math.max(0, viewportHeight - 116) : viewportHeight * (filtersOpen ? 0.72 : 0.62);
  const motionInsets = {
    top: width < 900 ? 104 : 64,
    right: width >= 900 && (showReading || showInspector) ? 416 : 16,
    bottom: narrowSheet !== 'none' && width < 900
      ? Math.round(narrowSheet === 'list' ? narrowFindHeight : viewportHeight * (narrowSheet === 'reading' ? 0.62 : 0.42))
      : 16,
    left: width >= 900 && showList ? 336 : 16,
  };

  useLayoutEffect(() => {
    const snap: MotionPresentationSnapshot = {
      scopeKey,
      selectedId,
      expandedTopicIds,
      planIdentity: `${liveProjection?.generated_at ?? ''}|${liveProjection?.result_ids.join(',') ?? ''}`,
      renderSurface,
      auxiliary: narrowSheet,
      reducedMotion,
      viewport: { width, height: typeof window === 'undefined' ? 800 : window.innerHeight },
      insets: motionInsets,
    };
    const notes = motionNotes.current;
    const delivery = arbitrateMotionDelivery(
      prevMotionSnap.current,
      snap,
      notes,
      scopeNote.current,
    );
    motionNotes.current = [];
    scopeNote.current = false;
    prevMotionSnap.current = snap;
    if (delivery.type === 'none') return;
    if (delivery.type === 'scopeChanged') {
      setMotionEpoch((value) => value + 1);
      return;
    }
    setMotionCause(delivery.cause);
    setMotionCauses(splitOrderedCauses(notes));
    setMotionEpoch((value) => value + 1);
  }, [
    expandedTopicIds,
    identityMode,
    liveProjection,
    motionInsets.bottom,
    motionInsets.left,
    motionInsets.right,
    motionInsets.top,
    narrowSheet,
    reducedMotion,
    renderSurface,
    scopeKey,
    selectedId,
    width,
    worldline,
  ]);

  return (
    <section
      className="memory-space"
      data-testid="memory-space"
      data-soul-open={String(soulOpen)}
      data-phase={phase}
      data-expanded-topics={expandedTopicIds.join(',')}
      data-selected-id={selectedId ?? ''}
      data-selected-kind={selected?.kind ?? ''}
      data-worldline={worldline}
      data-emphasis={emphasis}
      data-surface={renderSurface}
      data-reduced-motion={String(reducedMotion)}
      data-narrow-sheet={narrowSheet}
      data-motion-cause={motionCause}
      data-find-open={String(listOpen)}
      data-tools-open={String(filtersOpen)}
      aria-label="Memory Space"
      onContextMenu={(event) => {
        if (mode !== 'constellation') return;
        const target = event.target as HTMLElement;
        if (target.closest('input, textarea, select, [contenteditable="true"]') || window.getSelection()?.toString()) return;
        event.preventDefault();
        unwind({ closeAtRoot: false });
      }}
    >
      <div className="sr-only" role="status" aria-live="polite" data-testid="memory-announce">{announcement}</div>
      <header className="memory-space-veil" data-testid="memory-space-veil" data-chrome="compact">
        <button
          type="button"
          className="memory-space-return"
          data-testid="memory-back"
          onClick={() => unwind({ closeAtRoot: true })}
        >
          {!selectedId && !expandedTopicIds.length && !listOpen && !hasActiveCriteria(criteria) ? '返回' : '回退'}
        </button>
        <button
          type="button"
          className="memory-space-mark"
          data-testid="select-soul"
          onClick={() => handleSelect(liveProjection?.center.projection_id ?? null)}
        >
          <span className="memory-space-soul-mark">记忆 / SOUL</span>
        </button>
        {mode === 'constellation' && !fallbackList ? (
          <button type="button" className="memory-space-overview" title="保留筛选条件，恢复总览视角" onClick={returnToOverview}>回到总览</button>
        ) : null}
        <span className="memory-space-scope" data-testid="memory-plaque">
          {identityMode === 'self' ? 'USER' : 'OKABE'} · {worldline === 'steins_gate' ? 'SG' : 'β'}
        </span>
        <div className="memory-space-modes" role="group" aria-label="记忆空间模式">
          <button
            type="button"
            data-testid="memory-mode-constellation"
            aria-pressed={mode === 'constellation'}
            onClick={() => {
              // §4.14: leaving Archive with unsaved edits needs the explicit
              // discard choice, handled inside the Archive module.
              if (mode === 'archive' && !archiveModuleRef.current?.handleExitRequest()) return;
              setMode('constellation');
            }}
          >
            <svg className="memory-mode-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 17 7-11 7 9M5 17l14-2" /><circle cx="5" cy="17" r="2" /><circle cx="12" cy="6" r="2" /><circle cx="19" cy="15" r="2" /></svg>
            <span>记忆星图</span>
          </button>
          <button
            type="button"
            data-testid="memory-mode-archive"
            aria-pressed={mode === 'archive'}
            onClick={() => setMode('archive')}
          >
            <svg className="memory-mode-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v14H5zM5 10h14M5 14h14M10 7.5h4M10 12h4M10 16.5h4" /></svg>
            <span>记忆档案</span>
          </button>
          <span className="memory-mode-track" aria-hidden="true">
            <motion.span className="memory-mode-carriage" initial={false}
              animate={{ x: mode === 'archive' ? '100%' : '0%' }}
              transition={reducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 440, damping: 34 }} />
          </span>
        </div>
        {mode === 'constellation' ? (
          <button
            type="button"
            className="memory-space-find-toggle"
            aria-expanded={showList}
            aria-controls="memory-space-find"
            aria-pressed={listOpen}
            data-active={hasActiveCriteria(criteria) ? 'true' : undefined}
            onClick={toggleList}
            ref={findToggleRef}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="5.5" /><path d="m14 14 5 5M10 7.5v5M7.5 10h5" /></svg>
            <span>查找</span>
          </button>
        ) : <span className="memory-space-find-slot" aria-hidden="true" />}
      </header>

      {mode === 'constellation' && jobs?.runtime_mode && jobs.runtime_mode !== 'v11' ? (
        <p className="memory-runtime-notice memory-runtime-notice-overview" role="status">当前对话尚未使用本页的记忆。星图与档案暂供查看。</p>
      ) : null}

      {mode === 'constellation' ? (
        <p className="memory-soul-lockup is-mirror" data-testid="soul-lockup">
          <span className="memory-soul-lockup-soul">{lockup[0]}</span>
        </p>
      ) : null}

      {mode === 'constellation' && presentation.phase === 'loading' && !liveProjection ? (
        <p className="memory-space-state" data-testid="memory-loading">正在读取记忆投影…</p>
      ) : null}
      {mode === 'constellation' && erasureSyncing ? (
        <p className="memory-space-state" role="status" data-testid="memory-erasure-sync">
          记忆已删除，正在同步档案视图…
        </p>
      ) : null}
      {mode === 'constellation' && presentation.phase === 'scope-switching' && liveProjection ? (
        <p className="memory-space-state" data-testid="memory-stale">{presentation.copy}</p>
      ) : null}
      {mode === 'constellation' && presentation.stale && presentation.phase !== 'scope-switching' && liveProjection ? (
        <p className="memory-space-state" data-testid="memory-stale">{presentation.copy ?? '同步中'}</p>
      ) : null}
      {mode === 'constellation' && presentation.phase === 'unavailable' ? (
        <p className="memory-space-state" role="alert" data-testid="memory-unavailable">
          {presentation.copy}
          {presentation.showRetry ? (
            <button type="button" data-testid="memory-retry" onClick={retryProjection}>重试</button>
          ) : null}
        </p>
      ) : null}
      {mode === 'constellation' && presentation.phase === 'error' ? (
        <p className="memory-space-state" role="alert" data-testid="memory-error">
          {presentation.copy}
          {presentation.showRetry ? (
            <button type="button" data-testid="memory-retry" onClick={retryProjection}>重试</button>
          ) : null}
        </p>
      ) : null}
      {mode === 'constellation' && presentation.phase === 'true-empty' ? (
        <p className="memory-space-state" data-testid="memory-empty">{presentation.copy}</p>
      ) : null}
      {mode === 'constellation' && presentation.phase === 'criteria-empty' ? (
        <p className="memory-space-state" data-testid="memory-criteria-empty">{presentation.copy}</p>
      ) : null}
      {mode === 'constellation' && !soulOpen && truncated && liveProjection && planReady ? (
        <p className="memory-space-truncation" data-testid="memory-truncation">
          当前呈现 {liveProjection.shown.results} / {liveProjection.eligible.results}，并非全部记忆。
        </p>
      ) : null}
      {mode === 'constellation' && disclosurePlan && !disclosurePlan.valid ? (
        <p className="memory-space-state" role="alert" data-testid="memory-malformed">
          当前投影无效：有结果缺少对应节点，无法生成完整列表。
        </p>
      ) : null}
      {mode === 'constellation' && renderSurface === 'list' ? (
        <p className="memory-space-state" data-testid="memory-list-fallback">
          3D 当前不可用，已切换至完整列表。
        </p>
      ) : null}
      {mode === 'constellation' && capability.reason !== 'healthy' && renderSurface === 'list' ? (
        <button type="button" data-testid="memory-retry-3d" onClick={() => {
          framesRef.current = [];
          setFallbackDetailOpen(false);
          setRuntimeGeneration((value) => value + 1);
          noteMotion('render-surface');
          setCapability((current) => applyRetry3d(current, true));
        }}>
          重试 3D
        </button>
      ) : null}

      <div
        className="memory-constellation-host"
        ref={graphRef}
        data-testid="memory-graph"
        hidden={renderSurface !== '3d' || mode === 'archive'}
        tabIndex={listOwnsFocus ? -1 : 0}
        role="group"
        aria-label="记忆星座仪"
        onFocus={() => setListOwnsFocus(false)}
      >
        {liveProjection && disclosurePlan && planReady && renderSurface === '3d' ? (
          <ConstellationStage
            soulOpen={soulOpen}
            soulReading={soulReading}
            onSoulRead={() => setSoulReading(true)}
            key={runtimeGeneration}
            plan={disclosurePlan}
            selectedId={selectedId}
            reducedMotion={reducedMotion}
            decoration={capability.decoration}
            expandedTopicIds={expandedTopicIds}
            worldline={worldline}
            query={criteria.query}
            motionCause={motionCause}
            motionCauses={motionCauses}
            motionEpoch={motionEpoch}
            scopeKey={scopeKey}
            insets={motionInsets}
            onSelect={handleSelect}
            onUnwind={() => unwind({ closeAtRoot: false })}
            onCreateFail={() => setCapability((current) => applyCreateFailure(current))}
            onContextLost={() => {
              setCapability((current) => {
                const next = applyContextLoss(current);
                if (next.retrying) setRuntimeGeneration((value) => value + 1);
                return next;
              });
            }}
            onFrame={(sample) => {
              // P1 (round 5): the buffer itself is part of the judgment
              // window; keep it in the same accumulation seam.
              setCapability((current) => {
                const next = applyFrameSample(current, framesRef.current, sample);
                framesRef.current = next.buffer;
                return next.state;
              });
            }}
            onPerfBaselineReset={() => {
              // P1 (round 5): visibility/focus recovery clears the FULL
              // sampling window, not just the stage-local warmup counters.
              framesRef.current = [];
            }}
          />
        ) : null}
      </div>

      {mode === 'archive' && archiveModuleRef.current ? (
        <ArchiveView module={archiveModuleRef.current} scope={scope} width={width} reducedMotion={reducedMotion} />
      ) : null}

      {findMounted ? (
        <motion.aside initial={reducedMotion ? false : { opacity: 0, x: -18 }} animate={{ opacity: 1, x: 0 }} transition={{ type: 'spring', stiffness: 270, damping: 29 }}
          id="memory-space-find"
          className="memory-space-find"
          data-testid="memory-space-find"
          data-filters-open={String(filtersOpen)}
          hidden={!showList}
        >
          <div className="memory-find-heading">
            <h2>记忆检索</h2>
            {!fallbackList ? <button type="button" className="memory-panel-close" aria-label="关闭查找" onClick={() => { toggleList(); findToggleRef.current?.focus(); }}>×</button> : null}
          </div>
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
              placeholder="搜索记忆"
              aria-label="搜索记忆"
            />
          </label>
          {chips.length ? (
            <ul className="memory-space-chips" data-testid="memory-criteria-chips">
              {chips.map((chip: CriteriaChip) => (
                <li key={chip.id}>
                  <button type="button" onClick={() => changeCriteria(clearChip(criteria, chip))}>
                    {chip.key === 'updatedFrom' || chip.key === 'updatedTo'
                      ? `${chip.key === 'updatedFrom' ? '自' : '至'} ${toDatetimeLocalValue(chip.value).replace('T', ' ')}`
                      : chip.label}
                    <span aria-hidden="true" className="memory-chip-remove">×</span>
                  </button>
                </li>
              ))}
              <li>
                <button type="button" onClick={() => changeCriteria(EMPTY_CRITERIA)}>清除筛选</button>
              </li>
            </ul>
          ) : null}
          <fieldset className="memory-space-kinds" data-testid="memory-kind-filter">
            <legend className="sr-only">种类</legend>
            {(['topic', 'fact', 'experience'] as const).map((kind) => (
              <label key={kind}>
                <input
                  type="checkbox"
                  checked={criteria.kinds.includes(kind)}
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
          <details
            className="memory-space-find-filters"
            data-testid="memory-space-tools"
            data-open={String(filtersOpen)}
            open={filtersOpen}
            onToggle={(event) => setFiltersOpen(event.currentTarget.open)}
          >
            <summary>主题与时间</summary>
            <div className="memory-space-tools">
              <label>
                <span className="sr-only">主题筛选</span>
                <MemorySelect
                  aria-label="主题筛选"
                  value={criteria.topicId ?? ''}
                  onValueChange={(value) => changeCriteria({ ...criteria, topicId: value || null })}
                >
                  <option value="">全部主题</option>
                  {topics.map((topic) => (
                    <option key={topic.topic_id} value={topic.topic_id}>{topic.display_label}</option>
                  ))}
                </MemorySelect>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={criteria.pinnedOnly}
                  onChange={(event) => changeCriteria({ ...criteria, pinnedOnly: event.target.checked })}
                />
                仅置顶
              </label>
              <label>
                <span className="memory-field-label">起始时间</span>
                <input
                  type="datetime-local"
                  aria-label="起始时间"
                  value={toDatetimeLocalValue(criteria.updatedFrom)}
                  onChange={(event) => changeCriteria({
                    ...criteria,
                    updatedFrom: event.target.value ? new Date(event.target.value).toISOString() : null,
                  })}
                />
              </label>
              <label>
                <span className="memory-field-label">结束时间</span>
                <input
                  type="datetime-local"
                  aria-label="结束时间"
                  value={toDatetimeLocalValue(criteria.updatedTo)}
                  onChange={(event) => changeCriteria({
                    ...criteria,
                    updatedTo: event.target.value ? new Date(event.target.value).toISOString() : null,
                  })}
                />
              </label>
            </div>
          </details>
          <div className="memory-space-find-scope" role="group" aria-label="浏览范围">
            <p className="memory-space-find-scope-label">浏览范围</p>
            <MemorySelect
              aria-label="浏览身份"
              value={identityMode}
              onValueChange={(value) => setIdentityMode(value as IdentityMode)}
            >
              <option value="okabe">OKABE</option>
              <option value="self">USER</option>
            </MemorySelect>
            <MemorySelect
              aria-label="浏览世界线"
              value={worldline}
              onValueChange={(value) => setWorldline(value as Worldline)}
            >
              <option value="steins_gate">SG</option>
              <option value="beta">β</option>
            </MemorySelect>
          </div>
          {disclosurePlan && planReady ? (
            <MemoryResultOutline
              reducedMotion={reducedMotion}
              plan={disclosurePlan}
              selectedId={selectedId}
              focusedId={focusedId ?? listRovingId}
              listRef={listRef}
              onOwnsFocus={setListOwnsFocus}
              onSelect={(id) => {
                handleSelect(id, 'activate');
                setListOpen(true);
                if (fallbackList) setFallbackDetailOpen(true);
                setNarrowPrefer('detail');
              }}
              onRove={(nextId) => roveList(nextId)}
            />
          ) : (
            <FindResultsStatus
              phase={presentation.phase}
              copy={presentation.copy}
              showRetry={presentation.showRetry}
              malformed={Boolean(disclosurePlan && !disclosurePlan.valid)}
              onRetry={retryProjection}
            />
          )}
        </motion.aside>
      ) : null}

      {soulOpen ? <SoulInteriorPrototype worldline={worldline} reading={soulReading} onRead={() => setSoulReading(true)} onObserve={() => setSoulReading(false)} onClose={() => setSoulOpen(false)} /> : null}

      {showInspector && liveProjection && !soulOpen ? (
        <aside className="memory-inspector" data-testid="soul-inspector">
          <div className="memory-panel-heading">
            <h2>AMADEUS / SOUL</h2>
            <button type="button" className="memory-panel-close" aria-label="关闭 SOUL 概览" onClick={dismissPanel}>×</button>
          </div>
          <p>SOUL 是当前 Amadeus 记忆连续性的投影中心，不是一条可编辑的记忆。</p>
          <button type="button" className="soul-opening-toggle" onClick={() => setSoulOpen(true)}>展开核心</button>
          <button type="button" className="soul-origin-shortcut" onClick={() => { setSoulOpen(true); setSoulReading(true); }}>阅读起点 ↗</button>
          <dl data-testid="inspector-composition">
            <div><dt>稳定事实</dt><dd>{liveProjection.composition.active_facts}</dd></div>
            <div><dt>经历</dt><dd>{liveProjection.composition.active_experiences}</dd></div>
            <div><dt>主题</dt><dd>{liveProjection.composition.eligible_topics}</dd></div>
            <div>
              <dt>最近变更</dt>
              <dd data-testid="inspector-latest-change" title={liveProjection.composition.latest_memory_change_at ?? undefined}>
                {formatTimeHuman(liveProjection.composition.latest_memory_change_at) ?? '无'}
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

      {showReading && card ? (
        <motion.aside initial={reducedMotion ? false : { opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} transition={{ type: 'spring', stiffness: 270, damping: 29 }}
          className="memory-reading-dom"
          data-testid="reading-dom"
          data-reading-id={selectedId ?? ''}
        >
          <div className="memory-reading-heading">
            <span className="memory-reading-caption">{card.kind === '主题' ? '主题概览' : '阅读记忆'}</span>
            {readingIndex >= 0 ? <span className="memory-reading-position" data-testid="reading-position">{readingIndex + 1} / {readingResults.length}</span> : null}
            <button type="button" className="memory-panel-close" aria-label="关闭阅读" onClick={dismissPanel}>×</button>
          </div>
          <motion.div initial={reducedMotion ? false : { opacity: 0, y: 9, filter: 'blur(3px)' }} animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }} transition={{ duration: reducedMotion ? 0 : 0.3 }} className="memory-reading-card-content" key={selectedId ?? 'reading'} ref={readingPanelRef}>
            <ReadingBody
              card={card}
              details={effectiveDetails}
              model={readingModel}
              onRetry={retryDetails}
              scope={liveProjection?.scope ?? null}
              recordKind={selectedProjectionNode?.kind === 'fact' || selectedProjectionNode?.kind === 'experience'
                ? selectedProjectionNode.kind
                : null}
              recordId={detailsRecordId}
              onOpenInArchive={openArchiveForRecord}
            />
          </motion.div>
          {readingIndex >= 0 && readingResults.length > 1 ? (
            <nav className="memory-reading-navigation" aria-label="连续阅读">
              <button type="button" disabled={readingIndex === 0} onClick={() => handleSelect(readingResults[readingIndex - 1].id, 'activate')}>
                <span aria-hidden="true">←</span> 上一条
              </button>
              <button type="button" disabled={readingIndex === readingResults.length - 1} onClick={() => handleSelect(readingResults[readingIndex + 1].id, 'activate')}>
                下一条 <span aria-hidden="true">→</span>
              </button>
            </nav>
          ) : null}
        </motion.aside>
      ) : null}
    </section>
  );
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

function toDatetimeLocalValue(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function FindResultsStatus({
  phase,
  copy,
  showRetry,
  malformed,
  onRetry,
}: {
  phase: string;
  copy: string | null | undefined;
  showRetry?: boolean;
  malformed: boolean;
  onRetry: () => void;
}) {
  const alert = malformed || phase === 'unavailable' || phase === 'error';
  const text = malformed
    ? '当前投影无效：有结果缺少对应节点，无法生成完整列表。'
    : phase === 'loading'
      ? '正在读取记忆投影…'
      : (copy ?? '正在准备结果…');
  return (
    <p
      className="memory-find-results-state"
      role={alert ? 'alert' : undefined}
      data-testid="memory-find-results-state"
    >
      {text}
      {(phase === 'unavailable' || phase === 'error') && showRetry ? (
        <button type="button" data-testid="memory-find-retry" onClick={onRetry}>重试</button>
      ) : null}
    </p>
  );
}

type SourceLineModel = {
  sourceCreatedHuman: string | null;
  sourceErased: boolean;
  excerpt: string | null;
};

function SourceLines({ lines }: { lines: SourceLineModel[] }) {
  if (!lines.length) {
    // P2 (round 5): an absent field gets an honest disclosure; never imply
    // that the source was deleted or never existed.
    return <p className="memory-reading-sources-empty" data-testid="reading-sources-empty">未提供可显示的来源</p>;
  }
  return (
    <ul className="memory-reading-sources">
      {lines.map((line, index) => (
        <li key={`${index}-${line.sourceErased ? 'erased' : 'present'}`}>
          {line.sourceErased ? (
            <span data-testid="reading-source-erased">原始消息已删除，仅保留来源记录</span>
          ) : line.excerpt ? (
            <>
              <blockquote data-testid="reading-source-excerpt">{line.excerpt}</blockquote>
              {line.sourceCreatedHuman ? <span>{line.sourceCreatedHuman}</span> : null}
            </>
          ) : (
            <>
              <p className="memory-reading-source-missing" data-testid="reading-source-excerpt-missing">
                这条来源记录暂未提供摘录
              </p>
              {line.sourceCreatedHuman ? <span>{line.sourceCreatedHuman}</span> : null}
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function ReadingEvidence({
  outcome,
  onRetry,
}: {
  outcome: DetailsOutcome;
  onRetry: () => void;
}) {
  if (outcome.type === 'idle' || outcome.type === 'ok') return null;
  if (outcome.type === 'loading') {
    return <p data-testid="reading-evidence-loading">正在读取来源与版本…</p>;
  }
  if (outcome.type === 'not-found') {
    return <p data-testid="reading-evidence-missing">{NOT_FOUND_NOTICE}</p>;
  }
  return (
    <p data-testid="reading-evidence-error">
      证据暂时不可用。
      <button type="button" data-testid="reading-evidence-retry" onClick={onRetry}>重试</button>
    </p>
  );
}

type ScopeEcho = MemoryProjection['scope'] | null;

function DiagnosticsBlock({
  model,
  scope,
}: {
  model: FactReadingModel | ExperienceReadingModel;
  scope: ScopeEcho;
}) {
  return (
    <details data-testid="reading-diagnostics">
      <summary>诊断</summary>
      <dl>
        {model.kind === 'fact' ? (
          <>
            <div><dt>fact_id</dt><dd>{model.diagnostics.factId}</dd></div>
            {model.diagnostics.topicIdRaw ? (
              <div><dt>topic_id</dt><dd>{model.diagnostics.topicIdRaw}</dd></div>
            ) : null}
          </>
        ) : (
          <div><dt>experience_id</dt><dd>{model.diagnostics.experienceId}</dd></div>
        )}
        <div>
          <dt>observation_id</dt>
          <dd>
            {model.kind === 'fact'
              ? model.diagnostics.observationIds.join(', ')
              : model.diagnostics.observationId}
          </dd>
        </div>
        <div><dt>conversation_id</dt><dd>{model.diagnostics.conversationIds.join(', ')}</dd></div>
        <div>
          <dt>source_message_id</dt>
          <dd data-testid="reading-diagnostics-source-messages">
            {model.diagnostics.sourceMessageIds.join(', ')}
          </dd>
        </div>
        <div>
          <dt>raw ISO</dt>
          <dd data-testid="reading-diagnostics-raw-time">{model.diagnostics.rawTimestamps.join(' / ')}</dd>
        </div>
        {scope ? (
          <div>
            <dt>scope</dt>
            <dd data-testid="reading-diagnostics-scope">
              {scope.session_id} / {scope.worldline} / {scope.identity_mode}
            </dd>
          </div>
        ) : null}
      </dl>
    </details>
  );
}

function EvidenceDetails({
  model,
  scope,
}: {
  model: FactReadingModel | ExperienceReadingModel;
  scope: ScopeEcho;
}) {
  return (
    <div className="memory-reading-evidence" data-testid="reading-evidence">
      <h3 className="memory-evidence-heading">来源与沿革</h3>
      {model.kind === 'fact' ? (
        <>
          <SourceLines lines={model.activeSources} />
          {model.revisionSummary ? (
            <p data-testid="reading-revision-summary">{model.revisionSummary}</p>
          ) : null}
          {model.versionCount > 1 ? (
            <details data-testid="reading-versions">
              <summary>版本历史（{model.versionCount}）</summary>
              <ol className="memory-reading-versions">
                {model.historyVersions.map((version) => (
                  <li key={version.versionNo} data-active={version.isActive ? 'true' : 'false'}>
                    <span data-testid="reading-version-kind">{version.changeKindHuman}</span>
                    {version.validFromHuman ? <span> · {version.validFromHuman}</span> : null}
                    {version.isActive ? (
                      <span> · 当前版本</span>
                    ) : version.invalidAtHuman ? (
                      <span data-testid="reading-version-invalid"> · 失效于 {version.invalidAtHuman}</span>
                    ) : null}
                    <p>{version.displayText}</p>
                    <SourceLines lines={version.sources} />
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
        </>
      ) : (
        <>
          <SourceLines lines={model.sources} />
          {model.createdHuman ? (
            <p data-testid="reading-created-human">发生于 {model.createdHuman}</p>
          ) : null}
          {model.expiry ? (
            model.expiry.expired ? (
              <p data-testid="reading-expired">已过期{model.expiry.expiresHuman ? `（${model.expiry.expiresHuman}）` : ''}</p>
            ) : (
              <p data-testid="reading-expires">{model.expiry.expiresHuman ? `将于 ${model.expiry.expiresHuman} 过期` : ''}</p>
            )
          ) : null}
        </>
      )}
      <details className="memory-reading-notices">
        <summary>记录信息与说明</summary>
        <p data-testid="reading-correction-notice">{model.correctionNotice}</p>
        <p data-testid="reading-deeplink-notice">{model.deepLinkNotice}</p>
        <DiagnosticsBlock model={model} scope={scope} />
      </details>
    </div>
  );
}

function ReadingLocator({ card }: { card: NonNullable<ReturnType<typeof readingCardFromNode>> }) {
  return (
    <p className="memory-reading-kind" data-testid="reading-kind">
      {card.kind}
      {card.provenance.topicLabel ? (
        <>
          {' · '}
          <span data-testid="reading-topic-label">{card.provenance.topicLabel}</span>
        </>
      ) : null}
    </p>
  );
}

function ReadingBody({
  card,
  details,
  model,
  onRetry,
  scope,
  recordKind,
  recordId,
  onOpenInArchive,
}: {
  card: NonNullable<ReturnType<typeof readingCardFromNode>>;
  details: DetailsOutcome;
  model: FactReadingModel | ExperienceReadingModel | null;
  onRetry: () => void;
  scope: ScopeEcho;
  recordKind?: 'fact' | 'experience' | null;
  recordId?: string | null;
  onOpenInArchive?: (kind: 'fact' | 'experience', recordId: string) => void;
}) {
  const bodyText = details.type === 'ok' && model ? model.primaryText : card.title;
  if (details.type === 'ok' && model) {
    return (
      <>
        <ReadingLocator card={card} />
        <p className="memory-reading-body" data-testid="reading-title">{bodyText}</p>
        <EvidenceDetails model={model} scope={scope} />
        <ReadingProvenance provenance={{ ...card.provenance, topicLabel: null }} />
        {onOpenInArchive && recordKind && recordId ? (
          <button
            type="button"
            className="memory-reading-archive"
            data-testid="open-in-archive"
            onClick={() => onOpenInArchive(recordKind, recordId)}
          >
            在档案中整理
          </button>
        ) : null}
      </>
    );
  }
  return (
    <>
      {card.kindKey !== 'topic' ? (
        <p className="memory-reading-projection-summary" data-testid="reading-projection-summary">
          投影摘要（未核验）
        </p>
      ) : null}
      <ReadingLocator card={card} />
      <p className="memory-reading-body" data-testid="reading-title">{bodyText}</p>
      {card.summary ? <p data-testid="reading-summary">{card.summary}</p> : null}
      <ReadingProvenance provenance={{ ...card.provenance, topicLabel: null }} />
      <ReadingEvidence outcome={details} onRetry={onRetry} />
    </>
  );
}
