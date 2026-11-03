import { chartPreset, type ChartPreset, type Said } from './chartFixtures';
import type { FactProvenanceRow, FactVersionRow } from '../memory-space/readingDetails';
import type { MemoryChart } from '../memory-space/types';

export type MemorySeed = {
  facts: Array<{ fact_id: string; display_text: string; topic_id: string | null; is_pinned: boolean }>;
  topics: Array<{ topic_id: string; display_label: string }>;
  /** Replaces facts/topics with a dated sample library (one per worldline). */
  preset?: string;
};

const FIXED_TIME = '2026-10-17T12:00:00.000Z';
const RESIDENT_LIMIT = 32;
const CHART_LIMIT = 2000;
type Fact = MemorySeed['facts'][number] & {
  version_no: number;
  active_version: number;
  created_at: string;
  updated_at: string;
  versions: FactVersionRow[];
  provenance: FactProvenanceRow[];
};
type Topic = MemorySeed['topics'][number] & { created_at: string };
type Experience = {
  experience_id: string; display_text: string; created_at: string; updated_at: string;
  expires_at: string | null; is_pinned: boolean; said: Said[];
};
type ScopeData = { facts: Fact[]; topics: Topic[]; experiences: Experience[]; origin_at: string | null };

const iso = (at: string) => new Date(`${at.replace(' ', 'T')}${at.length <= 10 ? 'T12:00' : ''}:00+08:00`).toISOString();

function version(versionNo: number, text: string, at = FIXED_TIME, kind?: string): FactVersionRow {
  return {
    version_no: versionNo,
    display_text: text,
    change_kind: kind ?? (versionNo === 1 ? 'create' : 'user_edit'),
    previous_version: versionNo === 1 ? null : versionNo - 1,
    valid_from: at,
    invalid_at: null,
    is_active: true,
  };
}

function presetData(preset: ChartPreset): ScopeData {
  const facts: Fact[] = preset.facts.map((item) => {
    const history = item.ver ?? [[item.text, item.said[0][1]] as Said];
    const versions = history.map(([text, at], i) => ({
      ...version(i + 1, text, iso(at), i === 0 ? 'create' : 'supersede'),
      invalid_at: i + 1 < history.length ? iso(history[i + 1][1]) : null,
      is_active: i + 1 === history.length,
    }));
    const provenance = item.said.map(([excerpt, at], i): FactProvenanceRow => ({
      version_no: Math.max(1, history.filter(([, from]) => iso(from) <= iso(at)).length),
      observation_id: `${item.id}-o${i + 1}`, source_message_id: i + 1, conversation_id: 'mock-conversation-1',
      source_created_at: iso(at), source_state: 'present', excerpt,
    }));
    return {
      fact_id: item.id, display_text: history[history.length - 1][0], topic_id: item.topic, is_pinned: Boolean(item.pin),
      version_no: versions.length, active_version: versions.length, created_at: iso(item.said[0][1]),
      updated_at: iso(history[history.length - 1][1]), versions, provenance,
    };
  });
  const firstIn = (id: string) => facts.filter((f) => f.topic_id === id).map((f) => f.created_at).sort()[0];
  return {
    facts,
    topics: preset.topics.map((t) => ({
      topic_id: t.id, display_label: t.label, created_at: firstIn(t.id) ?? iso(preset.origin ?? '2026-01-01'),
    })),
    experiences: preset.experiences.map((e) => ({
      experience_id: e.id, display_text: e.text, created_at: iso(e.said[0][1]), updated_at: iso(e.said[0][1]),
      expires_at: e.expires ? iso(e.expires) : null, is_pinned: Boolean(e.pin), said: e.said,
    })),
    origin_at: preset.origin ? iso(preset.origin) : null,
  };
}

