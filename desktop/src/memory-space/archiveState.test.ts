import { describe, expect, it, vi } from 'vitest';

import { createArchiveModule, type ArchiveIntent } from './archiveState';
import type { ArchiveScope } from './archiveApi';

const SCOPE_A: ArchiveScope = { sessionId: 's-a', worldline: 'steins_gate', identityMode: 'okabe' };
const SCOPE_B: ArchiveScope = { sessionId: 's-a', worldline: 'beta', identityMode: 'okabe' };

function factRow(overrides: Record<string, unknown> = {}) {
  return {
    fact_id: 'f1',
    display_text: '喜欢黑咖啡',
    version_no: 1,
    active_version: 1,
    is_pinned: false,
    topic_id: null,
    confidence: 0.9,
    updated_at: '2026-08-20T00:00:00Z',
    ...overrides,
  };
}

function experienceRow(overrides: Record<string, unknown> = {}) {
  return {
    experience_id: 'e1',
    conversation_id: 'c1',
    display_text: '深夜实验',
    confidence: 0.8,
    status: 'active',
    expires_at: null,
    created_at: '2026-08-01T00:00:00Z',
    is_expired: false,
    is_pinned: false,
    ...overrides,
  };
}

type Responder = (url: string, init?: RequestInit) => { status: number; body: unknown } | undefined;

function scriptedFetcher(responder: Responder) {
  const urls: string[] = [];
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    urls.push(input);
    const scripted = responder(input, init);
    if (!scripted) return { ok: true, status: 200, json: async () => ({}) } as unknown as Response;
    return {
      ok: scripted.status >= 200 && scripted.status < 300,
      status: scripted.status,
      json: async () => scripted.body,
    } as unknown as Response;
  });
  return { fetcher, urls };
}

