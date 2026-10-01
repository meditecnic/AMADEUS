import {
  normalizeCriteria,
  sameScope,
  scopeKey,
  type CriteriaInput,
  type ScopeKey,
} from './criteria';
import {
  fetchMemoryProjection,
  isProjectionTransportError,
  projectionError,
  type ProjectionTransportError,
} from './fetchProjection';
import type { MemoryProjection } from './types';

export type Snapshot = {
  scope: ScopeKey;
  criteria: CriteriaInput;
  envelope: MemoryProjection;
};

export type LoadOutcome =
  | { type: 'stale'; epoch: number }
  | { type: 'ok'; epoch: number; envelope: MemoryProjection; scope: ScopeKey; criteria: CriteriaInput }
  | {
      type: 'error';
      epoch: number;
      error: ProjectionTransportError;
      previous?: Snapshot;
      restore?: Snapshot;
    };

export function createProjectionController(deps?: {
  fetchProjection?: typeof fetchMemoryProjection;
}) {
  const loadProjection = deps?.fetchProjection ?? fetchMemoryProjection;
  let epoch = 0;
  let inflight: AbortController | null = null;
  let lastSuccessful: Snapshot | null = null;
  const lastByScope = new Map<string, Snapshot>();

  return {
    get epoch() {
      return epoch;
    },
    lastSuccessful() {
      return lastSuccessful;
    },
    dispose() {
      inflight?.abort();
      inflight = null;
    },
    async load(
      scope: ScopeKey,
      criteriaInput: CriteriaInput,
      fetcher: typeof fetch = fetch,
    ): Promise<LoadOutcome> {
      const criteria = normalizeCriteria(criteriaInput);
      const requestEpoch = epoch + 1;
      epoch = requestEpoch;
      inflight?.abort();
      const controller = new AbortController();
      inflight = controller;
      const scopeChanged = Boolean(lastSuccessful && !sameScope(lastSuccessful.scope, scope));
      try {
        const result = await loadProjection(
          { ...scope, ...criteria, epoch: requestEpoch, signal: controller.signal },
          fetcher,
        );
        if (requestEpoch !== epoch) return { type: 'stale', epoch: requestEpoch };
        const snapshot: Snapshot = { scope, criteria, envelope: result.projection };
        lastSuccessful = snapshot;
        lastByScope.set(scopeKey(scope), snapshot);
        return { type: 'ok', epoch: requestEpoch, envelope: result.projection, scope, criteria };
      } catch (error) {
        if (controller.signal.aborted || requestEpoch !== epoch) {
          return { type: 'stale', epoch: requestEpoch };
        }
        const transport = isProjectionTransportError(error)
          ? error
          : projectionError('generic', 'projection_failed', 0);
        if (scopeChanged && lastSuccessful) {
          return { type: 'error', epoch: requestEpoch, error: transport, restore: lastSuccessful };
        }
        return {
          type: 'error',
          epoch: requestEpoch,
          error: transport,
          previous: lastByScope.get(scopeKey(scope)),
        };
      }
    },
  };
}

export type ProjectionController = ReturnType<typeof createProjectionController>;
