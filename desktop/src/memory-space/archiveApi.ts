import type { IdentityMode, Worldline } from './types';

// S4-ARCHIVE Gate 2 transport adapters. The existing v11 REST seams are the
// real adapters: every request carries the complete exact scope, outcomes are
// mapped into honest product states, and confidence is deliberately dropped
// from every row model (it is not a user-facing truth scale).

export type ArchiveScope = {
  sessionId: string;
  worldline: Worldline;
  identityMode: IdentityMode;
};

export type FactBrowseRow = {
  factId: string;
  displayText: string;
  versionNo: number;
  activeVersion: number;
  isPinned: boolean;
  topicId: string | null;
  updatedAt: string;
};

export type ExperienceBrowseRow = {
  experienceId: string;
  displayText: string;
  status: string;
  isExpired: boolean;
  isPinned: boolean;
  createdAt: string | null;
};

export type ArchivePagination = { total: number; hasMore: boolean };

export type FactsPagePayload = { rows: FactBrowseRow[] } & ArchivePagination;
export type ExperiencesPagePayload = { rows: ExperienceBrowseRow[] } & ArchivePagination;

export type TopicRow = { topicId: string; displayLabel: string; activeFactCount: number };
export type ObservationRow = {
  observationId: string;
  displayText: string | null;
  status: string;
};
export type FailedJobRow = {
  jobId: string;
  attemptCount: number;
  lastErrorCode: string | null;
  updatedAt: string | null;
  retryable: boolean;
};
export type MemoryStatusPayload = {
  runtimeMode?: 'legacy' | 'shadow' | 'v11';
  pendingCount: number;
  processingCount: number;
  failedCount: number;
  failedJobs: FailedJobRow[];
};

export type ArchiveResult<T> =
  | { type: 'ok'; payload: T }
  | { type: 'not-found' }
  | { type: 'conflict'; code: string }
  | { type: 'validation'; code: string }
  | { type: 'backend-failure'; code: string; status: number }
  | { type: 'transport-error' };

export type FactPageFilters = {
  query?: string;
  topicId?: string | null;
  pinnedOnly?: boolean;
  limit: number;
  offset: number;
};

export type ExperiencePageFilters = {
  query?: string;
  pinnedOnly?: boolean;
  limit: number;
  offset: number;
};

export type ArchiveRequestOptions = {
  fetcher?: typeof fetch;
  signal?: AbortSignal;
};

function scopeParams(scope: ArchiveScope): URLSearchParams {
  return new URLSearchParams({
    session_id: scope.sessionId,
    worldline: scope.worldline,
    identity_mode: scope.identityMode,
  });
}

export function buildFactsUrl(scope: ArchiveScope, filters: FactPageFilters): string {
  const params = scopeParams(scope);
  if (filters.query) params.set('query', filters.query);
  if (filters.topicId) params.set('topic_id', filters.topicId);
  if (filters.pinnedOnly) params.set('pinned_only', 'true');
  params.set('limit', String(filters.limit));
  params.set('offset', String(filters.offset));
  return `/api/memory/facts?${params.toString()}`;
}

export function buildExperiencesUrl(
  scope: ArchiveScope,
  filters: ExperiencePageFilters,
): string {
  const params = scopeParams(scope);
  if (filters.query) params.set('query', filters.query);
  if (filters.pinnedOnly) params.set('pinned_only', 'true');
  params.set('limit', String(filters.limit));
  params.set('offset', String(filters.offset));
  return `/api/memory/experiences?${params.toString()}`;
}

function readErrorCode(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const detail = (body as { detail?: { code?: unknown } }).detail;
  return typeof detail?.code === 'string' && detail.code.trim() ? detail.code : null;
}

async function mapResponse<T>(
  response: Response,
  parse: (body: unknown) => T | null,
): Promise<ArchiveResult<T>> {
  if (!response.ok) {
    let code = 'memory_operation_failed';
    try {
      code = readErrorCode(await response.json()) ?? code;
    } catch {
      /* keep the stable default; never leak body text */
    }
    if (response.status === 404) return { type: 'not-found' };
    if (response.status === 409) return { type: 'conflict', code };
    if (response.status === 422) return { type: 'validation', code };
    return { type: 'backend-failure', code, status: response.status };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { type: 'backend-failure', code: 'invalid_payload', status: response.status };
  }
  const payload = parse(body);
  if (payload === null) {
    return { type: 'backend-failure', code: 'invalid_payload', status: response.status };
  }
  return { type: 'ok', payload };
}

