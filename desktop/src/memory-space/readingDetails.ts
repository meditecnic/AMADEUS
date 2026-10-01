import type { IdentityMode, MemoryProjection, Worldline } from './types';

// P1R-3 reading / verification seam (contract 45e2ec6).
// Field authority (§5.2): this module renders ONLY payload-owned content
// (active text, versions, provenance, expiry). Selection, scope, counts and
// Topic labels stay envelope-owned. Confidence is never read (§15.1).

export type DetailsScope = {
  sessionId: string;
  worldline: Worldline;
  identityMode: IdentityMode;
};

export type DetailsRecordKind = 'fact' | 'experience';

export type DetailsRequest = {
  scope: DetailsScope;
  kind: DetailsRecordKind;
  recordId: string;
};

// Frozen backend payload shapes (system_api.py L676 / L930). Confidence is
// deliberately absent from these types: it must never reach a render path.
export type FactVersionRow = {
  version_no: number;
  display_text: string;
  change_kind: string;
  previous_version: number | null;
  valid_from: string | null;
  invalid_at: string | null;
  is_active: boolean;
};

export type FactProvenanceRow = {
  version_no: number;
  observation_id: string;
  source_message_id: number;
  conversation_id: string;
  source_created_at: string | null;
  source_state: string;
  excerpt: string | null;
};

export type FactDetailsPayload = {
  scope: { session_id: string; worldline: Worldline; identity_mode: IdentityMode };
  fact: {
    fact_id: string;
    topic_id: string | null;
    is_pinned: boolean;
    active_version: number;
    display_text: string;
  };
  versions: FactVersionRow[];
  provenance: FactProvenanceRow[];
};

export type ExperienceProvenanceRow = {
  source_message_id: number;
  conversation_id: string;
  source_created_at: string | null;
  source_state: string;
  excerpt: string | null;
};

export type ExperienceDetailsPayload = {
  scope: { session_id: string; worldline: Worldline; identity_mode: IdentityMode };
  experience: {
    experience_id: string;
    conversation_id: string;
    display_text: string;
    status: string;
    expires_at: string | null;
    created_at: string | null;
    is_expired: boolean;
  };
  observation_id: string;
  provenance: ExperienceProvenanceRow[];
};

export type DetailsPayload = FactDetailsPayload | ExperienceDetailsPayload;

export type DetailsLoadResult =
  | { type: 'ok'; payload: DetailsPayload }
  | { type: 'not-found' }
  | { type: 'backend-failure'; code: string; status: number }
  | { type: 'transport-error' };

export type DetailsOutcome =
  | { type: 'idle' }
  | { type: 'loading'; request: DetailsRequest }
  | { type: 'ok'; request: DetailsRequest; payload: DetailsPayload }
  | { type: 'not-found'; request: DetailsRequest }
  | { type: 'backend-failure'; request: DetailsRequest; code: string; status: number }
  | { type: 'transport-error'; request: DetailsRequest };

export const CORRECTION_NOTICE = '当前视图只读，不能在此纠正';
export const DEEP_LINK_NOTICE = '尚不能跳转回来源会话';
export const NOT_FOUND_NOTICE = '该记忆在此范围内不再可用';
export const TOPIC_UNAVAILABLE_NOTICE = '当前投影未提供可显示的归属主题';
export const UNGROUPED_NOTICE = '未归组';

export function buildDetailsUrl(request: DetailsRequest): string {
  const base =
    request.kind === 'fact'
      ? `/api/memory/facts/${encodeURIComponent(request.recordId)}/details`
      : `/api/memory/experiences/${encodeURIComponent(request.recordId)}/details`;
  const params = new URLSearchParams({
    session_id: request.scope.sessionId,
    worldline: request.scope.worldline,
    identity_mode: request.scope.identityMode,
  });
  return `${base}?${params.toString()}`;
}

function readErrorCode(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const detail = (body as { detail?: { code?: unknown } }).detail;
  return typeof detail?.code === 'string' && detail.code.trim() ? detail.code : null;
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isNumberOrNull(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value));
}

function scopeMatches(body: unknown, request: DetailsRequest): boolean {
  if (!body || typeof body !== 'object') return false;
  const scope = (body as { scope?: unknown }).scope;
  if (!scope || typeof scope !== 'object') return false;
  const candidate = scope as { session_id?: unknown; worldline?: unknown; identity_mode?: unknown };
  return (
    candidate.session_id === request.scope.sessionId
    && candidate.worldline === request.scope.worldline
    && candidate.identity_mode === request.scope.identityMode
  );
}

