import type { IdentityMode, MemoryProjection, Worldline } from './types';

export type ProjectionQuery = {
  sessionId: string;
  worldline: Worldline;
  identityMode: IdentityMode;
  query?: string;
  epoch: number;
};

export async function fetchMemoryProjection(
  input: ProjectionQuery,
  fetcher: typeof fetch = fetch,
): Promise<{ epoch: number; projection: MemoryProjection }> {
  const params = new URLSearchParams({
    session_id: input.sessionId,
    worldline: input.worldline,
    identity_mode: input.identityMode,
    view: 'overview',
  });
  const q = (input.query || '').trim();
  if (q) params.set('query', q);
  const response = await fetcher(`/api/memory/graph/projection?${params.toString()}`);
  if (!response.ok) {
    let code = 'projection_failed';
    try {
      const body = await response.json() as { detail?: { code?: string } };
      if (body.detail?.code) code = body.detail.code;
    } catch {
      /* keep default */
    }
    throw new Error(code);
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
