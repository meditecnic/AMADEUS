import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type RefObject } from 'react';

import { useReducedMotion } from '../design-system/useReducedMotion';
import { ArchiveView } from './ArchiveView';
import { createArchiveModule, type ArchiveModule } from './archiveState';
import { buildChartModel, plateGeometry, platePoint } from './chartCoordinates';
import { hm, recordNo, ymd } from './chartFormat';
import { memorySpaceShortcut } from './chrome';
import { MemoryCone, type ConeControls } from './cone/MemoryCone';
import { clearSlow, machineKey, readChoice, readSlowUntil, writeChoice, writeSlow } from './displayPrefs';
import { chartScopeKey, createChartController, type ChartError, type ChartScope } from './fetchChart';
import { MemoryPlate, type ChartSelection } from './MemoryPlate';
import { MemoryReading } from './MemoryReading';
import { nextMemoryUnwind, type MemoryUnwindKind } from './memoryUnwind';
import { createDetailsController, type DetailsCache, type DetailsOutcome, type DetailsRequest } from './readingDetails';
import {
  applyContextLoss, applyCreateFailure, applyRetry3d, applySlow, applyUserChoice, decideSurface, type CapabilityState, type Surface,
} from './renderCapability';
import type { IdentityMode, MemoryChart, Worldline } from './types';
import { probeWebGL } from './webglProbe';

/** Opens the space on a chat mark: select the first of `select` that is on the plate, draw `mark` with the
 *  dashed "this turn" reticle, and (when `hintAt` is set) show the "referenced n · HH:MM" hint. Applies to the chat's own scope only. */
export type MemoryLink = {
  select: readonly string[];
  mark: readonly string[];
  hintAt: number | null;
};

export type MemorySpaceProps = {
  sessionId: string;
  chatWorldline: Worldline;
  chatIdentityMode: IdentityMode;
  onClose: () => void;
  /** Lets the header's memory key leave through the same unsaved-draft guard as Esc. */
  controlRef?: RefObject<MemorySpaceControl | null>;
  link?: MemoryLink | null;
};

export type MemorySpaceControl = { requestClose: () => void };

export type MemorySpaceMode = 'chart' | 'archive';

type ChartLoad =
  | { status: 'loading'; scopeKey: string }
  | { status: 'ready'; scopeKey: string; chart: MemoryChart; today: number }
  | { status: 'error'; scopeKey: string; error: ChartError };

const LAST_OPEN_KEY = 'amadeus_memory_last_open';
const HEADER_PX = 60;
const SCOPES: Array<{ worldline: Worldline; identityMode: IdentityMode }> = [
  { identityMode: 'okabe', worldline: 'steins_gate' }, { identityMode: 'okabe', worldline: 'beta' },
  { identityMode: 'self', worldline: 'steins_gate' }, { identityMode: 'self', worldline: 'beta' },
];

