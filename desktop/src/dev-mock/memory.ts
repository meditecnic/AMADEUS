import { chartPreset, type ChartPreset, type Said } from './chartFixtures';
import type { FactProvenanceRow, FactVersionRow } from '../memory-space/readingDetails';
import type { MemoryChart } from '../memory-space/types';

export type SeedDiary = {
  diary_id: string;
  conversation_id: string;
  body_ja: string;
  body_zh: string;
  written_at: string;
  first_message_id?: number;
  last_message_id?: number;
  source_count?: number;
  content_epoch?: number;
  verified_phrases_json?: string;
};

export type MemorySeed = {
  facts: Array<{ fact_id: string; display_text: string; topic_id: string | null; is_pinned: boolean }>;
  topics: Array<{ topic_id: string; display_label: string; user_retained?: boolean }>;
  observations?: Array<{ observation_id: string; display_text: string; status?: string }>;
  diaries?: SeedDiary[];
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
type Topic = {
  topic_id: string;
  display_label: string;
  normalized_label: string;
  user_retained: boolean;
  aliases: string[];
  created_at: string;
};
type Diary = {
  diary_id: string;
  conversation_id: string;
  first_message_id: number;
  last_message_id: number;
  source_count: number;
  content_epoch: number;
  body_ja: string | null;
  body_zh: string | null;
  verified_phrases_json: string | null;
  written_at: string;
  deleted: boolean;
};
type Experience = {
  experience_id: string; display_text: string; created_at: string; updated_at: string;
  expires_at: string | null; is_pinned: boolean; said: Said[];
};
type Observation = {
  observation_id: string;
  display_text: string;
  status: 'candidate' | 'attached' | 'ignored';
  created_at: string;
};
type ScopeData = {
  facts: Fact[];
  topics: Topic[];
  experiences: Experience[];
  observations: Observation[];
  diaries: Diary[];
  origin_at: string | null;
};

const TRAILING_PUNCT = /[。.!！?？,，、;;；]+$/u;
const BRACKET_PAIRS: ReadonlyArray<[string, string]> = [
  ['《', '》'], ['〈', '〉'], ['「', '」'], ['『', '』'], ['【', '】'],
  ['(', ')'], ['（', '）'], ['[', ']'], ['“', '”'], ['‘', '’'], ['"', '"'], ["'", "'"],
];

function prepareTopicLabel(raw: unknown): { display: string; normalized: string } | null {
  if (typeof raw !== 'string') return null;
  let display = raw.normalize('NFKC').trim().replace(/\s+/g, ' ');
  for (;;) {
    const before = display;
    display = display.replace(TRAILING_PUNCT, '').trim();
    for (const [open, close] of BRACKET_PAIRS) {
      if (display.length >= 2 && display[0] === open && display[display.length - 1] === close) {
        display = display.slice(1, -1).trim();
        break;
      }
    }
    display = display.trim();
    if (display === before) break;
  }
  if (!display) return null;
  return { display, normalized: display.toLocaleLowerCase() };
}

function makeTopic(topicId: string, displayLabel: string, createdAt: string, userRetained = false): Topic {
  const prepared = prepareTopicLabel(displayLabel);
  return {
    topic_id: topicId,
    display_label: prepared?.display ?? displayLabel,
    normalized_label: prepared?.normalized ?? displayLabel.toLocaleLowerCase(),
    user_retained: userRetained,
    aliases: [],
    created_at: createdAt,
  };
}

function toDiary(seed: SeedDiary, index: number): Diary {
  return {
    diary_id: seed.diary_id,
    conversation_id: seed.conversation_id,
    first_message_id: seed.first_message_id ?? index * 10 + 1,
    last_message_id: seed.last_message_id ?? index * 10 + 8,
    source_count: seed.source_count ?? 8,
    content_epoch: seed.content_epoch ?? 0,
    body_ja: seed.body_ja,
    body_zh: seed.body_zh,
    verified_phrases_json: seed.verified_phrases_json ?? '[]',
    written_at: seed.written_at,
    deleted: false,
  };
}

const defaultObservations = (at = FIXED_TIME): Observation[] => [
  { observation_id: 'obs-1', display_text: '你偏爱手冲深度烘焙的黑咖啡。', status: 'candidate', created_at: at },
  { observation_id: 'obs-2', display_text: '你通常会在深夜整理新的实验笔记。', status: 'candidate', created_at: at },
];

const toObservation = (
  obs: NonNullable<MemorySeed['observations']>[number],
): Observation => ({
  observation_id: obs.observation_id,
  display_text: obs.display_text,
  status: obs.status === 'attached' || obs.status === 'ignored' ? obs.status : 'candidate',
  created_at: FIXED_TIME,
});

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
    topics: preset.topics.map((t) =>
      makeTopic(t.id, t.label, firstIn(t.id) ?? iso(preset.origin ?? '2026-01-01'), Boolean(t.userRetained)),
    ),
    experiences: preset.experiences.map((e) => ({
      experience_id: e.id, display_text: e.text, created_at: iso(e.said[0][1]), updated_at: iso(e.said[0][1]),
      expires_at: e.expires ? iso(e.expires) : null, is_pinned: Boolean(e.pin), said: e.said,
    })),
    observations: defaultObservations(preset.origin ? iso(preset.origin) : FIXED_TIME),
    diaries: (preset.diaries ?? []).map((d, i) => toDiary({
      diary_id: d.id,
      conversation_id: d.conversationId,
      body_ja: d.bodyJa,
      body_zh: d.bodyZh,
      written_at: iso(d.writtenAt),
      source_count: d.sourceCount,
      verified_phrases_json: JSON.stringify(d.verifiedPhrases ?? []),
    }, i)),
    origin_at: preset.origin ? iso(preset.origin) : null,
  };
}

