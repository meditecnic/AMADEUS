import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { CompatibilityView } from './CompatibilityView';
import { MemoryResultOutline } from './MemoryResultOutline';
import { deriveOverviewPresentation, outlineIds } from './overviewPresentation';
import { memorySpaceShortcut } from './chrome';
import { ConstellationStage, type ScreenAnchor } from './ConstellationStage';
import type { OverviewFrame, PresentationChangeCause } from './overviewMotion';
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
import { layoutProjection, readingSurfaceMode } from './layout';
import { classifyOverviewState } from './overviewState';
import { createProjectionController } from './projectionController';
import { ReadingFields, ReadingProvenance, readingCardFromNode } from './readingContent';
import {
  createDetailsController,
  deriveExperienceReading,
  deriveFactReading,
  NOT_FOUND_NOTICE,
  type DetailsOutcome,
  type ExperienceReadingModel,
  type FactReadingModel,
} from './readingDetails';
import {
  HEALTHY_CAPABILITY,
  applyContextLoss,
  applyCreateFailure,
  applyExplicitCompatibility,
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
  const detailsControllerRef = useRef(createDetailsController());
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
  const [camOwnership, setCamOwnership] = useState<OverviewFrame['ownership']>('rest');
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

  const surface = readingSurfaceMode({ width, preferList: listOpen, reducedMotion });
  const cameraMode: '3d' | '2d' = forceCompat ? '2d' : '3d';
  const renderSurface = capability.surface;
  const inspectorOpen = overviewOpen && selected?.kind === 'continuity_hub';
  const memoOpen = Boolean(card) && overviewOpen;
  const showSceneMemo = memoOpen && surface === 'scene' && renderSurface === '3d' && cameraMode === '3d' && camOwnership !== 'user' && (effectiveDetails.type === 'idle' || effectiveDetails.type === 'loading');
  const showDomMemo = memoOpen && !showSceneMemo;
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
      setToolsOpen(false);
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
    const restoreList = toolsOpen && listOpen;
    setToolsOpen(false);
    noteMotion('auxiliary-surface');
    if (restoreList || (listOpen && narrowPrefer !== 'list')) {
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

  const unwind = (opts?: { closeAtRoot?: boolean }) => {
    const selectedKind = selected?.kind
      ?? liveProjection?.nodes.find((node) => node.projection_id === selectedId)?.kind
      ?? null;
    const action = nextMemoryUnwind({
      toolsOpen,
      hasActiveCriteria: hasActiveCriteria(criteria),
      listVisible: Boolean(listOpen && (width >= 900 || narrowPrefer === 'list')),
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
      setToolsOpen(false);
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
      if (inField) return;
      const shortcut = memorySpaceShortcut(event);
      if (shortcut === 'search') {
        event.preventDefault();
        setToolsOpen(true);
        window.requestAnimationFrame(() => searchRef.current?.focus());
        return;
      }
      if (shortcut === 'list') {
        toggleList();
        return;
      }
      if (shortcut === 'view-mode') {
        setForceCompat((value) => !value);
        setCapability((current) => applyExplicitCompatibility(current, !forceCompat));
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
    if (toolsOpen) return 'tools';
    if (listOpen && narrowPrefer === 'list') return 'list';
    if (inspectorOpen) return 'inspector';
    if (memoOpen) return 'reading';
    if (listOpen) return 'list';
    return 'none';
  })();
  const showList = Boolean(
    disclosurePlan
    && planReady
    && listOpen
    && (width >= 900
      ? narrowSheet !== 'tools' && narrowSheet !== 'inspector'
      : narrowSheet === 'list'),
  );
  const showInspector = Boolean(inspectorOpen && liveProjection && narrowSheet === 'inspector');
  const showReading = Boolean(showDomMemo && card && narrowSheet === 'reading');
  const showSceneReading = Boolean(showSceneMemo && card && narrowSheet === 'reading');

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
      setAnnouncement(showReading || showSceneReading ? '证据已读取' : '证据已载入，可在阅读面查看');
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
  const motionInsets = {
    top: 64,
    right: 16,
    bottom: narrowSheet !== 'none' && width < 900 ? Math.round((typeof window === 'undefined' ? 800 : window.innerHeight) * 0.42) : 16,
    left: 16,
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
    cameraMode,
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
      data-tools-open={String(toolsOpen)}
      aria-label="Memory Space"
    >
      <div className="sr-only" role="status" aria-live="polite" data-testid="memory-announce">{announcement}</div>
      <header className="memory-space-veil" data-testid="memory-space-veil" data-chrome="compact">
        <button
          type="button"
          className="memory-space-return"
          data-testid="memory-back"
          onClick={() => unwind({ closeAtRoot: true })}
        >
          {!selectedId && !expandedTopicIds.length && !listOpen && !toolsOpen && !hasActiveCriteria(criteria) ? '返回' : '回退'}
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
          onClick={() => {
            noteMotion('auxiliary-surface');
            setToolsOpen((value) => !value);
          }}
        >
          仪器
        </button>
        <button type="button" aria-pressed={listOpen && narrowSheet === 'list'} onClick={toggleList}>
          LIST
        </button>
        <button
          type="button"
          aria-pressed={renderSurface === 'compatibility'}
          onClick={() => {
            const next = renderSurface !== 'compatibility';
            noteMotion('render-surface');
            setForceCompat(next);
            setCapability((current) => applyExplicitCompatibility(current, next));
          }}
        >
          {renderSurface === 'compatibility' || cameraMode === '2d' ? '2D' : '3D'}
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
      {truncated && liveProjection && planReady ? (
        <p className="memory-space-truncation" data-testid="memory-truncation">
          当前呈现 {liveProjection.shown.results} / {liveProjection.eligible.results}，并非全部记忆。
        </p>
      ) : null}
      {disclosurePlan && !disclosurePlan.valid ? (
        <p className="memory-space-state" role="alert" data-testid="memory-malformed">
          当前投影无效：有结果缺少对应节点，无法生成完整列表。
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
        {liveProjection && disclosurePlan && planReady && renderSurface === '3d' ? (
          <ConstellationStage
            key={runtimeGeneration}
            plan={disclosurePlan}
            selectedId={selectedId}
            mode={cameraMode}
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
            onOwnershipChange={setCamOwnership}
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
              // P1 (round 5): the buffer itself is part of the judgment
              // window; keep it in the same accumulation seam.
              const next = applyFrameSample(capability, framesRef.current, sample);
              framesRef.current = next.buffer;
              setCapability(next.state);
            }}
            onPerfBaselineReset={() => {
              // P1 (round 5): visibility/focus recovery clears the FULL
              // sampling window, not just the stage-local warmup counters.
              framesRef.current = [];
            }}
          />
        ) : null}
        {disclosurePlan && planReady && renderSurface === 'compatibility' ? (
          <CompatibilityView
            plan={disclosurePlan}
            selectedId={selectedId}
            focusedId={focusedId}
            onSelect={handleSelect}
          />
        ) : null}

        {showSceneReading && card ? (
          <aside
            className="memory-reading-scene"
            data-testid="reading-scene"
            data-attached="sphere"
            style={anchor ? { left: anchor.x, top: anchor.y } : undefined}
          >
            <ReadingBody
              card={card}
              details={effectiveDetails}
              model={readingModel}
              onRetry={retryDetails}
              scope={liveProjection?.scope ?? null}
            />
          </aside>
        ) : null}
      </div>

      {showList && disclosurePlan ? (
        <MemoryResultOutline
          plan={disclosurePlan}
          selectedId={selectedId}
          focusedId={focusedId ?? listRovingId}
          listRef={listRef}
          onOwnsFocus={setListOwnsFocus}
          onSelect={(id) => {
            handleSelect(id, 'activate');
            setListOpen(true);
            setNarrowPrefer('list');
          }}
          onRove={(nextId) => roveList(nextId)}
        />
      ) : null}

      {showInspector && liveProjection ? (
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

      {showReading && card ? (
        <aside className="memory-reading-dom" data-testid="reading-dom">
          <ReadingBody
            card={card}
            details={effectiveDetails}
            model={readingModel}
            onRetry={retryDetails}
            scope={liveProjection?.scope ?? null}
          />
        </aside>
      ) : null}
    </section>
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
          ) : (
            <>
              <blockquote data-testid="reading-source-excerpt">{line.excerpt}</blockquote>
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
      {model.kind === 'fact' ? (
        <>
          {model.revisionSummary ? (
            <p data-testid="reading-revision-summary">{model.revisionSummary}</p>
          ) : null}
          <SourceLines lines={model.activeSources} />
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
          <SourceLines lines={model.sources} />
        </>
      )}
      <DiagnosticsBlock model={model} scope={scope} />
      <p data-testid="reading-correction-notice">{model.correctionNotice}</p>
      <p data-testid="reading-deeplink-notice">{model.deepLinkNotice}</p>
    </div>
  );
}

// P1 spec-3: one payload-owned primary text in the success state; the
// envelope card is an explicitly labeled projection summary everywhere else.
function ReadingBody({
  card,
  details,
  model,
  onRetry,
  scope,
}: {
  card: NonNullable<ReturnType<typeof readingCardFromNode>>;
  details: DetailsOutcome;
  model: FactReadingModel | ExperienceReadingModel | null;
  onRetry: () => void;
  scope: ScopeEcho;
}) {
  if (details.type === 'ok' && model) {
    return (
      <>
        <p className="memory-reading-kind" data-testid="reading-kind">{card.kind}</p>
        <h2 data-testid="reading-title">{model.primaryText}</h2>
        <ReadingProvenance provenance={card.provenance} />
        <EvidenceDetails model={model} scope={scope} />
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
      <ReadingFields card={card} />
      <ReadingEvidence outcome={details} onRetry={onRetry} />
    </>
  );
}