export function createMemoryStore(seed: MemorySeed) {
  // Snapshot now; new scopes must not inherit later caller or scope mutations.
  const snapshot: MemorySeed = {
    facts: seed.facts.map(fact => ({ ...fact })),
    topics: seed.topics.map(topic => ({ ...topic })),
    preset: seed.preset,
  };
  const scopes = new Map<string, ScopeData>();
  return {
    scope(key: string, worldline = 'steins_gate'): ScopeData {
      let data = scopes.get(key);
      if (!data) {
        data = snapshot.preset ? presetData(chartPreset(snapshot.preset, worldline)) : {
          facts: snapshot.facts.map(fact => ({
            ...fact, version_no: 1, active_version: 1, created_at: FIXED_TIME, updated_at: FIXED_TIME,
            versions: [version(1, fact.display_text)], provenance: [],
          })),
          topics: snapshot.topics.map(topic => ({ ...topic, created_at: FIXED_TIME })),
          experiences: [],
          origin_at: null,
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

function factRow({ versions: _versions, provenance: _provenance, created_at: _created, ...row }: Fact) { return { ...row }; }
function experienceRow({ said: _said, ...row }: Experience) {
  return { ...row, status: 'active', is_expired: false, conversation_id: 'mock-conversation-1' };
}

type Criteria = {
  query: string | null; topic_id: string | null; pinned_only: boolean; updated_from: string | null; updated_to: string | null;
};

function criteriaFor(params: URLSearchParams): Criteria {
  const text = (key: string) => params.get(key)?.trim() || null;
  return {
    query: text('query'),
    topic_id: text('topic_id'),
    pinned_only: params.get('pinned_only') === 'true',
    updated_from: text('updated_from'),
    updated_to: text('updated_to'),
  };
}

function matchingFacts(data: ScopeData, criteria: Criteria): Fact[] {
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

const byCreated = <T extends { created_at: string; id: string }>(a: T, b: T) =>
  a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id);

function chart(scope: MemoryChart['scope'], data: ScopeData): MemoryChart {
  const topicIds = new Set(data.topics.map(topic => topic.topic_id));
  const facts = data.facts.map(fact => ({
    id: fact.fact_id, label: fact.display_text,
    topic_id: fact.topic_id && topicIds.has(fact.topic_id) ? fact.topic_id : null,
    created_at: fact.created_at, updated_at: fact.updated_at, is_pinned: fact.is_pinned,
    evidence_count: new Set(fact.provenance.map(row => row.observation_id)).size,
    version_count: fact.versions.length, resident: false,
  }));
  // Mirrors the resident order (pinned, corrected, mentioned) closely enough for a fixture.
  [...facts].sort((a, b) => Number(b.is_pinned) - Number(a.is_pinned)
    || Number(b.version_count > 1) - Number(a.version_count > 1) || b.evidence_count - a.evidence_count)
    .slice(0, RESIDENT_LIMIT).forEach(fact => { fact.resident = true; });
  const experiences = data.experiences.map(e => ({
    id: e.experience_id, label: e.display_text, created_at: e.created_at, updated_at: e.updated_at,
    is_pinned: e.is_pinned, expires_at: e.expires_at,
  }));
  const records = [...facts, ...experiences].sort(byCreated);
  const kept = new Set(records.slice(Math.max(0, records.length - CHART_LIMIT)).map(row => row.id));
  const earliest = records[0]?.created_at ?? null;
  return {
    scope, generated_at: new Date().toISOString(),
    origin_at: data.origin_at ?? earliest,
    topics: data.topics.map(topic => ({
      id: topic.topic_id, label: topic.display_label, created_at: topic.created_at,
      fact_count: facts.filter(fact => fact.topic_id === topic.topic_id).length,
    })).sort(byCreated),
    facts: facts.filter(row => kept.has(row.id)).sort(byCreated),
    experiences: experiences.filter(row => kept.has(row.id)).sort(byCreated),
    counts: { facts: facts.length, experiences: experiences.length, topics: data.topics.length },
    truncated: kept.size < records.length,
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
  const data = store.scope(JSON.stringify([scope.session_id, scope.worldline, scope.identity_mode]), scope.worldline);
  method = method.toUpperCase();
  if (method === 'GET') {
    if (path === '/status') return success({
      ...scope, pending_count: 0, processing_count: 0, failed_count: 0, failed_jobs: [],
    });
    if (path === '/facts') {
      const facts = matchingFacts(data, criteriaFor(params));
      const page = pagination(params, facts.length);
      return success({ scope, facts: facts.slice(page.offset, page.offset + page.limit).map(factRow), pagination: page });
    }
    if (path === '/experiences') {
      const query = params.get('query')?.trim().toLocaleLowerCase();
      const rows = data.experiences.filter(e => !query || e.display_text.toLocaleLowerCase().includes(query));
      const page = pagination(params, rows.length);
      return success({ scope, experiences: rows.slice(page.offset, page.offset + page.limit).map(experienceRow), pagination: page });
    }
    if (path === '/observations') {
      return success({ scope, observations: [], pagination: pagination(params, 0) });
    }
    if (path === '/topics') return success({
      scope, topics: data.topics.map(({ created_at: _created, ...topic }) => ({
        ...topic, fact_count: data.facts.filter(fact => fact.topic_id === topic.topic_id).length,
      })),
    });
    // Scope strings are echoed verbatim; the casts must not normalize incoming strings.
    if (path === '/chart') return success(chart(scope as MemoryChart['scope'], data));
  }

  const factMatch = path.match(/^\/facts\/([^/]+)(?:\/(details|presentation))?$/);
  if (factMatch) {
    let id: string;
    try { id = decodeURIComponent(factMatch[1]); } catch { return failure(422, 'invalid_fact_id'); }
    const fact = data.facts.find(item => item.fact_id === id);
    if (!fact) return failure(404, 'fact_not_found');
    const action = factMatch[2];
    if (method === 'GET' && (action === 'details' || !action)) return success({
      scope, fact: factRow(fact), versions: fact.versions.map(row => ({ ...row })),
      provenance: fact.provenance.map(row => ({ ...row })),
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
  const experienceMatch = path.match(/^\/experiences\/([^/]+)(?:\/(details|presentation))?$/);
  if (experienceMatch) {
    let id: string;
    try { id = decodeURIComponent(experienceMatch[1]); } catch { return failure(422, 'invalid_experience_id'); }
    const experience = data.experiences.find(item => item.experience_id === id);
    if (!experience) return failure(404, 'experience_not_found');
    const action = experienceMatch[2];
    if (method === 'GET' && action === 'details') return success({
      scope, observation_id: `${id}-o1`,
      experience: { ...experienceRow(experience), is_pinned: undefined, updated_at: undefined },
      provenance: experience.said.map(([excerpt, at], i) => ({
        source_message_id: i + 1, conversation_id: 'mock-conversation-1', source_created_at: iso(at),
        source_state: 'present', excerpt,
      })),
    });
    if (method === 'PATCH' && action === 'presentation') {
      if (typeof body.is_pinned !== 'boolean') return failure(422, 'invalid_is_pinned');
      experience.is_pinned = body.is_pinned;
      return success({ ok: true, scope, experience: experienceRow(experience) });
    }
    if (method === 'DELETE' && !action) {
      data.experiences = data.experiences.filter(item => item !== experience);
      return success({ ok: true, scope, experience_id: id });
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
    const { created_at: _created, ...row } = topic;
    return success({ ok: true, scope, topic: row });
  }
  if (method === 'POST' && (/^\/observations\/[^/]+\/ignore$/.test(path) || /^\/jobs\/[^/]+\/retry$/.test(path))) {
    return success({ ok: true, scope });
  }
  return failure(404, 'memory_endpoint_not_found');
}
