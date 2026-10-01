import {
  correctFactText,
  deleteExperience,
  deleteFact,
  fetchExperiencesPage,
  fetchFactsPage,
  fetchMemoryStatus,
  fetchObservations,
  fetchTopics,
  ignoreObservation,
  renameTopic,
  retryFailedJob,
  updateExperiencePresentation,
  updateFactPresentation,
  type ArchiveScope,
  type ExperienceBrowseRow,
  type FactBrowseRow,
  type FailedJobRow,
  type MemoryStatusPayload,
  type ObservationRow,
  type TopicRow,
} from './archiveApi';
import {
  fetchDetails,
  invalidateDetailsCache,
  NOT_FOUND_NOTICE,
  type DetailsCache,
  type DetailsPayload,
} from './readingDetails';

// S4-ARCHIVE Gate 2 deep module (§6): one scoped record-governance seam.
// archive.open(scope, restore?) -> ArchiveState; archive.dispatch(intent).
// The module owns epochs/abort, exact-scope cache keys, independent
// pagination, details reuse/invalidation, conflict handling, deletion purge,
// per-scope restore, and honest outcome mapping. Existing HTTP endpoints are
// the real adapters; no projection request is ever issued from here.

export type ArchiveRecordKind = 'fact' | 'experience';

export type ArchiveListStatus = 'idle' | 'loading' | 'ready' | 'failed';

export type FactListState = {
  status: ArchiveListStatus;
  rows: FactBrowseRow[];
  total: number;
  offset: number;
  hasMore: boolean;
  errorCode: string | null;
};

export type ExperienceListState = {
  status: ArchiveListStatus;
  rows: ExperienceBrowseRow[];
  total: number;
  offset: number;
  hasMore: boolean;
  errorCode: string | null;
};

export type ArchiveSelection = { kind: ArchiveRecordKind; recordId: string } | null;

export type ArchiveDetailsState = {
  status: 'idle' | 'loading' | 'ready' | 'not-found' | 'failed';
  payload: DetailsPayload | null;
  errorCode: string | null;
};

export type ArchiveEditorState = {
  factId: string;
  draft: string;
  baseVersion: number;
  status: 'editing' | 'saving' | 'conflict';
  conflict: { serverText: string; serverVersion: number } | null;
} | null;

export type ArchiveDeleteFlow = {
  kind: ArchiveRecordKind;
  recordId: string;
  summary: string;
  scopeLabel: string;
  expectedVersion?: number;
  status: 'confirming' | 'deleting' | 'failed';
  errorCode: string | null;
} | null;

export type ArchiveTopicRename = { topicId: string; draft: string; saving: boolean } | null;

// §4.14: closing/switching with unsaved Fact edits requires an explicit
// discard choice. This product state (never window.confirm) names the
// original action that will run only after the user discards.
export type ArchiveDiscardAction = 'back' | 'exit-archive' | 'scope-switch';
export type ArchiveDiscardConfirm = { action: ArchiveDiscardAction } | null;

export type ArchivePendingState = {
  status: ArchiveListStatus;
  observations: ObservationRow[];
  failedJobs: FailedJobRow[];
  counts: MemoryStatusPayload | null;
  // Per-side outcome truth (P2): a failure must never masquerade as the
  // empty state, and a successful side must survive the other side failing.
  observationsFailed: boolean;
  statusFailed: boolean;
};

export type ArchiveFilters = {
  query: string;
  topicId: string | null;
  pinnedOnly: boolean;
};

export type ArchiveState = {
  scope: ArchiveScope | null;
  activeKind: ArchiveRecordKind;
  filters: ArchiveFilters;
  facts: FactListState;
  experiences: ExperienceListState;
  topics: { status: ArchiveListStatus; items: TopicRow[] };
  selection: ArchiveSelection;
  details: ArchiveDetailsState;
  editor: ArchiveEditorState;
  deleteFlow: ArchiveDeleteFlow;
  topicRename: ArchiveTopicRename;
  discardConfirm: ArchiveDiscardConfirm;
  pending: ArchivePendingState;
  announcement: string;
  focusFallback: string | null;
};