function defaultResponder(scope: ArchiveScope): Responder {
  const scopeQuery = `session_id=${scope.sessionId}`;
  return (url) => {
    if (!url.includes(scopeQuery)) return undefined;
    if (url.startsWith('/api/memory/facts?')) {
      return {
        status: 200,
        body: {
          facts: [factRow()],
          scope: { session_id: scope.sessionId, worldline: scope.worldline, identity_mode: scope.identityMode },
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        },
      };
    }
    if (url.startsWith('/api/memory/experiences?')) {
      return {
        status: 200,
        body: {
          experiences: [experienceRow()],
          scope: { session_id: scope.sessionId, worldline: scope.worldline, identity_mode: scope.identityMode },
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        },
      };
    }
    if (url.startsWith('/api/memory/topics?')) {
      return {
        status: 200,
        body: {
          scope: { session_id: scope.sessionId, worldline: scope.worldline, identity_mode: scope.identityMode },
          topics: [{ topic_id: 't1', display_label: '咖啡', fact_count: 1 }],
        },
      };
    }
    if (url.startsWith('/api/memory/status?')) {
      return {
        status: 200,
        body: {
          session_id: scope.sessionId,
          worldline: scope.worldline,
          identity_mode: scope.identityMode,
          runtime_mode: 'v11', pending_count: 0,
          processing_count: 0,
          failed_count: 0,
          failed_jobs: [],
        },
      };
    }
    if (url.startsWith('/api/memory/observations?')) {
      return { status: 200, body: { observations: [], pagination: { total: 0 } } };
    }
    if (url.startsWith(`/api/memory/facts/f1/details`)) {
      return {
        status: 200,
        body: {
          scope: { session_id: scope.sessionId, worldline: scope.worldline, identity_mode: scope.identityMode },
          fact: { fact_id: 'f1', topic_id: null, is_pinned: false, active_version: 1, display_text: '喜欢黑咖啡' },
          versions: [
            {
              version_no: 1,
              display_text: '喜欢黑咖啡',
              change_kind: 'create',
              previous_version: null,
              valid_from: '2026-08-20T00:00:00Z',
              invalid_at: null,
              is_active: true,
            },
          ],
          provenance: [],
        },
      };
    }
    return undefined;
  };
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function openReady(scope: ArchiveScope = SCOPE_A) {
  const { fetcher, urls } = scriptedFetcher(defaultResponder(scope));
  const module = createArchiveModule({ fetcher });
  module.open(scope);
  await flush();
  return { module, fetcher, urls };
}

describe('archive deep module: browse seams', () => {
  it.each(['legacy', 'shadow', undefined])('keeps browsing but blocks governance when runtime is %s', async (runtimeMode) => {
    const base = defaultResponder(SCOPE_A);
    const { fetcher } = scriptedFetcher((url, init) => {
      const response = base(url, init);
      if (response && url.startsWith('/api/memory/status?')) {
        response.body = { ...(response.body as object), runtime_mode: runtimeMode };
      }
      return response;
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    expect(module.snapshot().facts.rows.length).toBe(1);
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'request-delete' });
    module.dispatch({ type: 'confirm-delete' });
    expect(module.snapshot().editor).toBeNull();
    expect(module.snapshot().deleteFlow).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
    module.dispose();
  });
  it('open(scope) loads facts + experiences + topics through repository seams only', async () => {
    const { module, urls } = await openReady();
    const state = module.snapshot();
    expect(state.scope).toEqual(SCOPE_A);
    expect(state.facts.status).toBe('ready');
    expect(state.facts.rows).toHaveLength(1);
    expect(state.experiences.rows).toHaveLength(1);
    expect(state.topics.items).toHaveLength(1);
    // Invariant 2: Archive never issues projection requests for rows.
    expect(urls.some((url) => url.includes('graph/projection'))).toBe(false);
    // Confidence never enters Archive row state.
    expect(JSON.stringify(state.facts.rows)).not.toContain('confidence');
    module.dispose();
  });

  it('scope switch aborts in-flight work and leaves zero previous-scope cache', async () => {
    let releaseA: ((value: Response) => void) | null = null;
    const fetcher = vi.fn((input: string) => {
      if (input.includes('session_id=s-a') && input.includes('worldline=steins_gate')) {
        return new Promise<Response>((resolve) => {
          releaseA = resolve;
        });
      }
      const scripted = defaultResponder(SCOPE_B)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    module.open(SCOPE_B);
    await flush();
    // Late scope-A payload resolves AFTER the switch: nothing may land.
    releaseA?.({
      ok: true,
      status: 200,
      json: async () => ({
        facts: [factRow({ fact_id: 'stale-a', display_text: '旧范围事实' })],
        scope: { session_id: 's-a', worldline: 'steins_gate', identity_mode: 'okabe' },
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    } as unknown as Response);
    await flush();
    const state = module.snapshot();
    expect(state.scope).toEqual(SCOPE_B);
    expect(JSON.stringify(state)).not.toContain('旧范围事实');
    expect(JSON.stringify(state)).not.toContain('stale-a');
    expect(state.facts.rows.length + state.experiences.rows.length).toBeGreaterThan(0);
    module.dispose();
  });

  it('a late stale facts response never overwrites the newest query results', async () => {
    let releaseOld: ((value: Response) => void) | null = null;
    const scopeEcho = {
      session_id: SCOPE_A.sessionId,
      worldline: SCOPE_A.worldline,
      identity_mode: SCOPE_A.identityMode,
    };
    const fetcher = vi.fn((input: string) => {
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = (scripted?.body ?? {}) as Record<string, unknown>;
      if (input.startsWith('/api/memory/facts?') && !input.includes('query=')) {
        // The pre-query request stays in flight and resolves LAST.
        return new Promise<Response>((resolve) => {
          releaseOld = resolve;
        });
      }
      if (input.startsWith('/api/memory/facts?')) {
        body.facts = [factRow({ fact_id: 'new-f', display_text: '新查询结果' })];
        body.pagination = { limit: 20, offset: 0, total: 1, has_more: false };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'set-query', query: '新' });
    await flush();
    expect(module.snapshot().facts.rows.map((row) => row.factId)).toEqual(['new-f']);
    // The old response arrives after the new one: latest request must win.
    releaseOld?.({
      ok: true,
      status: 200,
      json: async () => ({
        facts: [factRow({ fact_id: 'old-f', display_text: '旧查询结果' })],
        scope: scopeEcho,
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    } as unknown as Response);
    await flush();
    expect(module.snapshot().facts.rows.map((row) => row.factId)).toEqual(['new-f']);
    module.dispose();
  });

  it('a late stale experiences response never overwrites the newest query results', async () => {
    let releaseOld: ((value: Response) => void) | null = null;
    const scopeEcho = {
      session_id: SCOPE_A.sessionId,
      worldline: SCOPE_A.worldline,
      identity_mode: SCOPE_A.identityMode,
    };
    const fetcher = vi.fn((input: string) => {
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = (scripted?.body ?? {}) as Record<string, unknown>;
      if (input.startsWith('/api/memory/experiences?') && !input.includes('query=')) {
        return new Promise<Response>((resolve) => {
          releaseOld = resolve;
        });
      }
      if (input.startsWith('/api/memory/experiences?')) {
        body.experiences = [experienceRow({ experience_id: 'new-e', display_text: '新经历结果' })];
        body.pagination = { limit: 20, offset: 0, total: 1, has_more: false };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'set-query', query: '新' });
    await flush();
    expect(module.snapshot().experiences.rows.map((row) => row.experienceId)).toEqual(['new-e']);
    releaseOld?.({
      ok: true,
      status: 200,
      json: async () => ({
        experiences: [experienceRow({ experience_id: 'old-e', display_text: '旧经历结果' })],
        scope: scopeEcho,
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    } as unknown as Response);
    await flush();
    expect(module.snapshot().experiences.rows.map((row) => row.experienceId)).toEqual(['new-e']);
    module.dispose();
  });

  it('details A→B→A: the earliest superseded response never overwrites the newest', async () => {
    let releaseFirst: ((value: Response) => void) | null = null;
    let detailsRequests = 0;
    const scopeEcho = {
      session_id: SCOPE_A.sessionId,
      worldline: SCOPE_A.worldline,
      identity_mode: SCOPE_A.identityMode,
    };
    const fetcher = vi.fn((input: string) => {
      if (input.includes('/details')) {
        detailsRequests += 1;
        const order = detailsRequests;
        if (order === 1) {
          // The FIRST fact-details request is held and resolves LAST.
          return new Promise<Response>((resolve) => {
            releaseFirst = resolve;
          });
        }
        if (order === 3) {
          // The second fact-details request carries the newest truth.
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              scope: scopeEcho,
              fact: {
                fact_id: 'f1',
                topic_id: null,
                is_pinned: false,
                active_version: 1,
                display_text: '最新A文本',
              },
              versions: [],
              provenance: [],
            }),
          } as unknown as Response);
        }
        // order === 2: the intermediate experience details.
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            scope: scopeEcho,
            experience: {
              experience_id: 'e1',
              conversation_id: 'c1',
              display_text: '深夜实验',
              status: 'active',
              expires_at: null,
              created_at: '2026-08-01T00:00:00Z',
              is_expired: false,
            },
            observation_id: 'o-exp',
            provenance: [],
          }),
        } as unknown as Response);
      }
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    module.dispatch({ type: 'select', kind: 'experience', recordId: 'e1' });
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    expect(module.snapshot().details.status).toBe('ready');
    if (module.snapshot().details.payload && 'fact' in module.snapshot().details.payload!) {
      expect(module.snapshot().details.payload.fact.display_text).toBe('最新A文本');
    }
    // The FIRST (superseded) fact response arrives last: it must be dropped.
    releaseFirst?.({
      ok: true,
      status: 200,
      json: async () => ({
        scope: scopeEcho,
        fact: {
          fact_id: 'f1',
          topic_id: null,
          is_pinned: false,
          active_version: 1,
          display_text: '陈旧A文本',
        },
        versions: [],
        provenance: [],
      }),
    } as unknown as Response);
    await flush();
    const payload = module.snapshot().details.payload;
    expect(payload && 'fact' in payload ? payload.fact.display_text : null).toBe('最新A文本');
    module.dispose();
  });

  it('overlapping pending loads: an older observations/status pair never overrides the newest', async () => {
    let releaseSecond: ((value: Response) => void) | null = null;
    let observationsRequests = 0;
    const scopeEcho = {
      session_id: SCOPE_A.sessionId,
      worldline: SCOPE_A.worldline,
      identity_mode: SCOPE_A.identityMode,
    };
    const candidate = (id: string) => ({
      observation_id: id,
      display_text: `候选${id}`,
      evidence_kind: 'direct_user',
      memory_class: 'stable_candidate',
      status: 'candidate',
      reanalysis_count: 0,
      expires_at: null,
      created_at: '2026-08-01T00:00:00Z',
    });
    const fetcher = vi.fn((input: string) => {
      if (input.startsWith('/api/memory/observations?')) {
        observationsRequests += 1;
        const order = observationsRequests;
        if (order === 1) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              scope: scopeEcho,
              observations: [candidate('o1'), candidate('o2')],
              pagination: { limit: 20, offset: 0, total: 2, has_more: false },
            }),
          } as unknown as Response);
        }
        if (order === 2) {
          // The POST-o1 follow-up load is held and resolves LAST.
          return new Promise<Response>((resolve) => {
            releaseSecond = resolve;
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            scope: scopeEcho,
            observations: [],
            pagination: { limit: 20, offset: 0, total: 0, has_more: false },
          }),
        } as unknown as Response);
      }
      if (input.includes('/ignore')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ observation_id: 'x', status: 'ignored' }),
        } as unknown as Response);
      }
      if (input.startsWith('/api/memory/status?')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...scopeEcho,
            runtime_mode: 'v11', pending_count: 0,
            processing_count: 0,
            failed_count: 0,
            failed_jobs: [],
          }),
        } as unknown as Response);
      }
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    expect(module.snapshot().pending.observations.map((item) => item.observationId)).toEqual([
      'o1',
      'o2',
    ]);
    // Ignore o1: its follow-up pending load (#2) hangs; then ignore o2 whose
    // follow-up load (#3) resolves immediately with an empty list.
    module.dispatch({ type: 'ignore-observation', observationId: 'o1' });
    await flush();
    module.dispatch({ type: 'ignore-observation', observationId: 'o2' });
    await flush();
    expect(module.snapshot().pending.observations).toEqual([]);
    // The held #2 pair arrives LAST with a stale o2 row: it must be dropped.
    releaseSecond?.({
      ok: true,
      status: 200,
      json: async () => ({
        scope: scopeEcho,
        observations: [candidate('o2')],
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    } as unknown as Response);
    await flush();
    expect(module.snapshot().pending.observations).toEqual([]);
    module.dispose();
  });

  it('retry announces honestly: unchanged failed is never a success', async () => {
    const scopeEcho = {
      session_id: SCOPE_A.sessionId,
      worldline: SCOPE_A.worldline,
      identity_mode: SCOPE_A.identityMode,
    };
    const fetcher = vi.fn((input: string) => {
      if (input.includes('/jobs/') && input.includes('/retry')) {
        // Backend authority: the job is NOT retryable — unchanged failed.
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            job_id: 'j1',
            state: 'failed',
            session_id: SCOPE_A.sessionId,
            worldline: SCOPE_A.worldline,
            identity_mode: SCOPE_A.identityMode,
            attempt_count: 3,
            last_error_code: 'validation_failed',
          }),
        } as unknown as Response);
      }
      if (input.startsWith('/api/memory/status?')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...scopeEcho,
            runtime_mode: 'v11', pending_count: 0,
            processing_count: 0,
            failed_count: 1,
            failed_jobs: [
              {
                job_id: 'j1',
                last_error_code: 'validation_failed',
                attempt_count: 3,
                updated_at: '2026-08-02T00:00:00+00:00',
                retryable: true,
              },
            ],
          }),
        } as unknown as Response);
      }
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'retry-job', jobId: 'j1' });
    await flush();
    // The returned state is still 'failed' — announcing a re-queue would be
    // a false success.
    expect(module.snapshot().announcement).toBe('任务不可重试，状态未变');
    module.dispose();
  });

  it('retry announces a real re-queue only when the backend returns pending', async () => {
    const scopeEcho = {
      session_id: SCOPE_A.sessionId,
      worldline: SCOPE_A.worldline,
      identity_mode: SCOPE_A.identityMode,
    };
    const fetcher = vi.fn((input: string) => {
      if (input.includes('/jobs/') && input.includes('/retry')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            job_id: 'j1',
            state: 'pending',
            session_id: SCOPE_A.sessionId,
            worldline: SCOPE_A.worldline,
            identity_mode: SCOPE_A.identityMode,
            attempt_count: 3,
            last_error_code: null,
          }),
        } as unknown as Response);
      }
      if (input.startsWith('/api/memory/status?')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ...scopeEcho,
            runtime_mode: 'v11', pending_count: 0,
            processing_count: 0,
            failed_count: 1,
            failed_jobs: [
              {
                job_id: 'j1',
                last_error_code: 'provider_unavailable',
                attempt_count: 3,
                updated_at: '2026-08-02T00:00:00+00:00',
                retryable: true,
              },
            ],
          }),
        } as unknown as Response);
      }
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'retry-job', jobId: 'j1' });
    await flush();
    expect(module.snapshot().announcement).toBe('已重新排队处理');
    module.dispose();
  });

  it('presentation mutations serialize: only one in-flight request is ever sent', async () => {
    let releasePatch: ((value: Response) => void) | null = null;
    let patchCount = 0;
    const fetcher = vi.fn((input: string) => {
      if (input.includes('/presentation')) {
        patchCount += 1;
        if (patchCount === 1) {
          return new Promise<Response>((resolve) => {
            releasePatch = resolve;
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ fact_id: 'f1', is_pinned: true, topic_id: null }),
        } as unknown as Response);
      }
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    // One pin request goes out and hangs; a double-click and a topic change
    // during the flight must NOT add a second/third request.
    module.dispatch({ type: 'toggle-pin', kind: 'fact', recordId: 'f1' });
    module.dispatch({ type: 'toggle-pin', kind: 'fact', recordId: 'f1' });
    module.dispatch({ type: 'assign-topic', topicId: 't1' });
    await flush();
    expect(patchCount).toBe(1);
    // The request completes: announcement + refresh reflect the real outcome.
    releasePatch?.({
      ok: true,
      status: 200,
      json: async () => ({ fact_id: 'f1', is_pinned: true, topic_id: null }),
    } as unknown as Response);
    await flush();
    await flush();
    expect(module.snapshot().announcement).toBe('已置顶');
    // Only after completion is the next presentation mutation accepted.
    module.dispatch({ type: 'toggle-pin', kind: 'fact', recordId: 'f1' });
    await flush();
    expect(patchCount).toBe(2);
    module.dispose();
  });

  it('selecting a fact reuses the P1R-3 details seam', async () => {
    const { module, urls } = await openReady();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    const state = module.snapshot();
    expect(state.details.status).toBe('ready');
    expect(urls.some((url) => url.startsWith('/api/memory/facts/f1/details'))).toBe(true);
    module.dispose();
  });
});

