import type { FactVersionRow } from '../memory-space/readingDetails';
import type { MemoryProjection, ProjectionEdge, ProjectionNode } from '../memory-space/types';

export type MemorySeed = {
  facts: Array<{ fact_id: string; display_text: string; topic_id: string | null; is_pinned: boolean }>;
  topics: Array<{ topic_id: string; display_label: string }>;
};

const FIXED_TIME = '2026-10-17T12:00:00.000Z';
type Fact = MemorySeed['facts'][number] & {
  version_no: number;
  active_version: number;
  updated_at: string;
  versions: FactVersionRow[];
};
type ScopeData = { facts: Fact[]; topics: MemorySeed['topics'] };

function version(versionNo: number, text: string): FactVersionRow {
  return {
    version_no: versionNo,
    display_text: text,
    change_kind: versionNo === 1 ? 'create' : 'user_edit',
    previous_version: versionNo === 1 ? null : versionNo - 1,
    valid_from: FIXED_TIME,
    invalid_at: null,
    is_active: true,
  };
}

export function createMemoryStore(seed: MemorySeed) {
  // Snapshot now; new scopes must not inherit later caller or scope mutations.
  const snapshot: MemorySeed = {
    facts: seed.facts.map(fact => ({ ...fact })),
    topics: seed.topics.map(topic => ({ ...topic })),
  };
  const scopes = new Map<string, ScopeData>();
  return {
    scope(key: string): ScopeData {
      let data = scopes.get(key);
      if (!data) {
        data = {
          facts: snapshot.facts.map(fact => ({
            ...fact, version_no: 1, active_version: 1, updated_at: FIXED_TIME,
            versions: [version(1, fact.display_text)],
          })),
          topics: snapshot.topics.map(topic => ({ ...topic })),
        };
        scopes.set(key, data);
      }
      return data;
    },
  };
}

type Result = { status: number; body: unknown };
function success(body: unknown): Result { return { status: 200, body }; }
function failure(status: number, code: string): Result {
  return { status, body: { detail: { code } } };
}

function nonnegative(value: string | null, fallback: number): number {
  if (value === null || !value.trim()) return fallback;
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : fallback;
}

function pagination(params: URLSearchParams, total: number) {
  const limit = Math.min(nonnegative(params.get('limit'), 50), 200);
  const offset = nonnegative(params.get('offset'), 0);
  return { total, limit, offset, has_more: offset + limit < total };
}

function factRow({ versions: _versions, ...row }: Fact) { return { ...row }; }

function criteriaFor(params: URLSearchParams): MemoryProjection['criteria'] {
  const text = (key: string) => params.get(key)?.trim() || null;
  return {
    query: text('query'),
    kinds: [...new Set(params.getAll('kind').filter(kind => ['fact', 'topic', 'experience'].includes(kind)))].sort(),
    topic_id: text('topic_id'),
    pinned_only: params.get('pinned_only') === 'true',
    updated_from: text('updated_from'),
    updated_to: text('updated_to'),
  };
}

function matchingFacts(data: ScopeData, criteria: MemoryProjection['criteria']): Fact[] {
  const query = criteria.query?.toLocaleLowerCase();
  const from = criteria.updated_from ? Date.parse(criteria.updated_from) : null;
  const to = criteria.updated_to ? Date.parse(criteria.updated_to) : null;
  return data.facts.filter(fact =>
    (!query || fact.display_text.toLocaleLowerCase().includes(query))
    && (!criteria.topic_id || fact.topic_id === criteria.topic_id)
    && (!criteria.pinned_only || fact.is_pinned)
    && (from === null || Date.parse(fact.updated_at) >= from)
    && (to === null || Date.parse(fact.updated_at) <= to));
}