function isValidFactProvenanceRow(row: unknown): boolean {
  if (!row || typeof row !== 'object') return false;
  const candidate = row as Partial<FactProvenanceRow>;
  return (
    typeof candidate.version_no === 'number'
    && typeof candidate.observation_id === 'string'
    && typeof candidate.source_message_id === 'number'
    && typeof candidate.conversation_id === 'string'
    && isStringOrNull(candidate.source_created_at)
    && typeof candidate.source_state === 'string'
    && isStringOrNull(candidate.excerpt)
  );
}

function isValidFactVersionRow(row: unknown): boolean {
  if (!row || typeof row !== 'object') return false;
  const candidate = row as Partial<FactVersionRow>;
  return (
    typeof candidate.version_no === 'number'
    && typeof candidate.display_text === 'string'
    && typeof candidate.change_kind === 'string'
    && isNumberOrNull(candidate.previous_version)
    && isStringOrNull(candidate.valid_from)
    && isStringOrNull(candidate.invalid_at)
    && typeof candidate.is_active === 'boolean'
  );
}

// P1 spec-5: every nested field read downstream is type-checked, and the
// payload must belong to the requested scope and record. Anything else is a
// fail-closed backend failure, never a partial render.
function isValidFactPayload(body: unknown, request: DetailsRequest): body is FactDetailsPayload {
  if (!body || typeof body !== 'object') return false;
  if (!scopeMatches(body, request)) return false;
  const candidate = body as Partial<FactDetailsPayload>;
  if (!candidate.fact || typeof candidate.fact !== 'object') return false;
  const fact = candidate.fact;
  if (
    fact.fact_id !== request.recordId
    || typeof fact.display_text !== 'string'
    || typeof fact.is_pinned !== 'boolean'
    || typeof fact.active_version !== 'number'
    || !(fact.active_version > 0)
    || !(fact.topic_id === null || typeof fact.topic_id === 'string')
  ) {
    return false;
  }
  if (!Array.isArray(candidate.versions) || !candidate.versions.every(isValidFactVersionRow)) return false;
  if (!Array.isArray(candidate.provenance) || !candidate.provenance.every(isValidFactProvenanceRow)) return false;
  return true;
}

function isValidExperienceProvenanceRow(row: unknown): boolean {
  if (!row || typeof row !== 'object') return false;
  const candidate = row as Partial<ExperienceProvenanceRow>;
  return (
    typeof candidate.source_message_id === 'number'
    && typeof candidate.conversation_id === 'string'
    && isStringOrNull(candidate.source_created_at)
    && typeof candidate.source_state === 'string'
    && isStringOrNull(candidate.excerpt)
  );
}

function isValidExperiencePayload(
  body: unknown,
  request: DetailsRequest,
): body is ExperienceDetailsPayload {
  if (!body || typeof body !== 'object') return false;
  if (!scopeMatches(body, request)) return false;
  const candidate = body as Partial<ExperienceDetailsPayload>;
  if (!candidate.experience || typeof candidate.experience !== 'object') return false;
  const experience = candidate.experience;
  if (
    experience.experience_id !== request.recordId
    || typeof experience.display_text !== 'string'
    || typeof experience.conversation_id !== 'string'
    || typeof experience.status !== 'string'
    || typeof experience.is_expired !== 'boolean'
    || isStringOrNull(experience.expires_at) === false
    || isStringOrNull(experience.created_at) === false
  ) {
    return false;
  }
  if (typeof candidate.observation_id !== 'string') return false;
  if (!Array.isArray(candidate.provenance) || !candidate.provenance.every(isValidExperienceProvenanceRow)) {
    return false;
  }
  return true;
}

export type DetailsFetchOptions = {
  fetcher?: typeof fetch;
  signal?: AbortSignal;
};

