import { apiFetch } from '../apiBase';
import { parseMicros } from './chartTime';
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
const isTime = (value: unknown) => parseMicros(value) !== null;
const isTimeOrNull = (value: unknown) => value === null || isTime(value);
const isTextOrNull = (value: unknown) => value === null || isText(value);
const isCount = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 0;
const isBool = (value: unknown) => typeof value === 'boolean';
const WORLDLINES: readonly unknown[] = ['steins_gate', 'beta'];
const MODES: readonly unknown[] = ['self', 'okabe'];

type Shape = Record<string, (value: unknown) => boolean>;
const TOPIC: Shape = { id: isText, label: isText, created_at: isTime, fact_count: isCount };
const FACT: Shape = {
  id: isText, label: isText, topic_id: isTextOrNull, created_at: isTime, updated_at: isTime,
  is_pinned: isBool, evidence_count: isCount, version_count: isCount, resident: isBool,
};
const EXPERIENCE: Shape = {
  id: isText, label: isText, created_at: isTime, updated_at: isTime, is_pinned: isBool, expires_at: isTimeOrNull,
};
const COUNTS: Shape = { facts: isCount, experiences: isCount, topics: isCount };

// Every field must be present with its backend type; "in" keeps a missing nullable
// field from passing as null.
function fits(value: unknown, shape: Shape): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return Object.entries(shape).every(([key, check]) => key in row && check(row[key]));
}
const allFit = (value: unknown, shape: Shape) => Array.isArray(value) && value.every((row) => fits(row, shape));

// Fail closed: a payload for another scope or with any malformed field never reaches the plate.
export function parseChart(body: unknown, scope: ChartScope): MemoryChart | null {
  if (!body || typeof body !== 'object') return null;
  const chart = body as Record<string, unknown>;
  const echo = chart.scope as Record<string, unknown> | null | undefined;
  if (!echo || typeof echo !== 'object' || !isText(echo.session_id) || !WORLDLINES.includes(echo.worldline)
    || !MODES.includes(echo.identity_mode)) return null;
  if (echo.session_id !== scope.sessionId || echo.worldline !== scope.worldline
    || echo.identity_mode !== scope.identityMode) return null;
  if (!isTime(chart.generated_at) || !('origin_at' in chart) || !isTimeOrNull(chart.origin_at)) return null;
  if (!allFit(chart.topics, TOPIC) || !allFit(chart.facts, FACT) || !allFit(chart.experiences, EXPERIENCE)) return null;
  if (!fits(chart.counts, COUNTS) || !isBool(chart.truncated)) return null;
  return body as MemoryChart;
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