export function createMemoryStore(seed: MemorySeed) {
  // Snapshot now; new scopes must not inherit later caller or scope mutations.
  const snapshot: MemorySeed = {
    facts: seed.facts.map(fact => ({ ...fact })),
    topics: seed.topics.map(topic => ({ ...topic })),
    observations: seed.observations?.map(obs => ({ ...obs })),
    diaries: seed.diaries?.map(diary => ({ ...diary })),
    preset: seed.preset,
  };
  const scopes = new Map<string, ScopeData>();
  return {
    scope(key: string, worldline = 'steins_gate'): ScopeData {
      let data = scopes.get(key);
      if (!data) {
        if (snapshot.preset) {
          data = presetData(chartPreset(snapshot.preset, worldline));
          if (snapshot.observations) {
            data.observations = snapshot.observations.map(toObservation);
          }
          if (snapshot.diaries) {
            data.diaries = snapshot.diaries.map(toDiary);
          }
        } else {
          data = {
            facts: snapshot.facts.map(fact => ({
              ...fact, version_no: 1, active_version: 1, created_at: FIXED_TIME, updated_at: FIXED_TIME,
              versions: [version(1, fact.display_text)], provenance: [],
            })),
            topics: snapshot.topics.map(topic =>
              makeTopic(topic.topic_id, topic.display_label, FIXED_TIME, Boolean(topic.user_retained)),
            ),
            experiences: [],
            observations: (snapshot.observations ?? []).map(toObservation),
            diaries: (snapshot.diaries ?? []).map(toDiary),
            origin_at: null,
          };
        }
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
  const chartTopics = data.topics.map(topic => ({
    id: topic.topic_id, label: topic.display_label, created_at: topic.created_at,
    fact_count: facts.filter(fact => fact.topic_id === topic.topic_id).length,
  })).filter(topic => topic.fact_count > 0).sort(byCreated);
  return {
    scope, generated_at: new Date().toISOString(),
    origin_at: data.origin_at ?? earliest,
    topics: chartTopics,
    facts: facts.filter(row => kept.has(row.id)).sort(byCreated),
    experiences: experiences.filter(row => kept.has(row.id)).sort(byCreated),
    counts: { facts: facts.length, experiences: experiences.length, topics: chartTopics.length },
    truncated: kept.size < records.length,
  };
}

let mockTopicCounter = 0;

function diaryRow(diary: Diary) {
  return {
    diary_id: diary.diary_id,
    conversation_id: diary.conversation_id,
    first_message_id: diary.first_message_id,
    last_message_id: diary.last_message_id,
    source_count: diary.source_count,
    content_epoch: diary.content_epoch,
    body_ja: diary.body_ja,
    body_zh: diary.body_zh,
    verified_phrases_json: diary.verified_phrases_json,
    written_at: diary.written_at,
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
      const page = pagination(params, data.observations.length);
      return success({
        scope,
        observations: data.observations.slice(page.offset, page.offset + page.limit).map(obs => ({ ...obs })),
        pagination: page,
      });
    }
    if (path === '/topics') {
      const topics = data.topics
        .map(topic => ({
          topic_id: topic.topic_id,
          display_label: topic.display_label,
          fact_count: data.facts.filter(fact => fact.topic_id === topic.topic_id).length,
          user_retained: topic.user_retained,
          normalized_label: topic.normalized_label,
        }))
        .filter(topic => topic.fact_count > 0 || topic.user_retained)
        .sort((a, b) => a.normalized_label.localeCompare(b.normalized_label) || a.topic_id.localeCompare(b.topic_id))
        .map(({ user_retained: _retained, normalized_label: _norm, ...row }) => row);
      return success({ scope, topics });
    }
    if (path === '/diaries') {
      const diaries = data.diaries
        .filter(diary => !diary.deleted && diary.body_ja !== null && diary.body_zh !== null)
        .sort((a, b) => b.written_at.localeCompare(a.written_at) || a.diary_id.localeCompare(b.diary_id))
        .map(diaryRow);
      return success({ scope, diaries });
    }
    // Scope strings are echoed verbatim; the casts must not normalize incoming strings.
    if (path === '/chart') return success(chart(scope as MemoryChart['scope'], data));
  }

  if (method === 'POST' && path === '/topics') {
    if (typeof body.display_label !== 'string') return failure(422, 'invalid_display_label');
    const prepared = prepareTopicLabel(body.display_label);
    if (!prepared) return failure(422, 'empty_display_label');
    const matches = data.topics.filter(
      item => item.normalized_label === prepared.normalized || item.aliases.includes(prepared.normalized),
    );
    if (matches.length > 1) return failure(409, 'topic_conflict');
    if (matches.length === 1) {
      const existing = matches[0];
      existing.user_retained = true;
      return success({
        topic_id: existing.topic_id,
        display_label: existing.display_label,
        normalized_label: existing.normalized_label,
        user_retained: true,
        scope,
      });
    }
    const created = makeTopic(`topic-user-${++mockTopicCounter}`, prepared.display, new Date().toISOString(), true);
    data.topics.push(created);
    return success({
      topic_id: created.topic_id,
      display_label: created.display_label,
      normalized_label: created.normalized_label,
      user_retained: true,
      scope,
    });
  }

  const diaryMatch = path.match(/^\/diaries\/([^/]+)$/);
  if (method === 'DELETE' && diaryMatch) {
    let id: string;
    try { id = decodeURIComponent(diaryMatch[1]); } catch { return failure(422, 'invalid_diary_id'); }
    const diary = data.diaries.find(item => item.diary_id === id && !item.deleted);
    if (!diary) return failure(404, 'diary_not_found');
    diary.deleted = true;
    diary.body_ja = null;
    diary.body_zh = null;
    diary.verified_phrases_json = null;
    return { status: 204, body: null };
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
      if ('topic_id' in body) {
        const previousTopicId = fact.topic_id;
        fact.topic_id = body.topic_id as string | null;
        if (previousTopicId && previousTopicId !== fact.topic_id) {
          const remaining = data.facts.some(item => item.topic_id === previousTopicId);
          if (!remaining) {
            const prevTopic = data.topics.find(item => item.topic_id === previousTopicId);
            if (prevTopic) prevTopic.user_retained = true;
          }
        }
      }
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
  if (topicMatch && (method === 'PATCH' || method === 'DELETE')) {
    let id: string;
    try { id = decodeURIComponent(topicMatch[1]); } catch { return failure(422, 'invalid_topic_id'); }
    const topic = data.topics.find(item => item.topic_id === id);
    if (!topic) return failure(404, 'topic_not_found');
    if (method === 'DELETE') {
      if (data.facts.some(fact => fact.topic_id === id)) {
        return failure(409, 'topic_not_empty');
      }
      data.topics = data.topics.filter(item => item.topic_id !== id);
      return { status: 204, body: null };
    }
    const prepared = prepareTopicLabel(body.display_label);
    if (!prepared) return failure(422, 'invalid_display_label');
    if (prepared.normalized !== topic.normalized_label) {
      if (!topic.aliases.includes(topic.normalized_label)) {
        topic.aliases.push(topic.normalized_label);
      }
      topic.aliases = topic.aliases.filter(alias => alias !== prepared.normalized);
      topic.normalized_label = prepared.normalized;
    }
    topic.display_label = prepared.display;
    return success({
      ok: true,
      scope,
      topic: { topic_id: topic.topic_id, display_label: topic.display_label },
    });
  }
  const obsMatch = path.match(/^\/observations\/([^/]+)\/(ignore|confirm)$/);
  if (method === 'POST' && obsMatch) {
    let id: string;
    try { id = decodeURIComponent(obsMatch[1]); } catch { return failure(422, 'invalid_observation_id'); }
    const action = obsMatch[2];
    const obs = data.observations.find(item => item.observation_id === id);
    if (!obs) return failure(404, 'observation_not_found');
    if (action === 'confirm') {
      if (obs.status !== 'candidate') return failure(409, 'observation_not_confirmable');
      obs.status = 'attached';
      const factId = `fact-${id}`;
      if (!data.facts.some(item => item.fact_id === factId)) {
        const now = new Date().toISOString();
        data.facts.push({
          fact_id: factId,
          display_text: obs.display_text,
          topic_id: null,
          is_pinned: false,
          version_no: 1,
          active_version: 1,
          created_at: now,
          updated_at: now,
          versions: [version(1, obs.display_text, now)],
          provenance: [{
            version_no: 1,
            observation_id: id,
            source_message_id: 1,
            conversation_id: 'mock-conversation-1',
            source_created_at: obs.created_at,
            source_state: 'present',
            excerpt: obs.display_text,
          }],
        });
      }
    } else {
      if (obs.status === 'attached') return failure(409, 'observation_state_conflict');
      obs.status = 'ignored';
    }
    return success({ ok: true, scope });
  }
  if (method === 'POST' && /^\/jobs\/[^/]+\/retry$/.test(path)) {
    return success({ ok: true, scope });
  }
  return failure(404, 'memory_endpoint_not_found');
}
