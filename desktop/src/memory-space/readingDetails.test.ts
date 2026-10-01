import { describe, expect, it, vi } from 'vitest';

import {
  buildDetailsUrl,
  CORRECTION_NOTICE,
  createDetailsController,
  deriveExperienceReading,
  deriveFactReading,
  fetchDetails,
  formatTimeHuman,
  topicAttributionForFact,
  type DetailsRequest,
  type ExperienceDetailsPayload,
  type FactDetailsPayload,
} from './readingDetails';
import type { MemoryProjection } from './types';

const scope = { sessionId: 's', worldline: 'steins_gate' as const, identityMode: 'okabe' as const };
const betaScope = { sessionId: 's', worldline: 'beta' as const, identityMode: 'okabe' as const };

function factRequest(recordId = 'f1'): DetailsRequest {
  return { scope, kind: 'fact', recordId };
}

function experienceRequest(recordId = 'e1'): DetailsRequest {
  return { scope, kind: 'experience', recordId };
}

function factPayload(overrides: Partial<FactDetailsPayload> = {}): FactDetailsPayload {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    fact: { fact_id: 'f1', topic_id: 't1', is_pinned: false, active_version: 2, display_text: '喜欢黑咖啡' },
    versions: [
      {
        version_no: 1,
        display_text: '喜欢咖啡',
        change_kind: 'create',
        previous_version: null,
        valid_from: '2026-08-01T00:00:00Z',
        invalid_at: '2026-08-10T00:00:00Z',
        is_active: false,
      },
      {
        version_no: 2,
        display_text: '喜欢黑咖啡',
        change_kind: 'user_edit',
        previous_version: 1,
        valid_from: '2026-08-10T00:00:00Z',
        invalid_at: null,
        is_active: true,
      },
    ],
    provenance: [
      {
        version_no: 1,
        observation_id: 'o1',
        source_message_id: 11,
        conversation_id: 'c1',
        source_created_at: '2026-08-01T00:00:00Z',
        source_state: 'present',
        excerpt: '我挺喜欢咖啡的',
      },
      {
        version_no: 2,
        observation_id: 'o2',
        source_message_id: 42,
        conversation_id: 'c1',
        source_created_at: '2026-08-10T00:00:00Z',
        source_state: 'present',
        excerpt: '其实是黑咖啡',
      },
    ],
    ...overrides,
  };
}

function experiencePayload(overrides: Partial<ExperienceDetailsPayload> = {}): ExperienceDetailsPayload {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    experience: {
      experience_id: 'e1',
      conversation_id: 'c1',
      display_text: '一起看了星空',
      status: 'active',
      expires_at: '2026-09-01T00:00:00Z',
      created_at: '2026-08-12T00:00:00Z',
      is_expired: false,
    },
    observation_id: 'o9',
    provenance: [
      {
        source_message_id: 7,
        conversation_id: 'c1',
        source_created_at: '2026-08-12T00:00:00Z',
        source_state: 'present',
        excerpt: '今晚的星空很好看',
      },
    ],
    ...overrides,
  };
}