export async function fetchDetails(
  request: DetailsRequest,
  options: DetailsFetchOptions = {},
): Promise<DetailsLoadResult> {
  const fetcher = options.fetcher ?? fetch;
  const init: RequestInit = options.signal ? { signal: options.signal } : {};
  let response: Response;
  try {
    response = await fetcher(buildDetailsUrl(request), init);
  } catch {
    return { type: 'transport-error' };
  }
  if (!response.ok) {
    let code = 'memory_operation_failed';
    try {
      code = readErrorCode(await response.json()) ?? code;
    } catch {
      /* keep the stable default; never leak body text */
    }
    if (response.status === 404) return { type: 'not-found' };
    return { type: 'backend-failure', code, status: response.status };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { type: 'backend-failure', code: 'invalid_details_payload', status: response.status };
  }
  if (request.kind === 'fact') {
    if (!isValidFactPayload(body, request)) {
      return { type: 'backend-failure', code: 'invalid_details_payload', status: response.status };
    }
    return { type: 'ok', payload: body };
  }
  if (!isValidExperiencePayload(body, request)) {
    return { type: 'backend-failure', code: 'invalid_details_payload', status: response.status };
  }
  return { type: 'ok', payload: body };
}

function cacheKey(request: DetailsRequest): string {
  return [
    request.scope.sessionId,
    request.scope.worldline,
    request.scope.identityMode,
    request.kind,
    request.recordId,
  ].join('|');
}

// S4-ARCHIVE Gate 2: the retained-payload cache can be SHARED between the
// Constellation reading controller and the Archive governance module by
// injecting the same Map. A successful erasure then purges the SAME
// (scope, kind, record) entry that any peer surface still holds (hard
// invariant 6). Controllers without an injected cache stay fully isolated.
export type DetailsCache = Map<string, DetailsPayload>;

/** Drop one cached details payload (post-mutation refresh / post-erasure purge). */
export function invalidateDetailsCache(cache: DetailsCache, request: DetailsRequest): void {
  cache.delete(cacheKey(request));
}

function withRequest(result: DetailsLoadResult, request: DetailsRequest): DetailsOutcome {
  if (result.type === 'ok') return { type: 'ok', request, payload: result.payload };
  if (result.type === 'not-found') return { type: 'not-found', request };
  if (result.type === 'backend-failure') {
    return { type: 'backend-failure', request, code: result.code, status: result.status };
  }
  return { type: 'transport-error', request };
}

export type DetailsActivateOptions = {
  /** Explicit retry: bypass the retained payload and refetch. */
  force?: boolean;
};

export function createDetailsController(deps?: {
  load?: (request: DetailsRequest, signal?: AbortSignal) => Promise<DetailsLoadResult>;
  cache?: DetailsCache;
}) {
  // §15.2: epoch is owned internally; the active {scope, kind, recordId}
  // result is retained across views; selection/scope change clears it.
  // The signal reaches transport; stale results are still discarded by epoch
  // as the second line of defense.
  // P2-A (round 4): a successfully read payload survives non-record
  // selection changes (Topic / SOUL / rove) so reactivating the same record
  // never re-GETs. Activating a DIFFERENT record/scope replaces the slot;
  // explicit retry (force) bypasses it.
  const load = deps?.load ?? ((request: DetailsRequest, signal?: AbortSignal) => fetchDetails(request, { signal }));
  const cache: DetailsCache = deps?.cache ?? new Map();
  let epoch = 0;
  let inflight: AbortController | null = null;
  // One retained slot per controller (P2-A): the shared Map may hold more
  // entries, but THIS controller only reuses the key it last retained. That
  // keeps activation semantics identical to the single-slot model while still
  // letting a peer governance seam purge entries via invalidateDetailsCache.
  let retainedKey: string | null = null;

  function activate(
    request: DetailsRequest,
    notify: (outcome: DetailsOutcome) => void,
    options: DetailsActivateOptions = {},
  ): DetailsOutcome {
    epoch += 1;
    const myEpoch = epoch;
    inflight?.abort();
    const controller = new AbortController();
    inflight = controller;
    const key = cacheKey(request);
    if (options.force) {
      cache.delete(key);
      retainedKey = null;
    }
    const shared = retainedKey === key ? cache.get(key) : undefined;
    if (shared) {
      return { type: 'ok', request, payload: shared };
    }
    retainedKey = null;
    void (async () => {
      const result = await load(request, controller.signal);
      if (myEpoch !== epoch) return; // stale: never render, announce, or cache
      if (result.type === 'ok') {
        cache.set(key, result.payload);
        retainedKey = key;
      }
      notify(withRequest(result, request));
    })();
    return { type: 'loading', request };
  }

  function deactivate(): void {
    epoch += 1;
    inflight?.abort();
    inflight = null;
    // The cached payload is retained: only a different record/scope or an
    // explicit retry replaces it (see activate).
  }

  /** P1/P2 (round 4): a scope change invalidates any retained payload. */
  function invalidate(): void {
    epoch += 1;
    inflight?.abort();
    inflight = null;
    if (retainedKey) cache.delete(retainedKey);
    retainedKey = null;
  }

  return { activate, deactivate, invalidate };
}