async function execute<T>(
  url: string,
  init: RequestInit | undefined,
  options: ArchiveRequestOptions,
  parse: (body: unknown) => T | null,
): Promise<ArchiveResult<T>> {
  const fetcher = options.fetcher ?? fetch;
  const requestInit: RequestInit = { ...(init ?? {}) };
  if (options.signal) requestInit.signal = options.signal;
  let response: Response;
  try {
    response = await fetcher(url, requestInit);
  } catch {
    return { type: 'transport-error' };
  }
  return mapResponse(response, parse);
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

// Fail-closed scope echo validation (hard invariant 1): a browse payload is
// only acceptable when the backend affirms the exact requested
// (session_id, worldline, identity_mode). Missing, malformed, or mismatched
// scope rejects the WHOLE response — no row may ever render cross-scope.
function responseScopeMatches(body: Record<string, unknown>, expected: ArchiveScope): boolean {
  const scope = body.scope;
  if (!scope || typeof scope !== 'object') return false;
  const candidate = scope as Record<string, unknown>;
  return (
    candidate.session_id === expected.sessionId
    && candidate.worldline === expected.worldline
    && candidate.identity_mode === expected.identityMode
  );
}

// The v11 status payload echoes scope as TOP-LEVEL fields (not a scope
// object) — validate the real payload shape, never an invented one.
function responseTopLevelScopeMatches(
  body: Record<string, unknown>,
  expected: ArchiveScope,
): boolean {
  return (
    body.session_id === expected.sessionId
    && body.worldline === expected.worldline
    && body.identity_mode === expected.identityMode
  );
}

function parseFactsPage(body: unknown, expected: ArchiveScope): FactsPagePayload | null {
  if (!body || typeof body !== 'object') return null;
  const candidate = body as { facts?: unknown; pagination?: { total?: unknown; has_more?: unknown } };
  if (!responseScopeMatches(candidate, expected)) return null;
  if (!Array.isArray(candidate.facts) || !candidate.pagination) return null;
  const total = Number(candidate.pagination.total);
  if (!Number.isFinite(total)) return null;
  const rows: FactBrowseRow[] = [];
  for (const raw of candidate.facts) {
    if (!raw || typeof raw !== 'object') return null;
    const row = raw as Record<string, unknown>;
    if (!isString(row.fact_id) || !isString(row.display_text)) return null;
    // Contract-required field: a missing/invalid updated_at is never faked
    // as an empty time — the WHOLE response fails closed instead.
    if (!isString(row.updated_at) || !row.updated_at) return null;
    rows.push({
      factId: row.fact_id,
      displayText: row.display_text,
      versionNo: typeof row.version_no === 'number' ? row.version_no : 0,
      activeVersion: typeof row.active_version === 'number' ? row.active_version : 0,
      isPinned: row.is_pinned === true,
      topicId: isString(row.topic_id) ? row.topic_id : null,
      updatedAt: row.updated_at,
      // confidence is intentionally never mapped: it must not reach the UI.
    });
  }
  return { rows, total, hasMore: candidate.pagination.has_more === true };
}

function parseExperiencesPage(body: unknown, expected: ArchiveScope): ExperiencesPagePayload | null {
  if (!body || typeof body !== 'object') return null;
  const candidate = body as {
    experiences?: unknown;
    pagination?: { total?: unknown; has_more?: unknown };
  };
  if (!responseScopeMatches(candidate, expected)) return null;
  if (!Array.isArray(candidate.experiences) || !candidate.pagination) return null;
  const total = Number(candidate.pagination.total);
  if (!Number.isFinite(total)) return null;
  const rows: ExperienceBrowseRow[] = [];
  for (const raw of candidate.experiences) {
    if (!raw || typeof raw !== 'object') return null;
    const row = raw as Record<string, unknown>;
    if (!isString(row.experience_id) || !isString(row.display_text)) return null;
    // Contract-required field: a missing/non-boolean is_pinned is never
    // coerced into "not pinned" — the WHOLE response fails closed instead.
    if (typeof row.is_pinned !== 'boolean') return null;
    rows.push({
      experienceId: row.experience_id,
      displayText: row.display_text,
      status: isString(row.status) ? row.status : '',
      isExpired: row.is_expired === true,
      isPinned: row.is_pinned,
      createdAt: isString(row.created_at) ? row.created_at : null,
    });
  }
  return { rows, total, hasMore: candidate.pagination.has_more === true };
}

function parseTopics(body: unknown, expected: ArchiveScope): TopicRow[] | null {
  if (!body || typeof body !== 'object') return null;
  const candidate = body as { topics?: unknown };
  if (!responseScopeMatches(candidate, expected)) return null;
  if (!Array.isArray(candidate.topics)) return null;
  const rows: TopicRow[] = [];
  for (const raw of candidate.topics) {
    if (!raw || typeof raw !== 'object') return null;
    const row = raw as Record<string, unknown>;
    if (!isString(row.topic_id) || !isString(row.display_label)) return null;
    // The backend field is fact_count. A missing/mistyped count is never
    // defaulted to 0 — the WHOLE topics response fails closed instead.
    if (
      typeof row.fact_count !== 'number'
      || !Number.isFinite(row.fact_count)
      || row.fact_count < 0
    ) {
      return null;
    }
    rows.push({
      topicId: row.topic_id,
      displayLabel: row.display_label,
      activeFactCount: row.fact_count,
    });
  }
  return rows;
}

// Strict status validator (§16 C3), shared by the Archive module AND the
// MemorySpace direct /api/memory/status read — one set of rules, never two.
// failed_jobs must be a REAL array; every count field must be present and a
// number; every failed-job row must carry EXACTLY the contract fields with
// correct types. Anything else rejects the WHOLE response.
function parseStatusJobRow(raw: unknown): FailedJobRow | null {
  if (!raw || typeof raw !== 'object') return null;
  const row = raw as Record<string, unknown>;
  if (Object.keys(row).length !== 5) return null;
  if (!isString(row.job_id) || !row.job_id) return null;
  if (row.last_error_code !== null && !isString(row.last_error_code)) return null;
  if (typeof row.attempt_count !== 'number' || !Number.isFinite(row.attempt_count)) return null;
  if (!isString(row.updated_at) || !row.updated_at) return null;
  if (typeof row.retryable !== 'boolean') return null;
  return {
    jobId: row.job_id,
    attemptCount: row.attempt_count,
    lastErrorCode: row.last_error_code,
    updatedAt: row.updated_at,
    retryable: row.retryable,
  };
}

export function parseMemoryStatusBody(
  body: unknown,
  expected: ArchiveScope,
): MemoryStatusPayload | null {
  if (!body || typeof body !== 'object') return null;
  const candidate = body as Record<string, unknown>;
  if (!responseTopLevelScopeMatches(candidate, expected)) return null;
  if (!Array.isArray(candidate.failed_jobs)) return null;
  const counts: Array<['pending' | 'processing' | 'failed', unknown]> = [
    ['pending', candidate.pending_count],
    ['processing', candidate.processing_count],
    ['failed', candidate.failed_count],
  ];
  for (const [, value] of counts) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  }
  const failedJobs: FailedJobRow[] = [];
  for (const raw of candidate.failed_jobs) {
    const row = parseStatusJobRow(raw);
    if (row === null) return null;
    failedJobs.push(row);
  }
  return {
    pendingCount: candidate.pending_count as number,
    runtimeMode: candidate.runtime_mode === 'legacy' || candidate.runtime_mode === 'shadow' || candidate.runtime_mode === 'v11' ? candidate.runtime_mode : undefined,
    processingCount: candidate.processing_count as number,
    failedCount: candidate.failed_count as number,
    failedJobs,
  };
}