function envelope(): MemoryProjection {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    view: 'overview',
    projection_version: 'memory-projection-v1',
    generated_at: '2026-08-14T00:00:00Z',
    criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    composition: { active_facts: 1, active_experiences: 0, eligible_topics: 1, latest_memory_change_at: null, person_anchors_supported: false },
    budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
    eligible: { nodes: 3, edges: 2, records: 1, results: 1 },
    shown: { nodes: 3, edges: 2, records: 1, results: 1 },
    truncated: { nodes: false, edges: false, records: false, results: false },
    empty: false,
    result_ids: ['fact:f1'],
    nodes: [
      { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
      { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 1, experience_count: 0 },
      { kind: 'fact', projection_id: 'fact:f1', fact_id: 'f1', label: '喜欢黑咖啡', is_pinned: false, updated_at: '2026-08-14T00:00:00Z', topic_id: 't1' },
    ],
    edges: [
      { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
      { kind: 'has_topic', from: 'fact:f1', to: 'topic:t1' },
    ],
  };
}

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function errorResponse(status: number, code: string) {
  return { ok: false, status, json: async () => ({ detail: { code } }) } as unknown as Response;
}

describe('buildDetailsUrl', () => {
  it('uses the explicit scope and the canonical record id', () => {
    const url = buildDetailsUrl(factRequest('f1'));
    expect(url).toContain('/api/memory/facts/f1/details');
    expect(url).toContain('session_id=s');
    expect(url).toContain('worldline=steins_gate');
    expect(url).toContain('identity_mode=okabe');
    expect(buildDetailsUrl(experienceRequest('e1'))).toContain('/api/memory/experiences/e1/details');
  });
});

describe('fetchDetails', () => {
  it('maps 404 to not-found and 500 to backend-failure with the stable code', async () => {
    const missing = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(errorResponse(404, 'fact_not_found'))) as typeof fetch });
    expect(missing.type).toBe('not-found');
    const failed = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(errorResponse(500, 'memory_operation_failed'))) as typeof fetch });
    expect(failed.type).toBe('backend-failure');
    if (failed.type === 'backend-failure') {
      expect(failed.code).toBe('memory_operation_failed');
      expect(failed.status).toBe(500);
    }
  });

  it('maps transport failure to transport-error and never leaks body text', async () => {
    const outcome = await fetchDetails(factRequest(), { fetcher: (() => Promise.reject(new Error('network down'))) as typeof fetch });
    expect(outcome.type).toBe('transport-error');
  });

  it('passes the abort signal through to transport', async () => {
    const seen: Array<RequestInit | undefined> = [];
    const controller = new AbortController();
    await fetchDetails(factRequest(), {
      fetcher: ((url: string, init?: RequestInit) => {
        seen.push(init);
        return Promise.resolve(okResponse(factPayload()));
      }) as typeof fetch,
      signal: controller.signal,
    });
    expect(seen[0]?.signal).toBe(controller.signal);
  });

  it('rejects a malformed 200 payload as backend-failure', async () => {
    const unrelated = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(okResponse({ unrelated: true }))) as typeof fetch });
    expect(unrelated.type).toBe('backend-failure');
    if (unrelated.type === 'backend-failure') expect(unrelated.code).toBe('invalid_details_payload');
  });

  it('fail-closes on structurally broken nested rows (versions: [null])', async () => {
    const broken = { ...factPayload(), versions: [null] };
    const outcome = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(okResponse(broken))) as typeof fetch });
    expect(outcome.type).toBe('backend-failure');
    if (outcome.type === 'backend-failure') expect(outcome.code).toBe('invalid_details_payload');
  });

  it('fail-closes when the payload scope does not match the request', async () => {
    const foreign = {
      ...factPayload(),
      scope: { session_id: 's', worldline: 'beta', identity_mode: 'okabe' },
    };
    const outcome = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(okResponse(foreign))) as typeof fetch });
    expect(outcome.type).toBe('backend-failure');
    if (outcome.type === 'backend-failure') expect(outcome.code).toBe('invalid_details_payload');
  });

  it('fail-closes when the payload record id does not match the request', async () => {
    const foreign = { ...factPayload(), fact: { ...factPayload().fact, fact_id: 'other-fact' } };
    const outcome = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(okResponse(foreign))) as typeof fetch });
    expect(outcome.type).toBe('backend-failure');
    const foreignExperience = {
      ...experiencePayload(),
      experience: { ...experiencePayload().experience, experience_id: 'other-exp' },
    };
    const experienceOutcome = await fetchDetails(
      experienceRequest(),
      { fetcher: (() => Promise.resolve(okResponse(foreignExperience))) as typeof fetch },
    );
    expect(experienceOutcome.type).toBe('backend-failure');
  });

  it('fail-closes on a provenance row with a malformed source_message_id', async () => {
    const broken = {
      ...factPayload(),
      provenance: [{ ...factPayload().provenance[0], source_message_id: '11' }],
    };
    const outcome = await fetchDetails(factRequest(), { fetcher: (() => Promise.resolve(okResponse(broken))) as typeof fetch });
    expect(outcome.type).toBe('backend-failure');
  });
});