export type ArchiveIntent =
  | { type: 'set-kind'; kind: ArchiveRecordKind }
  | { type: 'set-query'; query: string }
  | { type: 'set-topic'; topicId: string | null }
  | { type: 'set-pinned-only'; pinnedOnly: boolean }
  | { type: 'page'; kind: ArchiveRecordKind; offset: number }
  | { type: 'select'; kind: ArchiveRecordKind; recordId: string }
  | { type: 'open-editor' }
  | { type: 'edit-input'; text: string }
  | { type: 'save-edit' }
  | { type: 'discard-draft' }
  | { type: 'reapply-draft' }
  | { type: 'toggle-pin'; kind: ArchiveRecordKind; recordId: string }
  | { type: 'assign-topic'; topicId: string | null }
  | { type: 'request-delete' }
  | { type: 'cancel-delete' }
  | { type: 'confirm-delete' }
  | { type: 'rename-topic-start'; topicId: string }
  | { type: 'rename-topic-input'; text: string }
  | { type: 'rename-topic-save' }
  | { type: 'rename-topic-cancel' }
  | { type: 'ignore-observation'; observationId: string }
  | { type: 'retry-job'; jobId: string }
  | { type: 'confirm-discard' }
  | { type: 'cancel-discard' }
  | { type: 'back' };

export type ArchiveOpenOptions = {
  restore?: boolean;
  focus?: { kind: ArchiveRecordKind; recordId: string };
};

export type ArchiveModule = {
  /** Open (or re-scope) the Archive. Returns false when a pending draft
   *  demands an explicit discard choice first — the caller must not assume
   *  the switch happened. */
  open(scope: ArchiveScope, options?: ArchiveOpenOptions): boolean;
  dispatch(intent: ArchiveIntent): void;
  /** Semantic Back. Returns true when Archive consumed the gesture. */
  handleBack(): boolean;
  /** Peer-mode exit request. Returns true when the caller may switch away
   *  immediately; false when a discard confirmation was opened instead. */
  handleExitRequest(): boolean;
  subscribe(listener: () => void): () => void;
  snapshot(): ArchiveState;
  dispose(): void;
};

export const PAGE_SIZE = 20;

const FOCUS_FALLBACK_NOTICE = '无法在此范围内找到该记录，已返回档案列表。';

function scopeKey(scope: ArchiveScope): string {
  return `${scope.sessionId}|${scope.worldline}|${scope.identityMode}`;
}

export function scopeLabel(scope: ArchiveScope): string {
  const identity = scope.identityMode === 'self' ? 'USER' : 'OKABE';
  const line = scope.worldline === 'steins_gate' ? 'SG' : 'β';
  return `${identity} · ${line}`;
}

function initialFacts(): FactListState {
  return { status: 'idle', rows: [], total: 0, offset: 0, hasMore: false, errorCode: null };
}

function initialExperiences(): ExperienceListState {
  return { status: 'idle', rows: [], total: 0, offset: 0, hasMore: false, errorCode: null };
}

function initialState(scope: ArchiveScope | null): ArchiveState {
  return {
    scope,
    activeKind: 'fact',
    filters: { query: '', topicId: null, pinnedOnly: false },
    facts: initialFacts(),
    experiences: initialExperiences(),
    topics: { status: 'idle', items: [] },
    selection: null,
    details: { status: 'idle', payload: null, errorCode: null },
    editor: null,
    deleteFlow: null,
    topicRename: null,
    discardConfirm: null,
    pending: {
      status: 'idle',
      observations: [],
      failedJobs: [],
      counts: null,
      observationsFailed: false,
      statusFailed: false,
    },
    announcement: '',
    focusFallback: null,
  };
}

type RestoreEntry = {
  filters: ArchiveFilters;
  activeKind: ArchiveRecordKind;
  selection: ArchiveSelection;
};