describe('archive deep module: fact correction with optimistic locking', () => {
  it('saves the draft once with the displayed expected_version and refreshes row + details', async () => {
    let patched: { url: string; body: Record<string, unknown> } | null = null;
    let version = 1;
    const responder: Responder = (url, init) => {
      if (url.startsWith('/api/memory/facts/f1?') || url === '/api/memory/facts/f1') {
        const body = JSON.parse((init as RequestInit).body as string) as Record<string, unknown>;
        patched = { url, body };
        if (body.expected_version !== version) {
          return { status: 409, body: { detail: { code: 'expected_version_mismatch', message: '' } } };
        }
        version += 1;
        return {
          status: 200,
          body: { fact_id: 'f1', version_no: version, active_version: version, change_kind: 'user_edit' },
        };
      }
      const base = defaultResponder(SCOPE_A)(url, init);
      if (base && url.startsWith('/api/memory/facts/f1/details')) {
        const payload = base.body as { fact: { display_text: string; active_version: number } };
        payload.fact.display_text = version > 1 ? '修正后的咖啡' : '喜欢黑咖啡';
        payload.fact.active_version = version;
      }
      if (base && url.startsWith('/api/memory/facts?')) {
        const payload = base.body as { facts: Array<Record<string, unknown>> };
        if (version > 1) {
          payload.facts[0].display_text = '修正后的咖啡';
          payload.facts[0].active_version = version;
        }
      }
      return base;
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '修正后的咖啡' });
    const detailsBefore = fetcher.mock.calls.filter(([url]) => String(url).includes('/details')).length;
    module.dispatch({ type: 'save-edit' });
    await flush();
    expect(patched?.body).toEqual({ display_text: '修正后的咖啡', expected_version: 1 });
    const state = module.snapshot();
    expect(state.editor).toBeNull();
    expect(state.facts.rows[0].displayText).toBe('修正后的咖啡');
    // Post-write refresh: details history reloaded through the P1R-3 seam.
    const detailsAfter = fetcher.mock.calls.filter(([url]) => String(url).includes('/details')).length;
    expect(detailsAfter).toBe(detailsBefore + 1);
    module.dispose();
  });

  it('stale correction yields conflict: draft retained, fresh backend truth shown, zero auto retry', async () => {
    const responder: Responder = (url, init) => {
      if (url.startsWith('/api/memory/facts/f1?') || url === '/api/memory/facts/f1') {
        return { status: 409, body: { detail: { code: 'expected_version_mismatch', message: '' } } };
      }
      const base = defaultResponder(SCOPE_A)(url, init);
      if (base && url.startsWith('/api/memory/facts/f1/details')) {
        const payload = base.body as { fact: { display_text: string; active_version: number } };
        payload.fact.display_text = '他人已修订的文本';
        payload.fact.active_version = 2;
      }
      return base;
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '我的草稿' });
    const mutationCallsBefore = fetcher.mock.calls.filter(
      ([url, init]) => String(url).startsWith('/api/memory/facts/f1') && (init as RequestInit)?.method === 'PATCH',
    ).length;
    module.dispatch({ type: 'save-edit' });
    await flush();
    await flush();
    const state = module.snapshot();
    expect(state.editor?.status).toBe('conflict');
    expect(state.editor?.draft).toBe('我的草稿');
    expect(state.editor?.conflict).toEqual({ serverText: '他人已修订的文本', serverVersion: 2 });
    // Zero automatic overwrite / retry: exactly one PATCH was ever sent.
    const mutationCallsAfter = fetcher.mock.calls.filter(
      ([url, init]) => String(url).startsWith('/api/memory/facts/f1') && (init as RequestInit)?.method === 'PATCH',
    ).length;
    expect(mutationCallsAfter).toBe(mutationCallsBefore + 1);
    module.dispose();
  });

  it('presentation changes never alter text history', async () => {
    let patchedBodies: Array<Record<string, unknown>> = [];
    const responder: Responder = (url, init) => {
      if (url.includes('/api/memory/facts/f1/presentation')) {
        patchedBodies.push(JSON.parse((init as RequestInit).body as string));
        return { status: 200, body: { fact_id: 'f1', is_pinned: true, topic_id: null } };
      }
      return defaultResponder(SCOPE_A)(url, init);
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'toggle-pin', kind: 'fact', recordId: 'f1' });
    await flush();
    expect(patchedBodies).toEqual([{ is_pinned: true }]);
    // No text-version mutation seam was touched.
    expect(
      fetcher.mock.calls.some(([url]) => String(url).match(/^\/api\/memory\/facts\/f1\?/) && (String(url).includes('PATCH'))),
    ).toBe(false);
    module.dispose();
  });
});

