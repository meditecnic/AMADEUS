import { hasActiveCriteria, hasLongTermMemory, type CriteriaInput } from './criteria';
import type { MemoryProjection } from './types';

export type OverviewError = {
  kind: 'unavailable' | 'generic';
  code: string;
};

export type OverviewPhase =
  | 'loading'
  | 'populated'
  | 'true-empty'
  | 'criteria-empty'
  | 'unavailable'
  | 'error'
  | 'scope-switching';

export type OverviewPresentation = {
  phase: OverviewPhase;
  envelope: MemoryProjection | null;
  previousTruth: MemoryProjection | null;
  stale: boolean;
  error: OverviewError | null;
  copy: string | null;
  showRetry: boolean;
};

export function classifyEmpty(envelope: MemoryProjection): 'true-empty' | 'criteria-empty' | 'populated' {
  if (!envelope.empty) return 'populated';
  if (hasActiveCriteria(envelope.criteria)) return 'criteria-empty';
  return 'true-empty';
}

export function classifyOverviewState(input: {
  loading: boolean;
  scopeSwitching?: boolean;
  envelope: MemoryProjection | null;
  previousTruth?: MemoryProjection | null;
  error: OverviewError | null;
  requestedCriteria?: CriteriaInput;
}): OverviewPresentation {
  const previousTruth = input.previousTruth ?? null;
  if (input.scopeSwitching && input.loading) {
    return {
      phase: 'scope-switching',
      envelope: previousTruth,
      previousTruth,
      stale: Boolean(previousTruth),
      error: null,
      copy: previousTruth ? '正在切换范围…' : '正在读取记忆投影…',
      showRetry: false,
    };
  }
  if (input.loading && !input.envelope) {
    return {
      phase: 'loading',
      envelope: null,
      previousTruth,
      stale: Boolean(previousTruth),
      error: null,
      copy: previousTruth ? '同步中' : '正在读取记忆投影…',
      showRetry: false,
    };
  }
  if (input.error) {
    const retained = input.envelope ?? previousTruth;
    const unavailable = input.error.kind === 'unavailable';
    return {
      phase: unavailable ? 'unavailable' : 'error',
      envelope: retained,
      previousTruth: retained,
      stale: Boolean(retained),
      error: input.error,
      copy: unavailable ? '记忆存储暂时不可用。' : '无法读取记忆投影。',
      showRetry: true,
    };
  }
  if (!input.envelope) {
    return {
      phase: 'loading',
      envelope: null,
      previousTruth,
      stale: Boolean(previousTruth),
      error: null,
      copy: '正在读取记忆投影…',
      showRetry: false,
    };
  }
  const emptyKind = classifyEmpty(input.envelope);
  if (emptyKind === 'true-empty') {
    return {
      phase: 'true-empty',
      envelope: input.envelope,
      previousTruth: input.envelope,
      stale: Boolean(input.loading),
      error: null,
      copy: input.loading
        ? '同步中'
        : hasLongTermMemory(input.envelope.composition)
          ? '当前没有可呈现的记忆。'
          : '这个范围里还没有可呈现的长期记忆。',
      showRetry: false,
    };
  }
  if (emptyKind === 'criteria-empty') {
    return {
      phase: 'criteria-empty',
      envelope: input.envelope,
      previousTruth: input.envelope,
      stale: Boolean(input.loading),
      error: null,
      copy: input.loading ? '同步中' : '当前搜索或筛选没有匹配的记忆。',
      showRetry: false,
    };
  }
  return {
    phase: 'populated',
    envelope: input.envelope,
    previousTruth: input.envelope,
    stale: Boolean(input.loading),
    error: null,
    copy: input.loading ? '同步中' : null,
    showRetry: false,
  };
}
