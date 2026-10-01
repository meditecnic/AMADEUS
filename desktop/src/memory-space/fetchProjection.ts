import { buildProjectionUrl, normalizeCriteria, type CriteriaInput, type ScopeKey } from './criteria';
import type { MemoryProjection } from './types';

export type ProjectionQuery = ScopeKey & Partial<CriteriaInput> & {
  epoch: number;
  signal?: AbortSignal;
};

export type ProjectionTransportError = Error & {
  kind: 'unavailable' | 'generic';
  code: string;
  status: number;
};

export function isProjectionTransportError(error: unknown): error is ProjectionTransportError {
  return Boolean(error && typeof error === 'object' && 'kind' in error && 'code' in error);
}

export function projectionError(
  kind: 'unavailable' | 'generic',
  code: string,
  status: number,
): ProjectionTransportError {
  const error = new Error(code) as ProjectionTransportError;
  error.kind = kind;
  error.code = code;
  error.status = status;
  return error;
}

function readErrorCode(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const detail = (body as { detail?: { code?: unknown } }).detail;
  return typeof detail?.code === 'string' && detail.code.trim() ? detail.code : null;
}

export async function fetchMemoryProjection(
  input: ProjectionQuery,
  fetcher: typeof fetch = fetch,
): Promise<{ epoch: number; projection: MemoryProjection }> {
  const criteria = normalizeCriteria(input);
  const url = buildProjectionUrl(input, criteria);
  const init: RequestInit = input.signal ? { signal: input.signal } : {};
  const response = await fetcher(url, init);
  if (!response.ok) {
    let code = 'projection_failed';
    try {
      code = readErrorCode(await response.json()) ?? code;
    } catch {
      /* keep default; never leak body text */
    }
    const unavailable = response.status === 503 || code === 'projection_unavailable';
    throw projectionError(unavailable ? 'unavailable' : 'generic', code, response.status);
  }
  const projection = await response.json() as MemoryProjection;
  return { epoch: input.epoch, projection };
}

export function applyIfCurrent<T>(
  currentEpoch: number,
  incomingEpoch: number,
  value: T,
): T | undefined {
  return incomingEpoch === currentEpoch ? value : undefined;
}