describe('archive deep module: deletion closure', () => {
  it('delete purges rows and total synchronously; a failed refresh never resurrects', async () => {
    let deleted = false;
    const responder: Responder = (url, init) => {
      if (String(url).startsWith('/api/memory/facts/f1?') && (init as RequestInit)?.method === 'DELETE') {
        deleted = true;
        return { status: 200, body: { fact_id: 'f1', deleted: true } };
      }
      const base = defaultResponder(SCOPE_A)(url, init);
      if (base && url.startsWith('/api/memory/facts?') && deleted) {
        // Post-delete refresh FAILS: the removed row must stay removed.
        return { status: 500, body: { detail: { code: 'memory_operation_failed', message: '' } } };
      }
      return base;
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    expect(module.snapshot().facts.rows).toHaveLength(1);
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'request-delete' });
    module.dispatch({ type: 'confirm-delete' });
    await flush();
    const state = module.snapshot();
    // Immediate closure without waiting for the list refresh: row gone,
    // complete count decremented, focus workflow cleared, no residue.
    expect(state.facts.rows).toHaveLength(0);
    expect(state.facts.total).toBe(0);
    expect(state.selection).toBeNull();
    expect(state.deleteFlow).toBeNull();
    expect(state.editor).toBeNull();
    expect(JSON.stringify(state)).not.toContain('喜欢黑咖啡');
    module.dispose();
  });

  it('a 404 delete keeps honest wording: clears remnants without announcing success', async () => {
    let deleteAttempted = false;
    const responder: Responder = (url, init) => {
      if (String(url).startsWith('/api/memory/facts/f1?') && (init as RequestInit)?.method === 'DELETE') {
        deleteAttempted = true;
        return { status: 404, body: { detail: { code: 'fact_not_found', message: '' } } };
      }
      const base = defaultResponder(SCOPE_A)(url, init);
      if (base && url.startsWith('/api/memory/facts?')) {
        return {
          status: 200,
          body: deleteAttempted
            ? {
                facts: [],
                scope: {
                  session_id: SCOPE_A.sessionId,
                  worldline: SCOPE_A.worldline,
                  identity_mode: SCOPE_A.identityMode,
                },
                pagination: { limit: 20, offset: 0, total: 0, has_more: false },
              }
            : base.body,
        };
      }
      return base;
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'request-delete' });
    module.dispatch({ type: 'confirm-delete' });
    await flush();
    const state = module.snapshot();
    // Stale local remnants may be cleared…
    expect(state.facts.rows).toHaveLength(0);
    expect(state.selection).toBeNull();
    expect(state.deleteFlow).toBeNull();
    // …but the outcome must NOT claim deletion success, and must not
    // disclose whether the record was deleted, missing, or inaccessible.
    expect(state.announcement).not.toContain('删除');
    expect(state.announcement).toBe('该记忆在此范围内不再可用');
    module.dispose();
  });

  it('fact delete requires confirmation, purges readable state, and offers no undo', async () => {
    let deleted = false;
    const responder: Responder = (url, init) => {
      if (String(url).startsWith('/api/memory/facts/f1?') && (init as RequestInit)?.method === 'DELETE') {
        deleted = true;
        return { status: 200, body: { fact_id: 'f1', state: 'deleted', deleted: true } };
      }
      const base = defaultResponder(SCOPE_A)(url, init);
      if (base && url.startsWith('/api/memory/facts?') && deleted) {
        return {
          status: 200,
          body: {
            facts: [],
            scope: { session_id: 's-a', worldline: 'steins_gate', identity_mode: 'okabe' },
            pagination: { limit: 20, offset: 0, total: 0, has_more: false },
          },
        };
      }
      return base;
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();

    module.dispatch({ type: 'request-delete' });
    expect(module.snapshot().deleteFlow?.status).toBe('confirming');
    // Confirmation names kind, visible summary and active scope.
    const flow = module.snapshot().deleteFlow;
    expect(flow?.kind).toBe('fact');
    expect(flow?.summary).toContain('喜欢黑咖啡');
    expect(flow?.scopeLabel).toContain('OKABE');
    expect(flow?.expectedVersion).toBe(1);

    module.dispatch({ type: 'confirm-delete' });
    await flush();
    const state = module.snapshot();
    expect(state.facts.rows).toHaveLength(0);
    expect(state.facts.total).toBe(0);
    expect(state.selection).toBeNull();
    expect(state.details.status).toBe('idle');
    // No readable residue anywhere in the snapshot.
    expect(JSON.stringify(state)).not.toContain('喜欢黑咖啡');
    expect(state.deleteFlow).toBeNull();
    expect(state.announcement).not.toBe('');
    module.dispose();
  });

  it('saving blocks Back / exit / scope switch, and a late failure never revives the draft', async () => {
    let releasePatch: ((value: Response) => void) | null = null;
    const fetcher = vi.fn((input: string, init?: RequestInit) => {
      if (
        (input.startsWith('/api/memory/facts/f1?') || input === '/api/memory/facts/f1')
        && init?.method === 'PATCH'
      ) {
        return new Promise<Response>((resolve) => {
          releasePatch = resolve;
        });
      }
      const scripted = defaultResponder(SCOPE_A)(input, init);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '保存中的草稿' });
    module.dispatch({ type: 'save-edit' });
    expect(module.snapshot().editor?.status).toBe('saving');
    // While saving: Back is consumed WITHOUT raising the discard dialog…
    module.dispatch({ type: 'back' });
    expect(module.snapshot().editor?.status).toBe('saving');
    expect(module.snapshot().discardConfirm).toBeNull();
    expect(module.snapshot().announcement).toContain('正在保存');
    // …the peer-mode exit is refused…
    expect(module.handleExitRequest()).toBe(false);
    expect(module.snapshot().editor?.status).toBe('saving');
    // …and a scope switch is refused as well.
    expect(module.open(SCOPE_B, { restore: true })).toBe(false);
    expect(module.snapshot().scope).toEqual(SCOPE_A);
    // The save FAILS: the failure path restores the editable draft, which
    // the user may then explicitly discard.
    releasePatch?.({
      ok: false,
      status: 500,
      json: async () => ({ detail: { code: 'memory_operation_failed', message: '' } }),
    } as unknown as Response);
    await flush();
    expect(module.snapshot().editor?.status).toBe('editing');
    module.dispatch({ type: 'discard-draft' });
    expect(module.snapshot().editor).toBeNull();
    module.dispose();
  });

  it('a late conflict after an explicit discard never resurrects the editor', async () => {
    let releasePatch: ((value: Response) => void) | null = null;
    const fetcher = vi.fn((input: string, init?: RequestInit) => {
      if (
        (input.startsWith('/api/memory/facts/f1?') || input === '/api/memory/facts/f1')
        && init?.method === 'PATCH'
      ) {
        return new Promise<Response>((resolve) => {
          releasePatch = resolve;
        });
      }
      const scripted = defaultResponder(SCOPE_A)(input, init);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '将被放弃的草稿' });
    module.dispatch({ type: 'save-edit' });
    expect(module.snapshot().editor?.status).toBe('saving');
    // The 409 arrives while saving → the conflict state returns, giving the
    // user the fresh backend truth and the explicit discard choice.
    releasePatch?.({
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: 'expected_version_mismatch', message: '' } }),
    } as unknown as Response);
    await flush();
    await flush();
    expect(module.snapshot().editor?.status).toBe('conflict');
    module.dispatch({ type: 'discard-draft' });
    expect(module.snapshot().editor).toBeNull();
    // A superseded/late completion must never write the editor back.
    await flush();
    expect(module.snapshot().editor).toBeNull();
    expect(JSON.stringify(module.snapshot())).not.toContain('将被放弃的草稿');
    module.dispose();
  });

  it('only one explicit mutation workflow may be pending at a time', async () => {
    const { module } = await openReady();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '未保存草稿' });
    module.dispatch({ type: 'request-delete' });
    const state = module.snapshot();
    // The editor owns the pending slot; the delete workflow must not open.
    expect(state.deleteFlow).toBeNull();
    expect(state.editor?.draft).toBe('未保存草稿');
    module.dispose();
  });

  it('experience has no text edit path; pin and delete use explicit authorities', async () => {
    const { module } = await openReady();
    module.dispatch({ type: 'select', kind: 'experience', recordId: 'e1' });
    await flush();
    // open-editor is fact-only: selecting an experience never opens a draft.
    module.dispatch({ type: 'open-editor' });
    expect(module.snapshot().editor).toBeNull();
    module.dispatch({ type: 'request-delete' });
    const flow = module.snapshot().deleteFlow;
    expect(flow?.kind).toBe('experience');
    expect(flow?.expectedVersion).toBeUndefined();
    module.dispose();
  });
});

