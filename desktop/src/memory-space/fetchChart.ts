import { apiFetch } from '../apiBase';
import type { IdentityMode, MemoryChart, Worldline } from './types';

export type ChartScope = { sessionId: string; worldline: Worldline; identityMode: IdentityMode };
export type ChartError = { kind: 'unavailable' | 'generic'; code: string };
export type ChartResult = { type: 'ok'; chart: MemoryChart } | { type: 'error'; error: ChartError };
export type ChartLoad = ChartResult | { type: 'stale' };

export function chartScopeKey(scope: ChartScope): string {
  return `${scope.sessionId}|${scope.worldline}|${scope.identityMode}`;
}

export function buildChartUrl(scope: ChartScope): string {
  const params = new URLSearchParams({
    session_id: scope.sessionId,
    worldline: scope.worldline,
    identity_mode: scope.identityMode,
  });
  return `/api/memory/chart?${params.toString()}`;
}

const isText = (value: unknown): value is string => typeof value === 'string';
const isTime = (value: unknown) => isText(value) && !Number.isNaN(Date.parse(value));
const isCount = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 0;

function validRecord(row: unknown, kind: 'fact' | 'experience'): boolean {
  if (!row || typeof row !== 'object') return false;
  const r = row as Record<string, unknown>;
  if (!isText(r.id) || !isText(r.label) || !isTime(r.created_at) || typeof r.is_pinned !== 'boolean') return false;
  if (kind === 'experience') return r.expires_at === null || isTime(r.expires_at);
  return (r.topic_id === null || isText(r.topic_id))
    && isCount(r.evidence_count) && isCount(r.version_count) && typeof r.resident === 'boolean';
}

// Fail closed: a payload for another scope or with malformed rows never reaches the plate.
export function parseChart(body: unknown, scope: ChartScope): MemoryChart | null {
  if (!body || typeof body !== 'object') return null;
  const chart = body as MemoryChart;
  const echo = chart.scope;
  if (!echo || echo.session_id !== scope.sessionId || echo.worldline !== scope.worldline
    || echo.identity_mode !== scope.identityMode) return null;
  if (!(chart.origin_at === null || isTime(chart.origin_at))) return null;
  if (!Array.isArray(chart.topics) || !Array.isArray(chart.facts) || !Array.isArray(chart.experiences)) return null;
  if (!chart.topics.every((t) => t && isText(t.id) && isText(t.label) && isTime(t.created_at))) return null;
  if (!chart.facts.every((row) => validRecord(row, 'fact'))) return null;
  if (!chart.experiences.every((row) => validRecord(row, 'experience'))) return null;
  const counts = chart.counts;
  if (!counts || !isCount(counts.facts) || !isCount(counts.experiences) || !isCount(counts.topics)) return null;
  return { ...chart, truncated: chart.truncated === true };
}

export async function fetchMemoryChart(
  scope: ChartScope,
  options: { signal?: AbortSignal; fetcher?: typeof fetch } = {},
): Promise<ChartResult> {
  let response: Response;
  try {
    response = await apiFetch(buildChartUrl(scope), options.signal ? { signal: options.signal } : {}, options.fetcher ?? fetch);
  } catch (error) {
    if (options.signal?.aborted) throw error;
    return { type: 'error', error: { kind: 'generic', code: 'chart_transport_error' } };
  }
  if (!response.ok) {
    let code = 'chart_failed';
    try {
      const detail = (await response.json() as { detail?: { code?: unknown } }).detail;
      if (isText(detail?.code) && detail.code.trim()) code = detail.code;
    } catch {
      /* keep the stable default; never surface body text */
    }
    return { type: 'error', error: { kind: response.status === 503 ? 'unavailable' : 'generic', code } };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { type: 'error', error: { kind: 'generic', code: 'invalid_chart_payload' } };
  }
  const chart = parseChart(body, scope);
  return chart ? { type: 'ok', chart } : { type: 'error', error: { kind: 'generic', code: 'invalid_chart_payload' } };
}

// Scope switches and refreshes supersede the request in flight: the older one is
// aborted and, if it still settles, reported as stale so it can never paint.
export function createChartController(load: typeof fetchMemoryChart = fetchMemoryChart) {
  let epoch = 0;
  let inflight: AbortController | null = null;
  return {
    async load(scope: ChartScope): Promise<ChartLoad> {
      const mine = ++epoch;
      inflight?.abort();
      const controller = new AbortController();
      inflight = controller;
      try {
        const result = await load(scope, { signal: controller.signal });
        return mine === epoch ? result : { type: 'stale' };
      } catch {
        return mine === epoch && !controller.signal.aborted
          ? { type: 'error', error: { kind: 'generic', code: 'chart_failed' } }
          : { type: 'stale' };
      }
    },
    dispose() {
      epoch += 1;
      inflight?.abort();
      inflight = null;
    },
  };
}
