import type { IdentityMode, MemoryProjection, Worldline } from './types';

export type ScopeKey = {
  sessionId: string;
  worldline: Worldline;
  identityMode: IdentityMode;
};

export type CriteriaInput = {
  query: string;
  kinds: string[];
  topicId: string | null;
  pinnedOnly: boolean;
  updatedFrom: string | null;
  updatedTo: string | null;
};

export const EMPTY_CRITERIA: CriteriaInput = {
  query: '',
  kinds: [],
  topicId: null,
  pinnedOnly: false,
  updatedFrom: null,
  updatedTo: null,
};

export const KIND_OPTIONS = ['topic', 'fact', 'experience'] as const;

export function scopeKey(scope: ScopeKey): string {
  return `${scope.sessionId}\0${scope.worldline}\0${scope.identityMode}`;
}

export function sameScope(a: ScopeKey, b: ScopeKey): boolean {
  return scopeKey(a) === scopeKey(b);
}

export function normalizeKinds(kinds: readonly string[]): string[] {
  const allowed = new Set<string>(KIND_OPTIONS);
  const unique: string[] = [];
  for (const kind of kinds) {
    if (!allowed.has(kind) || unique.includes(kind)) continue;
    unique.push(kind);
  }
  unique.sort();
  return unique;
}

export function normalizeCriteria(input: Partial<CriteriaInput> | null | undefined): CriteriaInput {
  const query = (input?.query ?? '').trim();
  return {
    query,
    kinds: normalizeKinds(input?.kinds ?? []),
    topicId: input?.topicId?.trim() ? input.topicId.trim() : null,
    pinnedOnly: Boolean(input?.pinnedOnly),
    updatedFrom: input?.updatedFrom?.trim() ? input.updatedFrom.trim() : null,
    updatedTo: input?.updatedTo?.trim() ? input.updatedTo.trim() : null,
  };
}

export function hasActiveCriteria(criteria: CriteriaInput | MemoryProjection['criteria']): boolean {
  if (criteria.kinds.length > 0) return true;
  const query = criteria.query;
  if (typeof query === 'string' && query.trim()) return true;
  const topic = 'topicId' in criteria ? criteria.topicId : criteria.topic_id;
  if (topic) return true;
  const pinned = 'pinnedOnly' in criteria ? criteria.pinnedOnly : criteria.pinned_only;
  if (pinned) return true;
  const from = 'updatedFrom' in criteria ? criteria.updatedFrom : criteria.updated_from;
  if (from) return true;
  const to = 'updatedTo' in criteria ? criteria.updatedTo : criteria.updated_to;
  return Boolean(to);
}

export function criteriaFromEnvelope(envelope: MemoryProjection): CriteriaInput {
  return normalizeCriteria({
    query: envelope.criteria.query ?? '',
    kinds: envelope.criteria.kinds,
    topicId: envelope.criteria.topic_id,
    pinnedOnly: envelope.criteria.pinned_only,
    updatedFrom: envelope.criteria.updated_from,
    updatedTo: envelope.criteria.updated_to,
  });
}

export type CriteriaChip = {
  id: string;
  key: keyof CriteriaInput | 'kind';
  value: string;
  label: string;
};

export function criteriaChips(criteria: CriteriaInput): CriteriaChip[] {
  const chips: CriteriaChip[] = [];
  if (criteria.query) chips.push({ id: 'query', key: 'query', value: criteria.query, label: `搜索 ${criteria.query}` });
  for (const kind of criteria.kinds) {
    const names: Record<string, string> = { topic: '主题', fact: '事实', experience: '经历' };
    chips.push({ id: `kind:${kind}`, key: 'kind', value: kind, label: names[kind] ?? kind });
  }
  if (criteria.topicId) chips.push({ id: 'topic', key: 'topicId', value: criteria.topicId, label: '指定主题' });
  if (criteria.pinnedOnly) chips.push({ id: 'pinned', key: 'pinnedOnly', value: 'true', label: '仅置顶' });
  if (criteria.updatedFrom) chips.push({ id: 'from', key: 'updatedFrom', value: criteria.updatedFrom, label: `自 ${criteria.updatedFrom}` });
  if (criteria.updatedTo) chips.push({ id: 'to', key: 'updatedTo', value: criteria.updatedTo, label: `至 ${criteria.updatedTo}` });
  return chips;
}

export function clearChip(criteria: CriteriaInput, chip: CriteriaChip): CriteriaInput {
  if (chip.key === 'kind') {
    return normalizeCriteria({ ...criteria, kinds: criteria.kinds.filter((kind) => kind !== chip.value) });
  }
  if (chip.key === 'query') return normalizeCriteria({ ...criteria, query: '' });
  if (chip.key === 'topicId') return normalizeCriteria({ ...criteria, topicId: null });
  if (chip.key === 'pinnedOnly') return normalizeCriteria({ ...criteria, pinnedOnly: false });
  if (chip.key === 'updatedFrom') return normalizeCriteria({ ...criteria, updatedFrom: null });
  if (chip.key === 'updatedTo') return normalizeCriteria({ ...criteria, updatedTo: null });
  return criteria;
}

export function buildProjectionSearchParams(scope: ScopeKey, criteria: CriteriaInput): URLSearchParams {
  const normalized = normalizeCriteria(criteria);
  const params = new URLSearchParams({
    session_id: scope.sessionId,
    worldline: scope.worldline,
    identity_mode: scope.identityMode,
    view: 'overview',
  });
  if (normalized.query) params.set('query', normalized.query);
  for (const kind of normalized.kinds) params.append('kind', kind);
  if (normalized.topicId) params.set('topic_id', normalized.topicId);
  if (normalized.pinnedOnly) params.set('pinned_only', 'true');
  if (normalized.updatedFrom) params.set('updated_from', normalized.updatedFrom);
  if (normalized.updatedTo) params.set('updated_to', normalized.updatedTo);
  return params;
}

export function buildProjectionUrl(scope: ScopeKey, criteria: CriteriaInput): string {
  return `/api/memory/graph/projection?${buildProjectionSearchParams(scope, criteria).toString()}`;
}

export function hasLongTermMemory(composition: MemoryProjection['composition']): boolean {
  return (
    composition.active_facts > 0
    || composition.active_experiences > 0
    || composition.eligible_topics > 0
  );
}