function projection(
  params: URLSearchParams,
  scope: MemoryProjection['scope'],
  data: ScopeData,
): MemoryProjection {
  const criteria = criteriaFor(params);
  const matches = matchingFacts(data, criteria);
  const includes = (kind: string) => !criteria.kinds.length || criteria.kinds.includes(kind);
  const topics = data.topics.filter(topic => matches.some(fact => fact.topic_id === topic.topic_id));
  const center: MemoryProjection['center'] = {
    kind: 'continuity_hub', projection_id: 'hub:continuity',
    label_primary: 'Memory', label_secondary: `${scope.worldline} · ${scope.identity_mode}`,
  };
  const resultNodes: ProjectionNode[] = [
    ...(includes('topic') ? topics.map(topic => ({
      kind: 'topic' as const, projection_id: `topic:${topic.topic_id}`,
      topic_id: topic.topic_id, label: topic.display_label,
      fact_count: matches.filter(fact => fact.topic_id === topic.topic_id).length,
      experience_count: 0,
    })) : []),
    ...(includes('fact') ? matches.map(fact => ({
      kind: 'fact' as const, projection_id: `fact:${fact.fact_id}`,
      fact_id: fact.fact_id, label: fact.display_text, is_pinned: fact.is_pinned,
      updated_at: fact.updated_at, topic_id: fact.topic_id,
    })) : []),
  ];
  const allNodes = [center, ...resultNodes];
  const topicIds = new Set(resultNodes.filter(node => node.kind === 'topic').map(node => node.projection_id));
  const allEdges: ProjectionEdge[] = resultNodes.map(node => {
    if (node.kind === 'fact' && node.topic_id && topicIds.has(`topic:${node.topic_id}`)) {
      return { kind: 'has_topic', from: node.projection_id, to: `topic:${node.topic_id}` };
    }
    return {
      kind: node.kind === 'topic' ? 'hub_to_anchor' : 'hub_to_evidence',
      from: center.projection_id, to: node.projection_id,
    };
  });
  const nodes = allNodes.slice(0, 200);
  const ids = new Set(nodes.map(node => node.projection_id));
  const edges = allEdges.filter(edge => ids.has(edge.from) && ids.has(edge.to)).slice(0, 400);
  const records = (list: ProjectionNode[]) => list.filter(node => node.kind === 'fact' || node.kind === 'experience').length;
  const shownResults = nodes.filter(node => node.kind !== 'continuity_hub');
  return {
    scope, view: 'overview', projection_version: 'dev-mock-v1', generated_at: FIXED_TIME,
    criteria, center,
    composition: {
      active_facts: data.facts.length, active_experiences: 0,
      eligible_topics: data.topics.filter(topic => data.facts.some(fact => fact.topic_id === topic.topic_id)).length,
      latest_memory_change_at: data.facts.length ? FIXED_TIME : null,
      person_anchors_supported: false,
    },
    budgets: { nodes: 200, edges: 400, hard_max_nodes: 200, hard_max_edges: 400 },
    eligible: { nodes: allNodes.length, edges: allEdges.length, records: records(allNodes), results: resultNodes.length },
    shown: { nodes: nodes.length, edges: edges.length, records: records(nodes), results: shownResults.length },
    truncated: {
      nodes: nodes.length < allNodes.length, edges: edges.length < allEdges.length,
      records: records(nodes) < records(allNodes), results: shownResults.length < resultNodes.length,
    },
    empty: resultNodes.length === 0,
    result_ids: shownResults.map(node => node.projection_id), nodes, edges,
  };
}