export function createArchiveModule(deps?: {
  fetcher?: typeof fetch;
  detailsCache?: DetailsCache;
  /** Notified after a backend erasure (or honest 404 purge) so peer surfaces
   *  (Constellation reading outcome / activation / projection) purge the same
   *  record. The projection itself is re-fetched from the backend — never
   *  edited client-side. */
  onRecordErased?: (kind: ArchiveRecordKind, recordId: string) => void;
  /** Notified when a confirmed discard completes an exit-archive action. */
  onExitArchive?: () => void;
  /** Notified when a confirmed discard completes a deferred scope switch. */
  onScopeChangeSettled?: () => void;
}): ArchiveModule {
  const fetcher = deps?.fetcher;
  // Injected shared P1R-3 cache: successful Archive erasures/mutations purge
  // the same entries a Constellation reading controller may still retain.
  const detailsCache: DetailsCache = deps?.detailsCache ?? new Map();
  const listeners = new Set<() => void>();
  const restoreStore = new Map<string, RestoreEntry>();

  let state = initialState(null);
  let epoch = 0;
  // Latest-request-wins guards, one per record kind: query/filter/page all
  // bump their kind's sequence, so an older in-flight response can never
  // commit over newer results. Scope switches keep the global epoch + abort.
  let factsSeq = 0;
  let experiencesSeq = 0;
  // Same latest-request-wins discipline for details and pending loads:
  // A→B→A details re-selection must not let the FIRST A response win, and
  // overlapping pending loads must never commit an older observations/status
  // pair over the newest one. Scope switches invalidate both counters.
  let detailsSeq = 0;
  let pendingSeq = 0;
  let inflight: AbortController | null = null;
  let disposed = false;
  // Deferred while a pending draft waits for an explicit discard choice.
  let pendingScopeChange: { scope: ArchiveScope; options: ArchiveOpenOptions } | null = null;

  function notify() {
    state = { ...state };
    for (const listener of listeners) listener();
  }

  function persistRestore() {
    // Persist committed per-scope state for restore. Uncommitted mutation
    // state (editor/deleteFlow/topicRename) is never part of a restore entry,
    // and async loading transitions must not snapshot intermediate selection.
    if (state.scope) {
      restoreStore.set(scopeKey(state.scope), {
        filters: { ...state.filters },
        activeKind: state.activeKind,
        selection: state.selection ? { ...state.selection } : null,
      });
    }
  }

  function commit(partial: Partial<ArchiveState>, options: { persist?: boolean } = {}) {
    Object.assign(state, partial);
    if (options.persist !== false) persistRestore();
    notify();
  }

  function stale(guardEpoch: number): boolean {
    return disposed || guardEpoch !== epoch;
  }

  async function loadFacts() {
    if (!state.scope) return;
    const scope = state.scope;
    const offset = state.facts.offset;
    const mySeq = ++factsSeq;
    commit({ facts: { ...state.facts, status: 'loading', errorCode: null } }, { persist: false });
    const result = await fetchFactsPage(
      scope,
      {
        query: state.filters.query || undefined,
        topicId: state.filters.topicId,
        pinnedOnly: state.filters.pinnedOnly,
        limit: PAGE_SIZE,
        offset,
      },
      { fetcher, signal: inflight?.signal },
    );
    if (disposed || mySeq !== factsSeq || scopeKey(scope) !== scopeKey(state.scope)) return;
    if (result.type === 'ok') {
      commit({
        facts: {
          status: 'ready',
          rows: result.payload.rows,
          total: result.payload.total,
          offset,
          hasMore: result.payload.hasMore,
          errorCode: null,
        },
      }, { persist: false });
      return;
    }
    commit({
      facts: {
        ...state.facts,
        status: 'failed',
        errorCode: result.type === 'backend-failure' ? result.code : 'transport_error',
      },
    }, { persist: false });
  }

  async function loadExperiences() {
    if (!state.scope) return;
    const scope = state.scope;
    const offset = state.experiences.offset;
    const mySeq = ++experiencesSeq;
    commit({ experiences: { ...state.experiences, status: 'loading', errorCode: null } }, { persist: false });
    const result = await fetchExperiencesPage(
      scope,
      {
        query: state.filters.query || undefined,
        pinnedOnly: state.filters.pinnedOnly,
        limit: PAGE_SIZE,
        offset,
      },
      { fetcher, signal: inflight?.signal },
    );
    if (disposed || mySeq !== experiencesSeq || scopeKey(scope) !== scopeKey(state.scope)) return;
    if (result.type === 'ok') {
      commit({
        experiences: {
          status: 'ready',
          rows: result.payload.rows,
          total: result.payload.total,
          offset,
          hasMore: result.payload.hasMore,
          errorCode: null,
        },
      }, { persist: false });
      return;
    }
    commit({
      experiences: {
        ...state.experiences,
        status: 'failed',
        errorCode: result.type === 'backend-failure' ? result.code : 'transport_error',
      },
    }, { persist: false });
  }

  async function loadTopics(guardEpoch: number) {
    if (!state.scope) return;
    const scope = state.scope;
    const result = await fetchTopics(scope, { fetcher, signal: inflight?.signal });
    if (stale(guardEpoch) || scopeKey(scope) !== scopeKey(state.scope)) return;
    if (result.type === 'ok') {
      commit({ topics: { status: 'ready', items: result.payload } }, { persist: false });
      return;
    }
    commit({ topics: { status: 'failed', items: [] } }, { persist: false });
  }

  async function loadPending(guardEpoch: number) {
    if (!state.scope) return;
    const scope = state.scope;
    const mySeq = ++pendingSeq;
    commit({ pending: { ...state.pending, status: 'loading' } }, { persist: false });
    const [observations, status] = await Promise.all([
      fetchObservations(scope, { fetcher, signal: inflight?.signal }),
      fetchMemoryStatus(scope, { fetcher, signal: inflight?.signal }),
    ]);
    if (
      stale(guardEpoch)
      || mySeq !== pendingSeq
      || scopeKey(scope) !== scopeKey(state.scope)
    ) {
      return;
    }
    const observationsOk = observations.type === 'ok';
    const statusOk = status.type === 'ok';
    commit({
      pending: {
        status: 'ready',
        observations: observationsOk ? observations.payload : [],
        failedJobs: statusOk ? status.payload.failedJobs : [],
        counts: statusOk ? status.payload : null,
        // Per-side failure truth: a failed seam is never an empty result.
        observationsFailed: !observationsOk,
        statusFailed: !statusOk,
      },
    }, { persist: false });
  }

  async function loadDetails(
    selection: NonNullable<ArchiveSelection>,
    guardEpoch: number,
    options: { deepLink?: boolean } = {},
  ) {
    if (!state.scope) return;
    const scope = state.scope;
    const mySeq = ++detailsSeq;
    commit({
      selection,
      details: { status: 'loading', payload: null, errorCode: null },
      focusFallback: null,
    }, { persist: false });
    const result = await fetchDetails(
      {
        scope: {
          sessionId: scope.sessionId,
          worldline: scope.worldline,
          identityMode: scope.identityMode,
        },
        kind: selection.kind,
        recordId: selection.recordId,
      },
      { fetcher, signal: inflight?.signal },
    );
    if (
      stale(guardEpoch)
      || mySeq !== detailsSeq
      || scopeKey(scope) !== scopeKey(state.scope)
    ) {
      return;
    }
    const stillSelected =
      state.selection?.kind === selection.kind && state.selection.recordId === selection.recordId;
    if (!stillSelected) return;
    if (result.type === 'ok') {
      commit({ details: { status: 'ready', payload: result.payload, errorCode: null } });
      return;
    }
    if (result.type === 'not-found') {
      commit({
        // Deep-link targets that are missing/inaccessible fall back to the
        // scoped list with honest copy; never rebuilt from projection text.
        // The detail pane closes fully so a narrow screen returns to the
        // scoped list instead of lingering on an empty surface.
        selection: options.deepLink ? null : selection,
        details: options.deepLink
          ? { status: 'idle', payload: null, errorCode: null }
          : { status: 'not-found', payload: null, errorCode: null },
        focusFallback: options.deepLink ? FOCUS_FALLBACK_NOTICE : null,
        announcement: NOT_FOUND_NOTICE,
      });
      return;
    }
    commit({
      details: {
        status: 'failed',
        payload: null,
        errorCode: result.type === 'backend-failure' ? result.code : 'transport_error',
      },
    }, { persist: false });
  }

  function selectedRowText(): { text: string; version?: number } | null {
    // A star-map deep link can open a record outside the current list page.
    // Matching, scope-validated details still provide its text and revision.
    if (!state.selection) return null;
    if (state.selection.kind === 'fact') {
      const row = state.facts.rows.find((item) => item.factId === state.selection?.recordId);
      if (!row) {
        const payload = state.details.status === 'ready' ? state.details.payload : null;
        return payload && 'fact' in payload && payload.fact.fact_id === state.selection.recordId
          ? { text: payload.fact.display_text, version: payload.fact.active_version }
          : null;
      }
      return { text: row.displayText, version: row.activeVersion };
    }
    const row = state.experiences.rows.find(
      (item) => item.experienceId === state.selection?.recordId,
    );
    if (row) return { text: row.displayText };
    const payload = state.details.status === 'ready' ? state.details.payload : null;
    return payload && 'experience' in payload && payload.experience.experience_id === state.selection.recordId
      ? { text: payload.experience.display_text }
      : null;
  }

  function refreshAfterMutation(guardEpoch: number) {
    void loadFacts();
    void loadExperiences();
    void loadTopics(guardEpoch);
    if (state.selection) void loadDetails(state.selection, guardEpoch);
  }

  const SAVING_NOTICE = '正在保存，请稍候';

  function open(scope: ArchiveScope, options: ArchiveOpenOptions = {}): boolean {
    // Re-arm after a StrictMode-style dispose: React's double-mount runs the
    // unmount cleanup (dispose) and then remounts the SAME useRef instance.
    // The explicit open() re-enables the lifecycle on that instance — no
    // second module state is ever created. Old in-flight responses stay
    // invalidated: epoch and every kind/sequence counter are bumped below,
    // so any completion racing this re-open is discarded before it commits.
    // A true unmount (dispose without a later open) keeps dropping responses.
    disposed = false;
    // A save in flight must not be raced into a discard or a scope reset.
    if (state.editor?.status === 'saving') {
      commit({ announcement: SAVING_NOTICE }, { persist: false });
      return false;
    }
    // §4.14: a scope switch must never silently lose an unsaved draft —
    // defer it behind the explicit discard confirmation instead.
    if (state.scope && state.editor && scopeKey(scope) !== scopeKey(state.scope)) {
      commit({ discardConfirm: { action: 'scope-switch' } }, { persist: false });
      pendingScopeChange = { scope, options };
      return false;
    }
    epoch += 1;
    // Invalidate every kind-scoped request from the previous scope too.
    factsSeq += 1;
    experiencesSeq += 1;
    detailsSeq += 1;
    pendingSeq += 1;
    inflight?.abort();
    inflight = new AbortController();
    const guardEpoch = epoch;
    const key = scopeKey(scope);
    state = initialState(scope);
    if (options.restore) {
      const entry = restoreStore.get(key);
      if (entry) {
        state.filters = { ...entry.filters };
        state.activeKind = entry.activeKind;
        state.selection = entry.selection ? { ...entry.selection } : null;
      }
    }
    notify();
    void loadFacts();
    void loadExperiences();
    void loadTopics(guardEpoch);
    void loadPending(guardEpoch);
    const focus = options.focus ?? state.selection;
    if (focus) {
      void loadDetails(focus, guardEpoch, { deepLink: Boolean(options.focus) });
    }
    return true;
  }

  function dispatch(intent: ArchiveIntent) {
    if (!state.scope) return;
    if (state.pending.counts?.runtimeMode !== 'v11' && [
      'open-editor', 'save-edit', 'reapply-draft', 'assign-topic', 'toggle-pin',
      'request-delete', 'confirm-delete', 'rename-topic-start', 'rename-topic-save',
      'ignore-observation', 'retry-job',
    ].includes(intent.type)) {
      commit({ announcement: '尚未确认档案与当前对话使用同一套记忆，暂时只能查看。' });
      return;
    }
    const guardEpoch = epoch;
    switch (intent.type) {
      case 'set-kind':
        commit({ activeKind: intent.kind });
        return;
      case 'set-query':
        commit({
          filters: { ...state.filters, query: intent.query },
          facts: { ...state.facts, offset: 0 },
          experiences: { ...state.experiences, offset: 0 },
        });
        void loadFacts();
        void loadExperiences();
        return;
      case 'set-topic':
        commit({
          filters: { ...state.filters, topicId: intent.topicId },
          facts: { ...state.facts, offset: 0 },
        });
        void loadFacts();
        return;
      case 'set-pinned-only':
        commit({
          filters: { ...state.filters, pinnedOnly: intent.pinnedOnly },
          facts: { ...state.facts, offset: 0 },
          experiences: { ...state.experiences, offset: 0 },
        });
        void loadFacts();
        void loadExperiences();
        return;
      case 'page':
        if (intent.kind === 'fact') {
          commit({ facts: { ...state.facts, offset: intent.offset } });
          void loadFacts();
        } else {
          commit({ experiences: { ...state.experiences, offset: intent.offset } });
          void loadExperiences();
        }
        return;
      case 'select':
        void loadDetails({ kind: intent.kind, recordId: intent.recordId }, guardEpoch);
        return;
      case 'open-editor': {
        // Only Facts have a text editor, and only one explicit mutation
        // workflow may be pending at a time.
        if (state.selection?.kind !== 'fact' || state.editor || state.deleteFlow) return;
        const record = selectedRowText();
        if (!record || typeof record.version !== 'number') return;
        commit({
          editor: {
            factId: state.selection.recordId,
            draft: record.text,
            baseVersion: record.version,
            status: 'editing',
            conflict: null,
          },
        });
        return;
      }
      case 'edit-input':
        if (!state.editor || state.editor.status !== 'editing') return;
        commit({ editor: { ...state.editor, draft: intent.text } });
        return;
      case 'save-edit':
        void saveEdit(guardEpoch);
        return;
      case 'discard-draft':
        // Explicit discard invalidates any in-flight save completion.
        saveGeneration += 1;
        commit({ editor: null });
        return;
      case 'reapply-draft':
        // Deliberate reapply against the newly reviewed backend version.
        if (!state.editor || state.editor.status !== 'conflict' || !state.editor.conflict) return;
        commit({
          editor: {
            ...state.editor,
            status: 'editing',
            baseVersion: state.editor.conflict.serverVersion,
            conflict: null,
          },
        });
        return;
      case 'toggle-pin':
        void togglePin(intent.kind, intent.recordId, guardEpoch);
        return;
      case 'assign-topic':
        void assignTopic(intent.topicId, guardEpoch);
        return;
      case 'request-delete': {
        if (state.editor || state.deleteFlow || !state.selection) return;
        const row = selectedRowText();
        if (!row || !state.scope) return;
        commit({
          deleteFlow: {
            kind: state.selection.kind,
            recordId: state.selection.recordId,
            summary: row.text,
            scopeLabel: scopeLabel(state.scope),
            expectedVersion: row.version,
            status: 'confirming',
            errorCode: null,
          },
        });
        return;
      }
      case 'cancel-delete':
        commit({ deleteFlow: null });
        return;
      case 'confirm-delete':
        void confirmDelete(guardEpoch);
        return;
      case 'rename-topic-start': {
        const topic = state.topics.items.find((item) => item.topicId === intent.topicId);
        if (!topic) return;
        commit({ topicRename: { topicId: topic.topicId, draft: topic.displayLabel, saving: false } });
        return;
      }
      case 'rename-topic-input':
        if (!state.topicRename) return;
        commit({ topicRename: { ...state.topicRename, draft: intent.text } });
        return;
      case 'rename-topic-save':
        void saveTopicRename(guardEpoch);
        return;
      case 'rename-topic-cancel':
        commit({ topicRename: null });
        return;
      case 'confirm-discard': {
        // Explicit discard: drop the draft, then execute the original action.
        const action = state.discardConfirm?.action;
        if (!action) return;
        saveGeneration += 1;
        commit({ editor: null, discardConfirm: null });
        if (action === 'exit-archive') {
          deps?.onExitArchive?.();
          return;
        }
        if (action === 'scope-switch') {
          const deferred = pendingScopeChange;
          pendingScopeChange = null;
          if (deferred && open(deferred.scope, deferred.options)) {
            deps?.onScopeChangeSettled?.();
          }
          return;
        }
        return;
      }
      case 'cancel-discard': {
        // Continue editing: keep the draft AND the deferred/exit action.
        commit({ discardConfirm: null }, { persist: false });
        return;
      }
      case 'ignore-observation': {
        // Eligibility is backend-declared (status), never inferred from text.
        const target = state.pending.observations.find(
          (item) => item.observationId === intent.observationId,
        );
        if (!target || target.status !== 'candidate' || !state.scope) return;
        const scope = state.scope;
        void runAuxiliaryMutation(async () => {
          const result = await ignoreObservation(scope, intent.observationId, { fetcher });
          if (stale(guardEpoch)) return;
          if (result.type === 'ok' || result.type === 'validation') {
            commit({ announcement: '该观察项已忽略' });
          } else {
            commit({ announcement: '操作失败，观察项未变更' });
          }
          void loadPending(guardEpoch);
        });
        return;
      }
      case 'retry-job': {
        const target = state.pending.failedJobs.find((item) => item.jobId === intent.jobId);
        if (!target || target.retryable !== true || !state.scope) return;
        const scope = state.scope;
        void runAuxiliaryMutation(async () => {
          const result = await retryFailedJob(scope, intent.jobId, { fetcher });
          if (stale(guardEpoch)) return;
          if (result.type === 'ok') {
            // Honest outcome: the backend may legitimately return the job
            // UNCHANGED as failed (persisted non-retryable authority) — that
            // is never announced as a re-queue.
            const returnedState =
              typeof result.payload.state === 'string' ? result.payload.state : null;
            commit({
              announcement:
                returnedState === 'pending' ? '已重新排队处理' : '任务不可重试，状态未变',
            });
          } else {
            commit({ announcement: '重试失败，任务状态未变更' });
          }
          void loadPending(guardEpoch);
        });
        return;
      }
      case 'back':
        handleBack();
        return;
    }
  }

  // Superseded mutation completions (e.g. a late failure after an explicit
  // discard) must never write the editor back — generation + live-editor
  // identity are checked before every commit below.
  let saveGeneration = 0;
  // Single module-owned in-flight guard for presentation-class mutations
  // (pin, topic assign/clear, observation ignore, job retry): a second click
  // or a different presentation mutation NEVER sends a parallel request.
  // The next operation is accepted only after the current one completes.
  let auxiliaryMutationInFlight = false;

  async function saveEdit(guardEpoch: number) {
    const scope = state.scope;
    const editor = state.editor;
    if (!scope || !editor || editor.status !== 'editing') return;
    const myGeneration = ++saveGeneration;
    commit({ editor: { ...editor, status: 'saving' } });
    const result = await correctFactText(
      scope,
      editor.factId,
      { displayText: editor.draft, expectedVersion: editor.baseVersion },
      { fetcher },
    );
    if (stale(guardEpoch) || myGeneration !== saveGeneration) return;
    // The editor must still be THIS in-flight save; a discarded or replaced
    // editor is never overwritten by a late completion.
    if (
      !state.editor
      || state.editor.status !== 'saving'
      || state.editor.factId !== editor.factId
    ) {
      return;
    }
    if (result.type === 'ok') {
      // Success becomes the new authority: refresh row and details history.
      commit({ editor: null, announcement: '修订已保存' });
      if (state.selection?.kind === 'fact' && state.selection.recordId === editor.factId) {
        invalidateDetailsCache(detailsCache, {
          scope: { sessionId: scope.sessionId, worldline: scope.worldline, identityMode: scope.identityMode },
          kind: 'fact',
          recordId: editor.factId,
        });
      }
      refreshAfterMutation(guardEpoch);
      return;
    }
    if (result.type === 'conflict') {
      // Conflict: keep the draft locally, fetch fresh backend truth, and
      // never auto-retry / overwrite / merge.
      commit({
        editor: { ...editor, status: 'conflict' },
        announcement: '版本冲突：后端已有新版本，请先查看再决定',
      });
      const fresh = await fetchDetails(
        {
          scope: { sessionId: scope.sessionId, worldline: scope.worldline, identityMode: scope.identityMode },
          kind: 'fact',
          recordId: editor.factId,
        },
        { fetcher },
      );
      if (
        stale(guardEpoch)
        || myGeneration !== saveGeneration
        || fresh.type !== 'ok'
      ) {
        return;
      }
      const current = state.editor;
      if (!current || current.factId !== editor.factId || current.status !== 'conflict') return;
      // Fresh backend truth comes from the P1R-3 fact details payload.
      const freshText = 'fact' in fresh.payload ? fresh.payload.fact.display_text : current.draft;
      const freshVersion = 'fact' in fresh.payload ? fresh.payload.fact.active_version : current.baseVersion;
      commit({
        editor: {
          ...current,
          status: 'conflict',
          conflict: {
            serverText: freshText,
            serverVersion: freshVersion,
          },
        },
        details: { status: 'ready', payload: fresh.payload, errorCode: null },
      });
      return;
    }
    commit({
      editor: { ...editor, status: 'editing' },
      announcement: result.type === 'validation' ? '内容未通过校验，记录未变更' : '保存失败，记录未变更',
    });
  }

  async function runAuxiliaryMutation(run: () => Promise<void>): Promise<void> {
    if (auxiliaryMutationInFlight) return;
    auxiliaryMutationInFlight = true;
    try {
      await run();
    } finally {
      auxiliaryMutationInFlight = false;
    }
  }

  async function togglePin(kind: ArchiveRecordKind, recordId: string, guardEpoch: number) {
    const scope = state.scope;
    if (!scope || state.editor?.status === 'saving' || state.deleteFlow?.status === 'deleting') return;
    if (kind === 'fact') {
      const row = state.facts.rows.find((item) => item.factId === recordId);
      const payload = state.details.status === 'ready' ? state.details.payload : null;
      const isPinned = row?.isPinned ?? (payload && 'fact' in payload && payload.fact.fact_id === recordId
        ? payload.fact.is_pinned : null);
      if (isPinned === null) return;
      await runAuxiliaryMutation(async () => {
        const result = await updateFactPresentation(scope, recordId, { isPinned: !isPinned }, { fetcher });
        if (stale(guardEpoch)) return;
        commit({ announcement: result.type === 'ok' ? (!isPinned ? '已置顶' : '已取消置顶') : '置顶状态未变更' });
        refreshAfterMutation(guardEpoch);
      });
      return;
    }
    const row = state.experiences.rows.find((item) => item.experienceId === recordId);
    if (!row) return;
    await runAuxiliaryMutation(async () => {
      const result = await updateExperiencePresentation(scope, recordId, !row.isPinned, { fetcher });
      if (stale(guardEpoch)) return;
      commit({ announcement: result.type === 'ok' ? (!row.isPinned ? '已置顶' : '已取消置顶') : '置顶状态未变更' });
      refreshAfterMutation(guardEpoch);
    });
  }

  async function assignTopic(topicId: string | null, guardEpoch: number) {
    const scope = state.scope;
    if (!scope || state.selection?.kind !== 'fact') return;
    const factId = state.selection.recordId;
    await runAuxiliaryMutation(async () => {
      const result = await updateFactPresentation(scope, factId, { topicId }, { fetcher });
      if (stale(guardEpoch)) return;
      commit({ announcement: result.type === 'ok' ? '主题归属已更新' : '主题归属未变更' });
      refreshAfterMutation(guardEpoch);
    });
  }

  async function confirmDelete(guardEpoch: number) {
    const scope = state.scope;
    const flow = state.deleteFlow;
    if (!scope || !flow || flow.status !== 'confirming') return;
    commit({ deleteFlow: { ...flow, status: 'deleting' } });
    const result =
      flow.kind === 'fact' && typeof flow.expectedVersion === 'number'
        ? await deleteFact(scope, flow.recordId, flow.expectedVersion, { fetcher })
        : await deleteExperience(scope, flow.recordId, { fetcher });
    if (stale(guardEpoch)) return;
    if (result.type === 'ok' || result.type === 'not-found') {
      // Backend erasure succeeded (ok) or the record is already gone server-
      // side (404). Either way the readable local copies must go NOW — rows,
      // complete count, selection, details cache — never waiting on the async
      // list refresh (a failed refresh must not resurrect the deleted row).
      let purge: Partial<ArchiveState>;
      if (flow.kind === 'fact') {
        const rows = state.facts.rows.filter((row) => row.factId !== flow.recordId);
        purge = {
          facts: {
            ...state.facts,
            rows,
            total: rows.length < state.facts.rows.length
              ? Math.max(0, state.facts.total - 1)
              : state.facts.total,
          },
        };
      } else {
        const rows = state.experiences.rows.filter((row) => row.experienceId !== flow.recordId);
        purge = {
          experiences: {
            ...state.experiences,
            rows,
            total: rows.length < state.experiences.rows.length
              ? Math.max(0, state.experiences.total - 1)
              : state.experiences.total,
          },
        };
      }
      invalidateDetailsCache(detailsCache, {
        scope: { sessionId: scope.sessionId, worldline: scope.worldline, identityMode: scope.identityMode },
        kind: flow.kind,
        recordId: flow.recordId,
      });
      // Peer surfaces purge the same record; the projection refresh is
      // backend-authoritative (MemorySpace re-reads the projection seam).
      deps?.onRecordErased?.(flow.kind, flow.recordId);
      commit({
        ...purge,
        selection: null,
        details: { status: 'idle', payload: null, errorCode: null },
        deleteFlow: null,
        editor: null,
        // 404 stays honest: remnants are cleared, but the outcome never
        // claims deletion success and never discloses why it is unavailable.
        announcement:
          result.type === 'ok'
            ? '记忆已删除'
            : NOT_FOUND_NOTICE,
      });
      refreshAfterMutation(guardEpoch);
      void loadPending(guardEpoch);
      return;
    }
    commit({
      deleteFlow: {
        ...flow,
        status: 'failed',
        errorCode:
          result.type === 'conflict' || result.type === 'validation'
            ? result.code
            : result.type === 'backend-failure'
              ? result.code
              : 'transport_error',
      },
      announcement: '删除失败，记录未变更',
    });
  }

  async function saveTopicRename(guardEpoch: number) {
    const scope = state.scope;
    const rename = state.topicRename;
    if (!scope || !rename || rename.saving) return;
    const label = rename.draft.trim();
    if (!label) return;
    commit({ topicRename: { ...rename, saving: true } });
    const result = await renameTopic(scope, rename.topicId, label, { fetcher });
    if (stale(guardEpoch)) return;
    if (result.type === 'ok') {
      commit({ topicRename: null, announcement: '主题已重命名' });
      void loadTopics(guardEpoch);
      void loadFacts();
      return;
    }
    commit({
      topicRename: { ...rename, saving: false },
      announcement: '重命名失败，主题未变更',
    });
  }

  function handleBack(): boolean {
    if (state.topicRename) {
      commit({ topicRename: null });
      return true;
    }
    if (state.deleteFlow) {
      commit({ deleteFlow: null });
      return true;
    }
    if (state.editor?.status === 'saving') {
      // Fail-closed: a save in flight cannot be raced into a discard.
      commit({ announcement: SAVING_NOTICE }, { persist: false });
      return true;
    }
    if (state.editor) {
      // §4.14: Back never silently clears an unsaved draft — raise the
      // explicit discard confirmation and consume the gesture.
      commit({ discardConfirm: { action: 'back' } }, { persist: false });
      return true;
    }
    if (state.selection) {
      commit({
        selection: null,
        details: { status: 'idle', payload: null, errorCode: null },
      });
      return true;
    }
    return false;
  }

  function handleExitRequest(): boolean {
    // Fail-closed: no peer-mode exit while a save is in flight.
    if (state.editor?.status === 'saving') {
      commit({ announcement: SAVING_NOTICE }, { persist: false });
      return false;
    }
    // Peer-mode switch with an unsaved draft demands an explicit choice.
    if (state.editor) {
      commit({ discardConfirm: { action: 'exit-archive' } }, { persist: false });
      return false;
    }
    return true;
  }

  return {
    open,
    dispatch,
    handleBack,
    handleExitRequest,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    snapshot: () => state,
    dispose() {
      disposed = true;
      epoch += 1;
      inflight?.abort();
      inflight = null;
      listeners.clear();
    },
  };
}