function parseStatus(body: unknown, expected: ArchiveScope): MemoryStatusPayload | null {
  return parseMemoryStatusBody(body, expected);
}

function parseObservations(body: unknown, expected: ArchiveScope): ObservationRow[] | null {
  if (!body || typeof body !== 'object') return null;
  const candidate = body as { observations?: unknown };
  if (!responseScopeMatches(candidate, expected)) return null;
  if (!Array.isArray(candidate.observations)) return null;
  const rows: ObservationRow[] = [];
  for (const raw of candidate.observations) {
    if (!raw || typeof raw !== 'object') return null;
    const row = raw as Record<string, unknown>;
    if (!isString(row.observation_id)) return null;
    rows.push({
      observationId: row.observation_id,
      displayText: isString(row.display_text) ? row.display_text : null,
      status: isString(row.status) ? row.status : '',
    });
  }
  return rows;
}

function parseEcho(body: unknown): Record<string, unknown> | null {
  return body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
}

export function fetchFactsPage(
  scope: ArchiveScope,
  filters: FactPageFilters,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<FactsPagePayload>> {
  return execute(
    buildFactsUrl(scope, filters),
    undefined,
    options,
    (body) => parseFactsPage(body, scope),
  );
}

export function fetchExperiencesPage(
  scope: ArchiveScope,
  filters: ExperiencePageFilters,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<ExperiencesPagePayload>> {
  return execute(
    buildExperiencesUrl(scope, filters),
    undefined,
    options,
    (body) => parseExperiencesPage(body, scope),
  );
}

export function fetchTopics(
  scope: ArchiveScope,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<TopicRow[]>> {
  const params = scopeParams(scope);
  return execute(
    `/api/memory/topics?${params.toString()}`,
    undefined,
    options,
    (body) => parseTopics(body, scope),
  );
}

export function fetchMemoryStatus(
  scope: ArchiveScope,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<MemoryStatusPayload>> {
  const params = scopeParams(scope);
  return execute(
    `/api/memory/status?${params.toString()}`,
    undefined,
    options,
    (body) => parseStatus(body, scope),
  );
}

export function fetchObservations(
  scope: ArchiveScope,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<ObservationRow[]>> {
  const params = scopeParams(scope);
  params.set('limit', '20');
  params.set('offset', '0');
  return execute(
    `/api/memory/observations?${params.toString()}`,
    undefined,
    options,
    (body) => parseObservations(body, scope),
  );
}

function jsonPatch(
  url: string,
  body: Record<string, unknown>,
  options: ArchiveRequestOptions,
): Promise<ArchiveResult<Record<string, unknown>>> {
  return execute(
    url,
    { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
    options,
    parseEcho,
  );
}

export function correctFactText(
  scope: ArchiveScope,
  factId: string,
  edit: { displayText: string; expectedVersion: number },
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  return jsonPatch(
    `/api/memory/facts/${encodeURIComponent(factId)}?${params.toString()}`,
    { display_text: edit.displayText, expected_version: edit.expectedVersion },
    options,
  );
}

export function updateFactPresentation(
  scope: ArchiveScope,
  factId: string,
  change: { isPinned?: boolean; topicId?: string | null },
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  const body: Record<string, unknown> = {};
  if (change.isPinned !== undefined) body.is_pinned = change.isPinned;
  if (change.topicId !== undefined) body.topic_id = change.topicId;
  return jsonPatch(
    `/api/memory/facts/${encodeURIComponent(factId)}/presentation?${params.toString()}`,
    body,
    options,
  );
}

export function deleteFact(
  scope: ArchiveScope,
  factId: string,
  expectedVersion: number,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  params.set('expected_version', String(expectedVersion));
  return execute(
    `/api/memory/facts/${encodeURIComponent(factId)}?${params.toString()}`,
    { method: 'DELETE' },
    options,
    parseEcho,
  );
}

export function updateExperiencePresentation(
  scope: ArchiveScope,
  experienceId: string,
  isPinned: boolean,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  return jsonPatch(
    `/api/memory/experiences/${encodeURIComponent(experienceId)}/presentation?${params.toString()}`,
    { is_pinned: isPinned },
    options,
  );
}

export function deleteExperience(
  scope: ArchiveScope,
  experienceId: string,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  return execute(
    `/api/memory/experiences/${encodeURIComponent(experienceId)}?${params.toString()}`,
    { method: 'DELETE' },
    options,
    parseEcho,
  );
}

export function renameTopic(
  scope: ArchiveScope,
  topicId: string,
  displayLabel: string,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  return jsonPatch(
    `/api/memory/topics/${encodeURIComponent(topicId)}?${params.toString()}`,
    { display_label: displayLabel },
    options,
  );
}

export function ignoreObservation(
  scope: ArchiveScope,
  observationId: string,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  return execute(
    `/api/memory/observations/${encodeURIComponent(observationId)}/ignore?${params.toString()}`,
    { method: 'POST' },
    options,
    parseEcho,
  );
}

export function retryFailedJob(
  scope: ArchiveScope,
  jobId: string,
  options: ArchiveRequestOptions = {},
): Promise<ArchiveResult<Record<string, unknown>>> {
  const params = scopeParams(scope);
  return execute(
    `/api/memory/jobs/${encodeURIComponent(jobId)}/retry?${params.toString()}`,
    { method: 'POST' },
    options,
    parseEcho,
  );
}