export function handleMemory(
  url: URL,
  method: string,
  body: Record<string, unknown>,
  store: ReturnType<typeof createMemoryStore>,
): Result | undefined {
  if (url.pathname !== '/api/memory' && !url.pathname.startsWith('/api/memory/')) return undefined;
  const path = url.pathname.slice('/api/memory'.length).replace(/\/$/, '');
  const params = url.searchParams;
  const scope = {
    session_id: params.get('session_id') ?? 'default',
    worldline: params.get('worldline') ?? 'steins_gate',
    identity_mode: params.get('identity_mode') ?? 'self',
  };
  const data = store.scope(JSON.stringify([scope.session_id, scope.worldline, scope.identity_mode]));
  method = method.toUpperCase();
  if (method === 'GET') {
    if (path === '/status') return success({
      ...scope, runtime_mode: 'v11', pending_count: 0, processing_count: 0, failed_count: 0, failed_jobs: [],
    });
    if (path === '/facts') {
      if (!params.has('identity_mode')) {
        const ledger = (identity: string) => store.scope(JSON.stringify([scope.session_id, scope.worldline, identity])).facts
          .map((fact, index) => ({ id: index + 1, fact_key: fact.fact_id, fact_value: fact.display_text }));
        return success({ local: ledger('self'), shared: [], okabe: ledger('okabe') });
      }
      const facts = matchingFacts(data, criteriaFor(params));
      const page = pagination(params, facts.length);
      return success({ scope, facts: facts.slice(page.offset, page.offset + page.limit).map(factRow), pagination: page });
    }
    if (path === '/experiences' || path === '/observations') {
      return success({ scope, [path.slice(1)]: [], pagination: pagination(params, 0) });
    }
    if (path === '/topics') return success({
      scope, topics: data.topics.map(topic => ({
        ...topic, fact_count: data.facts.filter(fact => fact.topic_id === topic.topic_id).length,
      })),
    });
    if (path === '/graph/projection') {
      // Scope strings are echoed verbatim. Only supported frontend scope values
      // are used by the mock UI; the cast must not normalize incoming strings.
      return success(projection(params, scope as MemoryProjection['scope'], data));
    }
  }

  const factMatch = path.match(/^\/facts\/([^/]+)(?:\/(details|presentation))?$/);
  if (factMatch) {
    let id: string;
    try { id = decodeURIComponent(factMatch[1]); } catch { return failure(422, 'invalid_fact_id'); }
    const fact = data.facts.find(item => item.fact_id === id);
    if (!fact) return failure(404, 'fact_not_found');
    const action = factMatch[2];
    if (method === 'GET' && (action === 'details' || !action)) return success({
      scope, fact: factRow(fact), versions: fact.versions.map(row => ({ ...row })), provenance: [],
    });
    if (method === 'PATCH' && action === 'presentation') {
      if ('is_pinned' in body && typeof body.is_pinned !== 'boolean') return failure(422, 'invalid_is_pinned');
      if ('topic_id' in body && body.topic_id !== null
        && (typeof body.topic_id !== 'string' || !data.topics.some(topic => topic.topic_id === body.topic_id))) {
        return failure(422, 'invalid_topic_id');
      }
      if (typeof body.is_pinned === 'boolean') fact.is_pinned = body.is_pinned;
      if ('topic_id' in body) fact.topic_id = body.topic_id as string | null;
      return success({ ok: true, scope, fact: factRow(fact) });
    }
    if ((method === 'PATCH' || method === 'DELETE') && !action) {
      const expected = method === 'DELETE' ? Number(params.get('expected_version') ?? body.expected_version) : body.expected_version;
      if (!Number.isSafeInteger(expected) || Number(expected) < 1) return failure(422, 'invalid_expected_version');
      if (expected !== fact.active_version) return failure(409, 'version_conflict');
      if (method === 'DELETE') {
        data.facts = data.facts.filter(item => item !== fact);
        return success({ ok: true, scope, fact_id: id });
      }
      if (typeof body.display_text !== 'string' || !body.display_text.trim()) return failure(422, 'invalid_display_text');
      fact.versions.forEach(row => { row.is_active = false; row.invalid_at = FIXED_TIME; });
      fact.display_text = body.display_text.trim();
      fact.active_version += 1;
      fact.version_no = fact.active_version;
      fact.versions.push(version(fact.active_version, fact.display_text));
      return success({ ok: true, scope, fact: factRow(fact) });
    }
  }
  const topicMatch = path.match(/^\/topics\/([^/]+)$/);
  if (method === 'PATCH' && topicMatch) {
    let id: string;
    try { id = decodeURIComponent(topicMatch[1]); } catch { return failure(422, 'invalid_topic_id'); }
    const topic = data.topics.find(item => item.topic_id === id);
    if (!topic) return failure(404, 'topic_not_found');
    if (typeof body.display_label !== 'string' || !body.display_label.trim()) return failure(422, 'invalid_display_label');
    topic.display_label = body.display_label.trim();
    return success({ ok: true, scope, topic: { ...topic } });
  }
  if (method === 'POST' && (/^\/observations\/[^/]+\/ignore$/.test(path) || /^\/jobs\/[^/]+\/retry$/.test(path))) {
    return success({ ok: true, scope });
  }
  return failure(404, 'memory_endpoint_not_found');
}