// ---------------------------------------------------------------------------
// Reading model (pure derivation; never fetches)
// ---------------------------------------------------------------------------

export type TopicAttribution =
  | { kind: 'grouped'; label: string }
  | { kind: 'ungrouped' }
  | { kind: 'unavailable-in-projection'; topicId: string };

export type FactRef = { projectionId: string; topicId: string | null };

export function topicAttributionForFact(fact: FactRef, envelope: MemoryProjection): TopicAttribution {
  if (fact.topicId === null) return { kind: 'ungrouped' };
  const target = `topic:${fact.topicId}`;
  const hasEdge = envelope.edges.some(
    (edge) => edge.kind === 'has_topic' && edge.from === fact.projectionId && edge.to === target,
  );
  const topicNode = envelope.nodes.find(
    (node) => node.kind === 'topic' && node.topic_id === fact.topicId,
  );
  if (hasEdge && topicNode && topicNode.kind === 'topic') {
    return { kind: 'grouped', label: topicNode.label };
  }
  return { kind: 'unavailable-in-projection', topicId: fact.topicId };
}

// S4-ARCHIVE Gate 2: Archive reads the Topics seam directly (hard invariant 2
// — never the bounded projection). Attribution is label-owned by the backend
// topics list; an unknown topic id stays honestly unavailable.
export type ArchiveTopicLabel = { topicId: string; displayLabel: string };

export function topicAttributionFromTopics(
  topicId: string | null,
  topics: ArchiveTopicLabel[],
): TopicAttribution {
  if (topicId === null) return { kind: 'ungrouped' };
  const hit = topics.find((topic) => topic.topicId === topicId);
  if (hit) return { kind: 'grouped', label: hit.displayLabel };
  return { kind: 'unavailable-in-projection', topicId };
}

function factReadingCore(
  payload: FactDetailsPayload,
): Omit<FactReadingModel, 'topicAttribution'> {
  const activeVersion = payload.fact.active_version;
  const activeSources = payload.provenance
    .filter((row) => row.version_no === activeVersion)
    .map(sourceLine);
  const historyVersions: HistoryVersion[] = payload.versions.map((version) => ({
    versionNo: version.version_no,
    displayText: version.display_text,
    changeKind: version.change_kind,
    changeKindHuman: changeKindHuman(version.change_kind),
    validFromHuman: formatTimeHuman(version.valid_from),
    invalidAtHuman: formatTimeHuman(version.invalid_at),
    isActive: version.is_active,
    sources: payload.provenance
      .filter((row) => row.version_no === version.version_no)
      .map(sourceLine),
  }));
  const latestVersion =
    payload.versions.find((version) => version.is_active)
    ?? payload.versions[payload.versions.length - 1]
    ?? null;
  return {
    kind: 'fact',
    primaryText: payload.fact.display_text,
    isPinned: payload.fact.is_pinned,
    versionCount: payload.versions.length,
    revisionSummary:
      payload.versions.length > 1 && latestVersion
        ? `${payload.versions.length} 个版本 · 最新变更：${changeKindHuman(latestVersion.change_kind)}`
        : null,
    activeSources,
    historyVersions,
    correctionNotice: CORRECTION_NOTICE,
    deepLinkNotice: DEEP_LINK_NOTICE,
    diagnostics: {
      factId: payload.fact.fact_id,
      topicIdRaw: payload.fact.topic_id,
      observationIds: uniqueStrings(payload.provenance.map((row) => row.observation_id)),
      conversationIds: uniqueStrings(payload.provenance.map((row) => row.conversation_id)),
      sourceMessageIds: payload.provenance.map((row) => row.source_message_id),
      rawTimestamps: uniqueStrings([
        ...payload.versions.map((version) => version.valid_from),
        ...payload.versions.map((version) => version.invalid_at),
        ...payload.provenance.map((row) => row.source_created_at),
      ]),
    },
  };
}

/** Archive fact reading: same P1R-3 derivation, Topics-seam attribution. */
export function deriveFactReadingArchive(
  payload: FactDetailsPayload,
  topics: ArchiveTopicLabel[],
): FactReadingModel {
  return {
    ...factReadingCore(payload),
    topicAttribution: topicAttributionFromTopics(payload.fact.topic_id, topics),
  };
}