function readLastOpen(key: string): number | null {
  try {
    const value = (JSON.parse(localStorage.getItem(LAST_OPEN_KEY) ?? '{}') as Record<string, unknown>)[key];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function writeLastOpen(key: string, at: number): void {
  try {
    const all = JSON.parse(localStorage.getItem(LAST_OPEN_KEY) ?? '{}') as Record<string, number>;
    localStorage.setItem(LAST_OPEN_KEY, JSON.stringify({ ...all, [key]: at }));
  } catch {
    /* a private window keeps no "last open"; every record simply reads as already seen next time */
  }
}

const wlShort = (worldline: Worldline) => (worldline === 'steins_gate' ? 'SG' : 'β');
const who = (mode: IdentityMode) => (mode === 'self' ? 'USER' : 'OKABE');

function ScopeName({ worldline, identityMode }: { worldline: Worldline; identityMode: IdentityMode }) {
  return <><span className={identityMode === 'okabe' ? 'okabe' : undefined}>{who(identityMode)}</span> · {wlShort(worldline)}</>;
}

const noopSubscribe = () => () => undefined;

export function MemorySpace({ sessionId, chatWorldline, chatIdentityMode, onClose, controlRef, link }: MemorySpaceProps) {
  const reducedMotion = useReducedMotion();
  const [worldline, setWorldline] = useState<Worldline>(chatWorldline);
  const [identityMode, setIdentityMode] = useState<IdentityMode>(chatIdentityMode);
  const scope: ChartScope = useMemo(() => ({ sessionId, worldline, identityMode }), [sessionId, worldline, identityMode]);
  const scopeKey = chartScopeKey(scope);
  const scopeKeyRef = useRef(scopeKey);
  scopeKeyRef.current = scopeKey;

  const linkScopeRef = useRef(scopeKey);
  const pendingSelectRef = useRef<readonly string[]>(link?.select ?? []);
  const [marks, setMarks] = useState<readonly string[]>(() => link?.mark ?? []);
  const [hintAt, setHintAt] = useState<number | null>(() => link?.hintAt ?? null);

  const [mode, setMode] = useState<MemorySpaceMode>('chart');
  const [load, setLoad] = useState<ChartLoad>({ status: 'loading', scopeKey });
  const [refreshTick, setRefreshTick] = useState(0);
  // Records Archive just erased stay off the plate until the refreshed chart lands.
  const [erased, setErased] = useState<ReadonlySet<string>>(() => new Set());
  const [selection, setSelection] = useState<ChartSelection>(null);
  const [focusTopic, setFocusTopic] = useState<string | null>(null);
  const [framedTopic, setFramedTopic] = useState<string | null>(null);
  const [entering, setEntering] = useState(true);
  // 3D cone or SVG plate (DIRECTION 16.1): decided once per visit, then only the user, a lost context or the frame timer change it.
  const [capability, setCapability] = useState<CapabilityState>(() => {
    const probe = probeWebGL();
    const now = Date.now();
    return decideSurface({ choice: readChoice(), probe, reducedMotion, slowUntil: readSlowUntil(machineKey(probe.renderer), now), now });
  });
  const capabilityRef = useRef(capability);
  capabilityRef.current = capability;
  const surface: Surface = capability.surface;
  const [coneMounted, setConeMounted] = useState(surface === '3d');
  const [plateMounted, setPlateMounted] = useState(surface === 'plate');
  // The plate is fully opaque unless the cone is rising out from under it.
  const [plateOpaque, setPlateOpaque] = useState(true);
  const [coneFlat, setConeFlat] = useState(false);
  const [rising, setRising] = useState(0);
  const [coneKey, setConeKey] = useState(0);
  const coneRef = useRef<ConeControls | null>(null);
  const prevSurface = useRef<Surface>(surface);
  const [details, setDetails] = useState<DetailsOutcome>({ type: 'idle' });
  const [scopeMenu, setScopeMenu] = useState(false);
  const [searchTick, setSearchTick] = useState(0);
  const sheetRef = useRef<HTMLElement | null>(null);
  const [size, setSize] = useState({ width: 1440, height: 840 });

  const chartControllerRef = useRef(createChartController());
  const detailsCacheRef = useRef<DetailsCache>(new Map());
  const detailsControllerRef = useRef(createDetailsController({ cache: detailsCacheRef.current }));
  const lastOpenRef = useRef(new Map<string, number | null>());
  const archiveOpenedRef = useRef<string | null>(null);
  const archiveRef = useRef<ArchiveModule | null>(null);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const handleErased = useCallback((_kind: 'fact' | 'experience', recordId: string) => {
    setErased((current) => new Set(current).add(recordId));
    if (selectionRef.current?.kind === 'star' && selectionRef.current.id === recordId) {
      detailsControllerRef.current.invalidate();
      setSelection(null);
    }
    setRefreshTick((tick) => tick + 1);
  }, []);
  if (!archiveRef.current) {
    archiveRef.current = createArchiveModule({
      detailsCache: detailsCacheRef.current,
      onRecordErased: handleErased,
      onExitArchive: () => setMode('chart'),
      onCloseMemory: () => onCloseRef.current(),
      onScopeChangeSettled: () => { archiveOpenedRef.current = scopeKeyRef.current; },
    });
  }
  const archive = archiveRef.current;
  const archiveState = useSyncExternalStore(mode === 'archive' ? archive.subscribe : noopSubscribe, archive.snapshot);

  useEffect(() => () => {
    chartControllerRef.current.dispose();
    detailsControllerRef.current.deactivate();
    archiveRef.current?.dispose();
    // A StrictMode remount reuses the same module; forgetting the marker re-arms it.
    archiveOpenedRef.current = null;
  }, []);

  // Opening moves focus off the header onto the sheet; closing hands it back to the memory key.
  useEffect(() => { sheetRef.current?.focus({ preventScroll: true }); }, []);

  useLayoutEffect(() => {
    const measure = () => {
      const el = sheetRef.current;
      const width = el?.clientWidth || window.innerWidth;
      const height = el?.clientHeight || Math.max(320, window.innerHeight - HEADER_PX);
      setSize((current) => (current.width === width && current.height === height ? current : { width, height }));
    };
    measure();
    window.addEventListener('resize', measure);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    if (observer && sheetRef.current) observer.observe(sheetRef.current);
    return () => { window.removeEventListener('resize', measure); observer?.disconnect(); };
  }, []);

  useEffect(() => {
    let live = true;
    setLoad((current) => (current.status === 'ready' && current.scopeKey === scopeKey ? current : { status: 'loading', scopeKey }));
    void chartControllerRef.current.load(scope).then((result) => {
      if (!live || result.type === 'stale') return;
      if (result.type === 'ok') {
        setLoad({ status: 'ready', scopeKey, chart: result.chart, today: Date.now() });
        setErased(new Set());
      } else {
        setLoad({ status: 'error', scopeKey, error: result.error });
      }
    });
    return () => { live = false; };
  }, [scope, scopeKey, refreshTick]);

  const ready = load.status === 'ready' && load.scopeKey === scopeKey ? load : null;
  if (ready && !lastOpenRef.current.has(scopeKey)) lastOpenRef.current.set(scopeKey, readLastOpen(scopeKey));
  const lastOpen = ready ? lastOpenRef.current.get(scopeKey) ?? null : null;
  const chart = useMemo(() => {
    if (!ready) return null;
    if (!erased.size) return ready.chart;
    return {
      ...ready.chart,
      facts: ready.chart.facts.filter((row) => !erased.has(row.id)),
      experiences: ready.chart.experiences.filter((row) => !erased.has(row.id)),
    };
  }, [ready, erased]);
  // Between 130 and 150 records the chart keeps whatever it was drawn as last time.
  const collapsedRef = useRef(new Map<string, boolean>());
  const model = useMemo(() => {
    if (!chart || !ready) return null;
    const built = buildChartModel(chart, { today: ready.today, lastOpen, wasCollapsed: collapsedRef.current.get(ready.scopeKey) });
    collapsedRef.current.set(ready.scopeKey, built.collapsed);
    return built;
  }, [chart, ready, lastOpen]);

  useEffect(() => {
    if (ready && !entering) writeLastOpen(ready.scopeKey, Date.now());
  }, [ready, entering]);

  useEffect(() => {
    setSelection(null);
    setFocusTopic(null);
    setFramedTopic(null);
    setScopeMenu(false);
    detailsControllerRef.current.invalidate();
  }, [scopeKey]);

  // A chat mark's record opens like a clicked star once the plate has it; one that is gone opens nothing.
  useEffect(() => {
    if (!model || !pendingSelectRef.current.length || scopeKey !== linkScopeRef.current) return;
    const id = pendingSelectRef.current.find((candidate) => model.byId.has(candidate));
    pendingSelectRef.current = [];
    if (id) select({ kind: 'star', id });
    // select only reads state through setters and the model.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, scopeKey]);

  // Selections that point at records no longer on the plate fall back to the overview.
  useEffect(() => {
    if (!model || !selection) return;
    if (selection.kind === 'star' && !model.byId.has(selection.id)) setSelection(null);
    if (selection.kind === 'topic' && !model.groups.some((t) => t.id === selection.id && t.starIds.length)) setSelection(null);
    if (selection.kind === 'cell' && !model.cells.has(selection.key)) setSelection(null);
  }, [model, selection]);

  const selectedStar = selection?.kind === 'star' ? model?.byId.get(selection.id) ?? null : null;
  const detailsRequest: DetailsRequest | null = useMemo(() => (selectedStar ? {
    scope, kind: selectedStar.kind === 'fragment' ? 'experience' : 'fact', recordId: selectedStar.id,
  } : null), [scope, selectedStar]);
  const detailsKey = detailsRequest ? `${scopeKey}|${detailsRequest.kind}|${detailsRequest.recordId}` : null;
  useEffect(() => {
    const controller = detailsControllerRef.current;
    if (!detailsRequest) { controller.deactivate(); setDetails({ type: 'idle' }); return; }
    setDetails(controller.activate(detailsRequest, setDetails));
    // detailsKey carries scope, kind and record id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailsKey]);
  const shownDetails: DetailsOutcome = details.type !== 'idle' && detailsKey
    && `${chartScopeKey(details.request.scope)}|${details.request.kind}|${details.request.recordId}` === detailsKey
    ? details : { type: 'idle' };
  const retryDetails = () => {
    if (details.type !== 'backend-failure' && details.type !== 'transport-error') return;
    setDetails(detailsControllerRef.current.activate(details.request, setDetails, { force: true }));
  };

  useEffect(() => {
    if (mode !== 'archive' || archiveOpenedRef.current === scopeKey) return;
    if (!archive.open(scope, { restore: true })) return;
    archiveOpenedRef.current = scopeKey;
  }, [archive, mode, scope, scopeKey]);

  useEffect(() => {
    if (!searchTick) return undefined;
    const focus = () => document.querySelector<HTMLInputElement>('[data-testid="archive-query"]')?.focus();
    focus();
    const frame = window.requestAnimationFrame(focus);
    return () => window.cancelAnimationFrame(frame);
  }, [searchTick]);

  // Switching surfaces moves only the picture: selection, focus, the open group and the reading stay as they are.
  useEffect(() => {
    const before = prevSurface.current;
    prevSurface.current = surface;
    if (before === surface) return undefined;
    const timers: number[] = [];
    if (surface === 'plate') {
      // Cone up and over, flattened onto the disc (0.82 s), then the plate fades in over it (0.24 s).
      setPlateMounted(true);
      const finish = () => {
        setPlateOpaque(true);
        timers.push(window.setTimeout(() => setConeMounted(false), reducedMotion ? 0 : 260));
      };
      if (reducedMotion || !coneRef.current) { finish(); setConeMounted(false); }
      else {
        setPlateOpaque(false);
        coneRef.current.toPlate(finish);
      }
    } else {
      // The reverse (1.1 s): the cone is made flat under the plate, the plate fades out and the cone rises.
      setConeFlat(true);
      setConeMounted(true);
      setRising((n) => n + 1);
    }
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [surface, reducedMotion]);

  useEffect(() => {
    if (!rising || surface !== '3d') return;
    const cone = coneRef.current;
    if (!cone) return;
    setPlateOpaque(false);
    cone.fromPlate(() => {
      setConeFlat(false);
      setPlateOpaque(true);
      // A cone that failed to build falls back to the plate while this runs; the plate must stay then.
      if (capabilityRef.current.surface === '3d') setPlateMounted(false);
    });
    // One rise per switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rising]);

  const choose = (next: Surface) => {
    writeChoice(next);
    if (next === '3d') clearSlow(machineKey(probeWebGL().renderer));
    setCapability(applyUserChoice(next));
  };
  const onConeUnavailable = useCallback(() => setCapability((current) => applyCreateFailure(current)), []);
  const onConeLost = useCallback(() => {
    const next = applyContextLoss(capabilityRef.current);
    setCapability(next);
    if (next.surface === '3d') setConeKey((key) => key + 1);
  }, []);
  const onConeSlow = useCallback(() => {
    writeSlow(machineKey(probeWebGL().renderer), Date.now());
    setCapability((current) => applySlow(current));
  }, []);
  const noticeAction = () => {
    const kind = capability.notice?.kind;
    // A cone that never got its context may still be on screen for a moment; the key gives the retry a fresh one.
    if (kind === 'create-fail' || kind === 'context-loss') { setCapability(applyRetry3d()); setConeKey((key) => key + 1); return; }
    choose('3d');
  };

  const overview = () => { setSelection(null); setFocusTopic(null); setFramedTopic(null); };
  const select = (next: ChartSelection) => {
    setScopeMenu(false);
    if (!next) { overview(); return; }
    if (next.kind === 'topic') { setFocusTopic(next.id); setFramedTopic(next.id); }
    else if (next.kind === 'cell') {
      const cell = model?.cells.get(next.key);
      if (cell) { setFocusTopic(cell.groupId); setFramedTopic(cell.groupId); }
    }
    // A record never moves the plate; it only lights its own topic, or on a collapsed plate opens its group.
    else if (next.kind === 'star') {
      const star = model?.byId.get(next.id);
      const group = model?.collapsed ? star?.group : star?.topicId;
      if (group) setFocusTopic(group);
    }
    else { setFocusTopic(null); setFramedTopic(null); }
    setSelection(next);
  };

  // Every way out of the memory space (header key, Esc, back button, state cards)
  // passes the archive's unsaved-draft and save-in-flight guard.
  const requestClose = () => {
    setScopeMenu(false);
    if (archive.handleCloseRequest()) onCloseRef.current();
  };
  useLayoutEffect(() => {
    if (!controlRef) return undefined;
    controlRef.current = { requestClose };
    return () => { controlRef.current = null; };
  });

  const unwind = (closeAtRoot: boolean) => {
    if (scopeMenu) { setScopeMenu(false); return; }
    if (mode === 'archive') {
      if (archive.handleBack()) return;
      if (closeAtRoot) requestClose();
      return;
    }
    const kind: MemoryUnwindKind = selection?.kind === 'star'
      ? (selectedStar?.kind === 'fragment' ? 'experience' : 'fact')
      : selection?.kind === 'cell' ? 'fact'
      : selection?.kind === 'topic' ? 'topic' : selection?.kind === 'soul' ? 'continuity_hub' : selection?.kind === 'month' ? 'month' : null;
    const action = nextMemoryUnwind({
      toolsOpen: false, hasActiveCriteria: false, listVisible: false, selectedKind: kind, expanded: Boolean(focusTopic), closeAtRoot,
    });
    if (action === 'evidence-to-topic') { if (focusTopic) select({ kind: 'topic', id: focusTopic }); else overview(); }
    else if (action === 'topic-to-overview' || action === 'collapse-branch') overview();
    else if (action === 'close-inspector') setSelection(null);
    else if (action === 'close-memory') requestClose();
  };

  const openArchiveRecord = (kind: 'fact' | 'experience', recordId: string) => {
    archiveOpenedRef.current = scopeKey;
    setMode('archive');
    archive.open(scope, { focus: { kind, recordId } });
  };
  const openArchiveTopic = (topicId: string) => {
    archiveOpenedRef.current = scopeKey;
    setMode('archive');
    // A month or cell the archive was last showing belongs to another visit; it would empty this topic.
    if (archive.open(scope, { restore: true })) archive.dispatch({ type: 'set-within', within: null, topicId });
  };
  const openArchiveWithin = (ids: readonly string[], label: string, topicId: string | null) => {
    archiveOpenedRef.current = scopeKey;
    setMode('archive');
    if (archive.open(scope, { restore: true })) archive.dispatch({ type: 'set-within', within: { label, ids }, topicId });
  };
  const showChart = () => {
    if (mode === 'archive' && !archive.handleExitRequest()) return;
    setMode('chart');
  };
  const retry = () => { setLoad({ status: 'loading', scopeKey }); setRefreshTick((tick) => tick + 1); };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { unwind(true); return; }
      if (event.key === '/' && memorySpaceShortcut(event) === 'search' && !(event.target as HTMLElement | null)?.isContentEditable) {
        event.preventDefault();
        setScopeMenu(false);
        setMode('archive');
        setSearchTick((tick) => tick + 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const crumbs: Array<{ key: string; label: string; onClick?: () => void }> = [];
  if (mode === 'chart' && model) {
    const topic = focusTopic ? model.groups.find((t) => t.id === focusTopic) : null;
    if (topic) crumbs.push({ key: 'topic', label: topic.label, onClick: () => select({ kind: 'topic', id: topic.id }) });
    if (selectedStar) crumbs.push({ key: 'star', label: recordNo(selectedStar.no) });
    if (selection?.kind === 'soul') crumbs.push({ key: 'soul', label: 'SOUL' });
    if (selection?.kind === 'month') crumbs.push({ key: 'month', label: selection.key.replace('-', '.') });
    if (selection?.kind === 'cell') crumbs.push({ key: 'cell', label: selection.key.split('|')[1].replace('-', '.') });
  }
  const deeper = mode === 'chart' ? crumbs.length > 0 : Boolean(archiveState.selection || archiveState.editor || archiveState.deleteFlow);

  const status = load.scopeKey !== scopeKey || load.status === 'loading' ? 'loading'
    : load.status === 'error' ? (load.error.kind === 'unavailable' ? 'unavailable' : 'error')
      : !model?.stars.length ? 'empty'
        : model.stars.length <= 6 && !model.topics.some((t) => t.starIds.length) ? 'new' : 'normal';
  const g = plateGeometry(size.width, size.height);
  const ringY = (theta: number) => (model ? platePoint(theta, 0, g.radius, model.thetaFit).y : 0);
  // On the cone the disc is drawn in perspective, so these notes sit low in the sheet instead of under a plate ring.
  const lowNote = size.height - 150;
  const message = status === 'loading' ? { text: '正在读取记忆…', top: surface === '3d' ? lowNote : g.cy + 52 }
    : status === 'empty' && model ? { text: '这里还没有长期记忆；可以返回对话，或稍后再来查看。', top: surface === '3d' ? lowNote : g.cy + ringY(model.thetaToday) + 26 }
      : status === 'new' && model ? { text: `已经记下 ${model.stars.length} 件事。记得多了以后，会连成主题。`, top: surface === '3d' ? lowNote : g.cy + ringY(model.thetaToday) + 30 }
        : null;
  const shownRecords = chart ? chart.facts.length + chart.experiences.length : 0;
  const totalRecords = chart ? chart.counts.facts + chart.counts.experiences : 0;
  const footnote = chart?.truncated || shownRecords < totalRecords ? `星图只画了其中 ${shownRecords} / ${totalRecords} 条；完整的在档案里。` : null;
  const today = ready?.today ?? Date.now();
  const linked = scopeKey === linkScopeRef.current;
  const markedOnPlate = useMemo(() => (linked && model ? marks.filter((id) => model.byId.has(id)) : []), [linked, model, marks]);
  const showHint = hintAt !== null && linked && mode === 'chart' && Boolean(model);
  const clearMarks = () => { setMarks([]); setHintAt(null); };

  return (
    <section
      ref={sheetRef}
      className="memory-space"
      data-testid="memory-space"
      data-worldline={worldline}
      data-mode={mode}
      data-state={status}
      data-reduced-motion={String(reducedMotion)}
      aria-label="Memory Space"
      tabIndex={-1}
      onContextMenu={(event) => {
        if (mode !== 'chart') return;
        const target = event.target as HTMLElement;
        if (target.closest('input, textarea, select, [contenteditable="true"]') || window.getSelection()?.toString()) return;
        event.preventDefault();
        unwind(false);
      }}
    >
      <div className="m-grid" aria-hidden="true" />
      <div className="m-vignette" aria-hidden="true" />
      <div className="m-frame" aria-hidden="true">
        {(['top', 'bottom'] as const).map((side) => (
          <div key={side} className={`m-zones ${side}`}>{[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <span key={n}>{n}</span>)}</div>
        ))}
        {(['left', 'right'] as const).map((side) => (
          <div key={side} className={`m-zones ${side}`}>{'ABCDEF'.split('').map((n) => <span key={n}>{n}</span>)}</div>
        ))}
      </div>

      <nav className="m-nav" aria-label="记忆空间">
        <button type="button" className="m-back" data-testid="memory-back" onClick={() => unwind(true)}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" aria-hidden="true"><path d="M8.5 2.5 4 7l4.5 4.5M4 7h9" /></svg>
          <span>{deeper ? '回退' : '回到对话'}</span>
        </button>
        <div className="m-tabs" role="tablist" aria-label="记忆空间视图">
          <button type="button" role="tab" aria-selected={mode === 'chart'} data-testid="memory-tab-chart" onClick={showChart}>星图 <small>CHART</small></button>
          <button type="button" role="tab" aria-selected={mode === 'archive'} data-testid="memory-tab-archive" onClick={() => { setScopeMenu(false); setMode('archive'); }}>档案 <small>INDEX</small></button>
        </div>
        {mode === 'chart' ? (
          <div className="m-display" role="group" aria-label="星图的显示方式" data-testid="memory-display">
            <button type="button" aria-pressed={surface === '3d'} data-testid="memory-display-3d" onClick={() => choose('3d')}>立体</button>
            <button type="button" aria-pressed={surface === 'plate'} data-testid="memory-display-plate" onClick={() => choose('plate')}>图版</button>
          </div>
        ) : null}
        {crumbs.length ? (
          <div className="m-crumbs" data-testid="memory-crumbs">
            <button type="button" onClick={overview}>总览</button>
            {crumbs.map((crumb) => (
              <span key={crumb.key} className="m-crumb">
                <span className="sep" aria-hidden="true">/</span>
                {crumb.onClick ? <button type="button" onClick={crumb.onClick}>{crumb.label}</button> : <span>{crumb.label}</span>}
              </span>
            ))}
          </div>
        ) : null}
      </nav>

      {mode === 'chart' ? (
        <>
          <div className="m-tblock" data-testid="memory-titleblock">
            <span className="k">PLATE</span><span className="v">MEMORY · {wlShort(worldline)}</span>
            <span className="k">SCOPE</span>
            <span className="v">
              <button type="button" className="m-scope" data-testid="memory-scope" aria-haspopup="listbox" aria-expanded={scopeMenu}
                onClick={() => setScopeMenu((open) => !open)}>
                <ScopeName worldline={worldline} identityMode={identityMode} />
              </button>
            </span>
            <span className="k">SINCE</span>
            <span className="v" data-testid="memory-since">{model ? (model.origin !== null ? ymd(model.origin) : '----.--.--') : '--'}</span>
            <span className="k">STARS</span>
            <span className="v" data-testid="memory-stars">{chart ? `${chart.counts.facts} + ${chart.counts.experiences}` : '--'}</span>
            <span className="k">DATE</span><span className="v">{ymd(today)}</span>
          </div>
          {scopeMenu ? (
            <div className="m-scope-menu" data-testid="memory-scope-menu">
              <ul role="listbox" aria-label="浏览范围">
                {SCOPES.map((option) => {
                  const on = option.worldline === worldline && option.identityMode === identityMode;
                  return (
                    <li key={`${option.identityMode}-${option.worldline}`} role="option" aria-selected={on} tabIndex={0}
                      onClick={() => { setWorldline(option.worldline); setIdentityMode(option.identityMode); setScopeMenu(false); }}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter' && event.key !== ' ') return;
                        event.preventDefault();
                        setWorldline(option.worldline); setIdentityMode(option.identityMode); setScopeMenu(false);
                      }}>
                      <ScopeName {...option} />
                    </li>
                  );
                })}
              </ul>
              <p>只是浏览，对话不变</p>
            </div>
          ) : null}

          <div className="m-chart" data-testid="memory-graph">
            {coneMounted ? (
              <MemoryCone
                key={coneKey}
                ref={coneRef}
                model={model}
                width={size.width}
                height={size.height}
                selection={selection}
                focusTopic={focusTopic}
                reducedMotion={reducedMotion}
                entering={entering}
                startFlat={coneFlat}
                timed={capability.reason === 'healthy'}
                worldline={worldline}
                footnote={footnote}
                marks={markedOnPlate}
                onEntered={() => setEntering(false)}
                onSelect={select}
                onOverview={overview}
                onUnavailable={onConeUnavailable}
                onContextLost={onConeLost}
                onSlow={onConeSlow}
              />
            ) : null}
            {plateMounted ? (
              <div className={`m-layer${plateOpaque ? '' : ' is-hidden'}`} data-surface-layer="plate">
                <MemoryPlate
                  model={model}
                  soul={status === 'loading' ? 'loading' : status === 'unavailable' || status === 'error' ? 'muted' : 'ready'}
                  width={size.width}
                  height={size.height}
                  selection={selection}
                  focusTopic={focusTopic}
                  framedTopic={framedTopic}
                  reducedMotion={reducedMotion}
                  entering={entering}
                  worldline={worldline}
                  footnote={footnote}
                  marks={markedOnPlate}
                  onEntered={() => setEntering(false)}
                  onSelect={select}
                  onOverview={overview}
                />
              </div>
            ) : null}
            {capability.notice ? (
              <p className="m-fallback" role="status" data-testid="memory-fallback" data-reason={capability.notice.kind}>
                <span>{capability.notice.text}</span>
                {capability.notice.action ? (
                  <button type="button" data-testid="memory-fallback-action" onClick={noticeAction}>{capability.notice.action}</button>
                ) : null}
              </p>
            ) : null}
            {showHint && hintAt !== null ? (
              <p className="m-ref-hint" data-testid="memory-ref-hint">
                <span>{markedOnPlate.length ? `这一轮参照了 ${markedOnPlate.length} 条 · ${hm(hintAt)}` : '这一轮参照的记忆已不在星图上'}</span>
                <button type="button" data-testid="memory-ref-clear" onClick={clearMarks}>清除</button>
              </p>
            ) : null}
            {message ? (
              <p className="m-state" data-testid={`memory-${status}`} style={{ top: Math.round(message.top), width: g.cx * 2 }}>{message.text}</p>
            ) : null}
            {status === 'unavailable' ? (
              <section className="m-status" role="alert" data-testid="memory-unavailable" style={{ left: g.cx }}>
                <h2>记忆存储暂时不可用</h2>
                <div className="acts">
                  <button type="button" className="m-btn solid" data-testid="memory-retry" onClick={retry}>重试</button>
                  <button type="button" className="m-btn" onClick={requestClose}>返回对话</button>
                </div>
              </section>
            ) : null}
            {status === 'error' ? (
              <section className="m-status" role="alert" data-testid="memory-error" style={{ left: g.cx }}>
                <h2>无法读取记忆。</h2>
                <p>记忆本身没有改动。</p>
                <div className="acts"><button type="button" className="m-btn solid" data-testid="memory-retry" onClick={retry}>重试</button></div>
              </section>
            ) : null}
            {model && chart && selection ? (
              <MemoryReading
                selection={selection}
                model={model}
                chart={chart}
                details={shownDetails}
                width={size.width}
                height={size.height}
                surface={surface}
                onSelect={select}
                onClose={() => unwind(false)}
                onRetryDetails={retryDetails}
                onOpenArchive={openArchiveRecord}
                onOpenArchiveTopic={openArchiveTopic}
                onOpenArchiveWithin={openArchiveWithin}
              />
            ) : null}
          </div>
        </>
      ) : (
        <ArchiveView module={archive} scope={scope} width={size.width} reducedMotion={reducedMotion} />
      )}
    </section>
  );
}