describe('archive deep module: semantic back, restore and deep links', () => {
  it('back demands an explicit discard choice before closing the editor', async () => {
    const { module } = await openReady();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '未保存草稿' });
    module.dispatch({ type: 'back' });
    // §4.14: the draft is never silently cleared by Back.
    expect(module.snapshot().editor?.draft).toBe('未保存草稿');
    expect(module.snapshot().discardConfirm?.action).toBe('back');
    // Continue editing keeps the draft and closes the confirmation.
    module.dispatch({ type: 'cancel-discard' });
    expect(module.snapshot().editor?.draft).toBe('未保存草稿');
    expect(module.snapshot().discardConfirm).toBeNull();
    // Explicit discard executes the original action (Back).
    module.dispatch({ type: 'back' });
    module.dispatch({ type: 'confirm-discard' });
    expect(module.snapshot().editor).toBeNull();
    expect(module.snapshot().discardConfirm).toBeNull();
    expect(module.snapshot().details.status).toBe('ready');
    module.dispatch({ type: 'back' });
    expect(module.snapshot().selection).toBeNull();
    module.dispose();
  });

  it('mode-switch exit with a draft demands explicit discard, then executes the exit', async () => {
    const onExitArchive = vi.fn();
    const { fetcher } = scriptedFetcher(defaultResponder(SCOPE_A));
    const module = createArchiveModule({ fetcher, onExitArchive });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '未保存草稿' });
    // The peer shell asks Archive first: with a draft it must NOT switch.
    expect(module.handleExitRequest()).toBe(false);
    expect(module.snapshot().discardConfirm?.action).toBe('exit-archive');
    expect(module.snapshot().editor?.draft).toBe('未保存草稿');
    expect(onExitArchive).not.toHaveBeenCalled();
    // Continue editing: no exit, draft intact.
    module.dispatch({ type: 'cancel-discard' });
    expect(onExitArchive).not.toHaveBeenCalled();
    expect(module.snapshot().editor?.draft).toBe('未保存草稿');
    // Explicit discard executes the original action (the mode switch).
    module.handleExitRequest();
    module.dispatch({ type: 'confirm-discard' });
    expect(module.snapshot().editor).toBeNull();
    expect(onExitArchive).toHaveBeenCalledTimes(1);
    module.dispose();
  });

  it('scope switch with a draft is deferred until an explicit discard', async () => {
    const onScopeChangeSettled = vi.fn();
    const fetcher = vi.fn(async (input: string) => {
      const scope = input.includes('worldline=steins_gate') ? SCOPE_A : SCOPE_B;
      const body = defaultResponder(scope)(input)?.body ?? {};
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    });
    const module = createArchiveModule({ fetcher, onScopeChangeSettled });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '范围草稿' });
    // The deferred open refuses to reset the state silently.
    expect(module.open(SCOPE_B, { restore: true })).toBe(false);
    expect(module.snapshot().scope).toEqual(SCOPE_A);
    expect(module.snapshot().editor?.draft).toBe('范围草稿');
    expect(module.snapshot().discardConfirm?.action).toBe('scope-switch');
    // Continue editing: still on the old scope, draft intact.
    module.dispatch({ type: 'cancel-discard' });
    expect(module.snapshot().scope).toEqual(SCOPE_A);
    expect(module.snapshot().editor?.draft).toBe('范围草稿');
    // Explicit discard executes the deferred scope switch.
    module.open(SCOPE_B, { restore: true });
    module.dispatch({ type: 'confirm-discard' });
    await flush();
    expect(module.snapshot().scope).toEqual(SCOPE_B);
    expect(module.snapshot().editor).toBeNull();
    expect(onScopeChangeSettled).toHaveBeenCalledTimes(1);
    module.dispose();
  });

  it('committed state restores per scope; uncommitted drafts never do', async () => {
    const fetcher = vi.fn(async (input: string) => {
      const scope = input.includes('worldline=steins_gate') ? SCOPE_A : SCOPE_B;
      const body = defaultResponder(scope)(input)?.body ?? {};
      return { ok: true, status: 200, json: async () => body } as unknown as Response;
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    module.dispatch({ type: 'set-query', query: '咖啡' });
    module.dispatch({ type: 'select', kind: 'fact', recordId: 'f1' });
    await flush();
    module.dispatch({ type: 'open-editor' });
    module.dispatch({ type: 'edit-input', text: '未保存' });
    module.open(SCOPE_B);
    await flush();
    module.open(SCOPE_A, { restore: true });
    await flush();
    const state = module.snapshot();
    expect(state.filters.query).toBe('咖啡');
    expect(state.editor).toBeNull();
    module.dispose();
  });

  it('a true unmount still discards late responses after dispose', async () => {
    let release: ((value: Response) => void) | null = null;
    const fetcher = vi.fn((input: string) => {
      if (input.startsWith('/api/memory/facts?')) {
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      const scripted = defaultResponder(SCOPE_A)(input);
      const body = scripted?.body ?? {};
      return Promise.resolve({ ok: true, status: 200, json: async () => body } as unknown as Response);
    });
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A);
    await flush();
    // Real unmount: the module is disposed WITHOUT a subsequent open().
    module.dispose();
    release?.({
      ok: true,
      status: 200,
      json: async () => ({
        facts: [factRow({ fact_id: 'late-f', display_text: '迟到响应行' })],
        scope: {
          session_id: SCOPE_A.sessionId,
          worldline: SCOPE_A.worldline,
          identity_mode: SCOPE_A.identityMode,
        },
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    } as unknown as Response);
    await flush();
    // The late response must never be committed into the disposed module.
    const state = module.snapshot();
    expect(state.facts.rows).toHaveLength(0);
    expect(state.facts.total).toBe(0);
  });

  it('focus-record deep link falls back honestly when the target is unavailable', async () => {
    const responder: Responder = (url) => {
      if (url.startsWith('/api/memory/facts/missing/details')) {
        return { status: 404, body: { detail: { code: 'fact_not_found', message: '' } } };
      }
      return defaultResponder(SCOPE_A)(url);
    };
    const { fetcher } = scriptedFetcher(responder);
    const module = createArchiveModule({ fetcher });
    module.open(SCOPE_A, { focus: { kind: 'fact', recordId: 'missing' } });
    await flush();
    const state = module.snapshot();
    expect(state.selection).toBeNull();
    expect(state.focusFallback).toBeTruthy();
    // The selection pane fully closes so a narrow screen returns to the
    // scoped list instead of lingering on an empty detail surface.
    expect(state.details.status).toBe('idle');
    expect(state.details.payload).toBeNull();
    // Never rebuilt from projection text: no projection request was issued.
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('graph/projection'))).toBe(false);
    module.dispose();
  });
});