export type SourceLine = {
  sourceCreatedRaw: string | null;
  sourceCreatedHuman: string | null;
  sourceErased: boolean;
  excerpt: string | null;
};

export type HistoryVersion = {
  versionNo: number;
  displayText: string;
  changeKind: string;
  changeKindHuman: string;
  validFromHuman: string | null;
  invalidAtHuman: string | null;
  isActive: boolean;
  sources: SourceLine[];
};

export type FactReadingModel = {
  kind: 'fact';
  primaryText: string;
  isPinned: boolean;
  topicAttribution: TopicAttribution;
  versionCount: number;
  revisionSummary: string | null;
  activeSources: SourceLine[];
  historyVersions: HistoryVersion[];
  correctionNotice: string;
  deepLinkNotice: string;
  diagnostics: {
    factId: string;
    topicIdRaw: string | null;
    observationIds: string[];
    conversationIds: string[];
    sourceMessageIds: number[];
    rawTimestamps: string[];
  };
};

export type ExperienceReadingModel = {
  kind: 'experience';
  primaryText: string;
  createdHuman: string | null;
  expiry: { expired: boolean; expiresHuman: string | null } | null;
  sources: SourceLine[];
  correctionNotice: string;
  deepLinkNotice: string;
  diagnostics: {
    experienceId: string;
    observationId: string;
    conversationIds: string[];
    sourceMessageIds: number[];
    rawTimestamps: string[];
  };
};

const CHANGE_KIND_ZH: Record<string, string> = {
  create: '建立',
  refine: '细化',
  supersede: '修订替代',
  user_edit: '用户修订',
  legacy_import: '历史导入',
};

export function changeKindHuman(changeKind: string): string {
  return CHANGE_KIND_ZH[changeKind] ?? changeKind;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

export function formatTimeHuman(iso: string | null, now: number = Date.now()): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  const diff = now - parsed.getTime();
  if (diff < MINUTE_MS) return '刚刚';
  if (diff < HOUR_MS) return `${Math.floor(diff / MINUTE_MS)} 分钟前`;
  if (diff < DAY_MS) return `${Math.floor(diff / HOUR_MS)} 小时前`;
  if (diff < 30 * DAY_MS) return `${Math.floor(diff / DAY_MS)} 天前`;
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function sourceLine(row: {
  source_created_at: string | null;
  source_state: string;
  excerpt: string | null;
}): SourceLine {
  const erased = row.source_state === 'deleted';
  return {
    sourceCreatedRaw: row.source_created_at,
    sourceCreatedHuman: formatTimeHuman(row.source_created_at),
    // Backend already forces excerpt=null for deleted sources; the guard here
    // is defense-in-depth and never reconstructs content.
    sourceErased: erased,
    excerpt: erased ? null : row.excerpt,
  };
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  for (const value of values) {
    if (value) seen.add(value);
  }
  return [...seen];
}

export function deriveFactReading(
  payload: FactDetailsPayload,
  envelope: MemoryProjection,
  fact: FactRef,
): FactReadingModel {
  return {
    ...factReadingCore(payload),
    topicAttribution: topicAttributionForFact(fact, envelope),
  };
}

export function deriveExperienceReading(payload: ExperienceDetailsPayload): ExperienceReadingModel {
  return {
    kind: 'experience',
    primaryText: payload.experience.display_text,
    createdHuman: formatTimeHuman(payload.experience.created_at),
    expiry:
      payload.experience.expires_at === null
        ? null
        : {
            expired: payload.experience.is_expired,
            expiresHuman: formatTimeHuman(payload.experience.expires_at),
          },
    sources: payload.provenance.map(sourceLine),
    correctionNotice: CORRECTION_NOTICE,
    deepLinkNotice: DEEP_LINK_NOTICE,
    diagnostics: {
      experienceId: payload.experience.experience_id,
      observationId: payload.observation_id,
      conversationIds: uniqueStrings([
        payload.experience.conversation_id,
        ...payload.provenance.map((row) => row.conversation_id),
      ]),
      sourceMessageIds: payload.provenance.map((row) => row.source_message_id),
      rawTimestamps: uniqueStrings([
        payload.experience.created_at,
        payload.experience.expires_at,
        ...payload.provenance.map((row) => row.source_created_at),
      ]),
    },
  };
}