describe('createDetailsController', () => {
  it('activates with loading then ok, and caches the active request across views', async () => {
    const loader = vi.fn(async () => ({ type: 'ok', payload: factPayload() } as const));
    const controller = createDetailsController({ load: loader });
    const seen: string[] = [];
    const initial = controller.activate(factRequest(), (outcome) => seen.push(outcome.type));
    expect(initial.type).toBe('loading');
    await vi.waitFor(() => expect(seen).toContain('ok'));
    // Same active request from another view: cached, no second fetch.
    const second = controller.activate(factRequest(), vi.fn());
    expect(second.type).toBe('ok');
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('clears the cache when the selection changes and aborts the superseded request', async () => {
    let releaseFirst: ((value: unknown) => void) | undefined;
    const aborted: Array<AbortSignal | undefined> = [];
    const load = vi.fn((request: DetailsRequest, signal?: AbortSignal) => {
      aborted.push(signal);
      if (request.recordId === 'f1') {
        return new Promise((resolve) => {
          releaseFirst = resolve;
        });
      }
      return Promise.resolve({ type: 'ok', payload: factPayload({ fact: { ...factPayload().fact, fact_id: 'f2' } }) });
    });
    const controller = createDetailsController({ load: load as never });
    controller.activate(factRequest('f1'), vi.fn());
    const second = controller.activate(factRequest('f2'), vi.fn());
    expect(second.type).toBe('loading');
    releaseFirst?.({ type: 'ok', payload: factPayload() });
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(aborted[0]?.aborted).toBe(true);
    // The late first payload must never be served: re-activating f1 refetches.
    const third = controller.activate(factRequest('f1'), vi.fn());
    expect(third.type).toBe('loading');
    expect(load).toHaveBeenCalledTimes(3);
  });

  it('deactivate aborts in-flight work; an unresolved payload is not retained', async () => {
    const signals: Array<AbortSignal | undefined> = [];
    const load = vi.fn((request: DetailsRequest, signal?: AbortSignal) => {
      signals.push(signal);
      return new Promise(() => undefined);
    });
    const controller = createDetailsController({ load: load as never });
    controller.activate(factRequest(), vi.fn());
    controller.deactivate();
    expect(signals[0]?.aborted).toBe(true);
    const again = controller.activate(factRequest(), vi.fn());
    expect(again.type).toBe('loading');
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('delivers the abort signal to the default transport loader', async () => {
    const fetcher = vi.fn(() => new Promise(() => undefined));
    // Inject the real fetchDetails through a fetch stub to observe init.
    const { createDetailsController: createReal } = await import('./readingDetails');
    const real = createReal();
    vi.stubGlobal('fetch', fetcher);
    real.activate(factRequest(), vi.fn());
    expect(fetcher).toHaveBeenCalledTimes(1);
    const init = fetcher.mock.calls[0][1] as RequestInit | undefined;
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    real.deactivate();
    expect((init?.signal as AbortSignal).aborted).toBe(true);
    vi.unstubAllGlobals();
  });

  it('P2-A: keeps the successful payload across non-record selection changes', async () => {
    const load = vi.fn(async () => ({ type: 'ok', payload: factPayload() } as const));
    const controller = createDetailsController({ load: load as never });
    const initial = controller.activate(factRequest(), vi.fn());
    expect(initial.type).toBe('loading');
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    // Selecting SOUL / Topic deactivates but must not drop the payload...
    controller.deactivate();
    // ...so reactivating the SAME record never re-GETs.
    const again = controller.activate(factRequest(), vi.fn());
    expect(again.type).toBe('ok');
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('P2-A: a different record or scope replaces the cache; force bypasses it', async () => {
    const load = vi.fn(async (request: DetailsRequest) => ({ type: 'ok', payload: factPayload({ fact: { ...factPayload().fact, fact_id: request.recordId } }) } as const));
    const controller = createDetailsController({ load: load as never });
    controller.activate(factRequest('f1'), vi.fn());
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    // Different record -> refetch and replace the slot.
    controller.activate(factRequest('f2'), vi.fn());
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    // Back to f1 -> the slot was replaced, so this refetches.
    controller.activate(factRequest('f1'), vi.fn());
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(3));
    // Same record, explicit retry -> force bypasses the retained payload.
    const forced = controller.activate(factRequest('f1'), vi.fn(), { force: true });
    expect(forced.type).toBe('loading');
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(4));
    // Different scope -> cache key mismatch -> refetch.
    controller.activate({ ...factRequest('f1'), scope: betaScope }, vi.fn());
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(5));
  });

  it('P2-A: invalidate() (scope change) drops the retained payload', async () => {
    const load = vi.fn(async () => ({ type: 'ok', payload: factPayload() } as const));
    const controller = createDetailsController({ load: load as never });
    controller.activate(factRequest(), vi.fn());
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    controller.invalidate();
    const again = controller.activate(factRequest(), vi.fn());
    expect(again.type).toBe('loading');
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it('never serves a late response after epoch change', async () => {
    let release: ((value: unknown) => void) | undefined;
    const load = vi.fn((request: DetailsRequest) => {
      if (request.scope.worldline === 'steins_gate') {
        return new Promise((resolve) => {
          release = resolve;
        });
      }
      return Promise.resolve({ type: 'not-found' });
    });
    const controller = createDetailsController({ load: load as never });
    const seen: string[] = [];
    controller.activate(factRequest(), (outcome) => seen.push(outcome.type));
    controller.activate({ ...factRequest(), scope: betaScope }, (outcome) => seen.push(`beta:${outcome.type}`));
    await vi.waitFor(() => expect(seen).toContain('beta:not-found'));
    release?.({ type: 'ok', payload: factPayload() });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(seen.filter((entry) => entry === 'ok')).toHaveLength(0);
  });
});

describe('topicAttributionForFact', () => {
  it('uses the canonical envelope Topic label when the has_topic edge is present', () => {
    const attribution = topicAttributionForFact({ projectionId: 'fact:f1', topicId: 't1' }, envelope());
    expect(attribution).toEqual({ kind: 'grouped', label: '咖啡' });
  });

  it('says ungrouped only when topic_id is null', () => {
    const attribution = topicAttributionForFact({ projectionId: 'fact:f1', topicId: null }, envelope());
    expect(attribution).toEqual({ kind: 'ungrouped' });
  });

  it('marks out-of-envelope topic ids honestly instead of calling them ungrouped', () => {
    const attribution = topicAttributionForFact({ projectionId: 'fact:f1', topicId: 't-missing' }, envelope());
    expect(attribution).toEqual({ kind: 'unavailable-in-projection', topicId: 't-missing' });
  });
});

describe('deriveFactReading', () => {
  it('renders active content once and groups provenance by version', () => {
    const model = deriveFactReading(factPayload(), envelope(), { projectionId: 'fact:f1', topicId: 't1' });
    expect(model.primaryText).toBe('喜欢黑咖啡');
    expect(model.topicAttribution).toEqual({ kind: 'grouped', label: '咖啡' });
    expect(model.versionCount).toBe(2);
    // §7.3: collapsed disclosure shows total count AND the latest change kind.
    expect(model.revisionSummary).toBe('2 个版本 · 最新变更：用户修订');
    // Active-version sources are the default evidence region.
    expect(model.activeSources.map((line) => line.excerpt)).toEqual(['其实是黑咖啡']);
    // Historical sources stay attached to their own version only.
    const history = model.historyVersions.find((version) => version.versionNo === 1);
    expect(history?.sources.map((line) => line.excerpt)).toEqual(['我挺喜欢咖啡的']);
    const active = model.historyVersions.find((version) => version.versionNo === 2);
    expect(active?.isActive).toBe(true);
    expect(active?.changeKindHuman).toBeTruthy();
  });

  it('renders the source-deleted disclosure without any excerpt content', () => {
    const payload = factPayload({
      provenance: [
        {
          version_no: 2,
          observation_id: 'o2',
          source_message_id: 42,
          conversation_id: 'c1',
          source_created_at: '2026-08-10T00:00:00Z',
          source_state: 'deleted',
          excerpt: null,
        },
      ],
    });
    const model = deriveFactReading(payload, envelope(), { projectionId: 'fact:f1', topicId: 't1' });
    expect(model.activeSources[0].sourceErased).toBe(true);
    expect(model.activeSources[0].excerpt).toBeNull();
    const flat = JSON.stringify(model);
    expect(flat).not.toContain('今晚');
    expect(flat).not.toContain('其实是黑咖啡');
  });

  it('never surfaces confidence anywhere, diagnostics included', () => {
    // The frozen backend payload carries confidence; the reading model must drop it.
    const payload = { ...factPayload(), versions: factPayload().versions.map((version) => ({ ...version, confidence: 0.9 })) };
    const model = deriveFactReading(payload as unknown as FactDetailsPayload, envelope(), { projectionId: 'fact:f1', topicId: 't1' });
    const flat = JSON.stringify(model);
    expect(flat).not.toContain('confidence');
    expect(flat).not.toContain('0.9');
  });

  it('keeps raw identifiers only in diagnostics and ships the read-only correction notice', () => {
    const model = deriveFactReading(factPayload(), envelope(), { projectionId: 'fact:f1', topicId: 't1' });
    expect(model.diagnostics.factId).toBe('f1');
    expect(model.diagnostics.observationIds).toEqual(['o1', 'o2']);
    expect(model.diagnostics.sourceMessageIds).toEqual([11, 42]);
    expect(model.diagnostics.rawTimestamps).toEqual(
      expect.arrayContaining(['2026-08-01T00:00:00Z', '2026-08-10T00:00:00Z']),
    );
    expect(model.correctionNotice).toBe(CORRECTION_NOTICE);
    expect(model.primaryText).not.toContain('f1');
  });
});

describe('deriveExperienceReading', () => {
  it('renders active text, created time, expiry disclosure and no version surface', () => {
    const model = deriveExperienceReading(experiencePayload());
    expect(model.primaryText).toBe('一起看了星空');
    expect(model.createdHuman).toBeTruthy();
    expect(model.expiry).toEqual({ expired: false, expiresHuman: expect.any(String) });
    expect('versionCount' in model).toBe(false);
    expect(model.sources[0].excerpt).toBe('今晚的星空很好看');
    expect(model.diagnostics.sourceMessageIds).toEqual([7]);
    expect(model.diagnostics.rawTimestamps).toEqual(
      expect.arrayContaining(['2026-08-12T00:00:00Z', '2026-09-01T00:00:00Z']),
    );
    expect(model.correctionNotice).toBe(CORRECTION_NOTICE);
  });

  it('marks is_expired honestly and distinctly from deletion', () => {
    const model = deriveExperienceReading(
      experiencePayload({ experience: { ...experiencePayload().experience, is_expired: true, status: 'expired' } }),
    );
    expect(model.expiry?.expired).toBe(true);
    const flat = JSON.stringify(model);
    expect(flat).not.toContain('deleted');
  });

  it('never surfaces confidence for experiences either', () => {
    const payload = { ...experiencePayload(), experience: { ...experiencePayload().experience, confidence: 0.77 } };
    const model = deriveExperienceReading(payload as unknown as ExperienceDetailsPayload);
    const flat = JSON.stringify(model);
    expect(flat).not.toContain('confidence');
    expect(flat).not.toContain('0.77');
  });
});

describe('formatTimeHuman', () => {
  const now = new Date('2026-08-14T12:00:00Z').getTime();
  it('is relative-first for recent times and absolute later', () => {
    expect(formatTimeHuman('2026-08-14T11:59:30Z', now)).toBe('刚刚');
    expect(formatTimeHuman('2026-08-14T11:30:00Z', now)).toBe('30 分钟前');
    expect(formatTimeHuman('2026-08-14T07:00:00Z', now)).toBe('5 小时前');
    expect(formatTimeHuman('2026-08-12T12:00:00Z', now)).toBe('2 天前');
    expect(formatTimeHuman('2026-05-01T00:00:00Z', now)).toContain('2026-05-01');
  });
  it('returns null for unparseable values instead of inventing a time', () => {
    expect(formatTimeHuman('not-a-date', now)).toBeNull();
  });
});
