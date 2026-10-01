import { describe, expect, it, vi } from 'vitest';

import {
  buildExperiencesUrl,
  buildFactsUrl,
  correctFactText,
  deleteExperience,
  deleteFact,
  fetchExperiencesPage,
  fetchFactsPage,
  fetchMemoryStatus,
  fetchObservations,
  fetchTopics,
  retryFailedJob,
  updateExperiencePresentation,
  type ArchiveScope,
} from './archiveApi';

const scope: ArchiveScope = {
  sessionId: 's1',
  worldline: 'steins_gate',
  identityMode: 'okabe',
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('archiveApi URL building carries the complete exact scope', () => {
  it('facts browse URL contains scope, filters and pagination', () => {
    const url = buildFactsUrl(scope, {
      query: '咖啡',
      topicId: 't-1',
      pinnedOnly: true,
      limit: 20,
      offset: 40,
    });
    expect(url).toContain('/api/memory/facts?');
    expect(url).toContain('session_id=s1');
    expect(url).toContain('worldline=steins_gate');
    expect(url).toContain('identity_mode=okabe');
    expect(url).toContain(`query=${encodeURIComponent('咖啡')}`);
    expect(url).toContain('topic_id=t-1');
    expect(url).toContain('pinned_only=true');
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=40');
    // Archive never reaches for the bounded projection to build rows.
    expect(url).not.toContain('graph/projection');
  });

  it('experiences browse URL contains query and pinned_only filters', () => {
    const url = buildExperiencesUrl(scope, {
      query: '目标',
      pinnedOnly: true,
      limit: 10,
      offset: 0,
    });
    expect(url).toContain('/api/memory/experiences?');
    expect(url).toContain('session_id=s1');
    expect(url).toContain('worldline=steins_gate');
    expect(url).toContain('identity_mode=okabe');
    expect(url).toContain(`query=${encodeURIComponent('目标')}`);
    expect(url).toContain('pinned_only=true');
  });
});

describe('archiveApi honest outcome mapping', () => {
  it('facts page success returns typed rows without synthesizing fields', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(200, {
        facts: [
          {
            fact_id: 'f1',
            display_text: '喜欢黑咖啡',
            version_no: 2,
            active_version: 2,
            is_pinned: true,
            topic_id: null,
            confidence: 0.91,
            updated_at: '2026-08-20T00:00:00Z',
          },
        ],
        scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    );
    const result = await fetchFactsPage(scope, { limit: 20, offset: 0 }, { fetcher });
    expect(result.type).toBe('ok');
    if (result.type !== 'ok') return;
    expect(result.payload.rows).toHaveLength(1);
    expect(result.payload.rows[0]).toMatchObject({
      factId: 'f1',
      displayText: '喜欢黑咖啡',
      activeVersion: 2,
      isPinned: true,
      topicId: null,
      updatedAt: '2026-08-20T00:00:00Z',
    });
    // Confidence is not a user-facing truth scale: it must not reach the
    // Archive row model at all.
    expect(Object.keys(result.payload.rows[0])).not.toContain('confidence');
    expect(result.payload.total).toBe(1);
    expect(result.payload.hasMore).toBe(false);
  });

  it('experiences page rows expose is_pinned and expiry text state', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(200, {
        experiences: [
          {
            experience_id: 'e1',
            conversation_id: 'c1',
            display_text: '深夜实验',
            confidence: 0.8,
            status: 'active',
            expires_at: '2026-01-01T00:00:00Z',
            created_at: '2025-12-31T00:00:00Z',
            is_expired: true,
            is_pinned: false,
          },
        ],
        scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    );
    const result = await fetchExperiencesPage(scope, { limit: 20, offset: 0 }, { fetcher });
    expect(result.type).toBe('ok');
    if (result.type !== 'ok') return;
    expect(result.payload.rows[0]).toMatchObject({
      experienceId: 'e1',
      displayText: '深夜实验',
      isPinned: false,
      isExpired: true,
    });
    expect(Object.keys(result.payload.rows[0])).not.toContain('confidence');
  });

  it('facts browse fails closed when the response scope mismatches the request', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(200, {
        facts: [
          {
            fact_id: 'f1',
            display_text: '越界事实',
            version_no: 1,
            active_version: 1,
            is_pinned: false,
            topic_id: null,
            updated_at: '2026-08-20T00:00:00Z',
          },
        ],
        scope: { session_id: 's1', worldline: 'beta', identity_mode: 'okabe' },
        pagination: { limit: 20, offset: 0, total: 1, has_more: false },
      }),
    );
    const result = await fetchFactsPage(scope, { limit: 20, offset: 0 }, { fetcher });
    expect(result).toEqual({
      type: 'backend-failure',
      code: 'invalid_payload',
      status: 200,
    });
  });

  it('facts browse fails closed when the response scope is missing or malformed', async () => {
    const noScope = await fetchFactsPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          facts: [],
          pagination: { limit: 20, offset: 0, total: 0, has_more: false },
        }),
      ),
    });
    expect(noScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const malformed = await fetchFactsPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          facts: [],
          scope: 'steins_gate',
          pagination: { limit: 20, offset: 0, total: 0, has_more: false },
        }),
      ),
    });
    expect(malformed).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('experiences browse fails closed on wrong or missing scope', async () => {
    const wrongScope = await fetchExperiencesPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          experiences: [
            {
              experience_id: 'e1',
              conversation_id: 'c1',
              display_text: '越界经历',
              status: 'active',
              expires_at: null,
              created_at: '2025-12-31T00:00:00Z',
              is_expired: false,
              is_pinned: false,
            },
          ],
          scope: { session_id: 'other', worldline: 'steins_gate', identity_mode: 'okabe' },
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        }),
      ),
    });
    expect(wrongScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const missingScope = await fetchExperiencesPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          experiences: [],
          pagination: { limit: 20, offset: 0, total: 0, has_more: false },
        }),
      ),
    });
    expect(missingScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('a fact row without updated_at fails closed instead of faking an empty time', async () => {
    const result = await fetchFactsPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          facts: [
            {
              fact_id: 'f1',
              display_text: '缺时间的行',
              version_no: 1,
              active_version: 1,
              is_pinned: false,
              topic_id: null,
              // updated_at deliberately missing
            },
          ],
          scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        }),
      ),
    });
    expect(result).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('an experience row without a boolean is_pinned fails closed', async () => {
    const missing = await fetchExperiencesPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          experiences: [
            {
              experience_id: 'e1',
              conversation_id: 'c1',
              display_text: '缺置顶状态',
              status: 'active',
              expires_at: null,
              created_at: '2025-12-31T00:00:00Z',
              is_expired: false,
              // is_pinned deliberately missing
            },
          ],
          scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        }),
      ),
    });
    expect(missing).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const wrongType = await fetchExperiencesPage(scope, { limit: 20, offset: 0 }, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          experiences: [
            {
              experience_id: 'e2',
              conversation_id: 'c1',
              display_text: '置顶状态类型错误',
              status: 'active',
              expires_at: null,
              created_at: '2025-12-31T00:00:00Z',
              is_expired: false,
              is_pinned: 'yes',
            },
          ],
          scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        }),
      ),
    });
    expect(wrongType).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('status fails closed when failed_jobs or the count fields are missing', async () => {
    const base = {
      session_id: 's1',
      worldline: 'steins_gate',
      identity_mode: 'okabe',
      pending_count: 0,
      processing_count: 0,
      failed_count: 0,
      failed_jobs: [],
    };
    const noJobs = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, { ...base, failed_jobs: undefined }),
      ),
    });
    expect(noJobs).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const noPending = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () => {
        const { pending_count: _omitted, ...rest } = base;
        return jsonResponse(200, rest);
      }),
    });
    expect(noPending).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const wrongTypeCount = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, { ...base, failed_count: '3' }),
      ),
    });
    expect(wrongTypeCount).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('status fails closed when a failed-job row is missing, extra, or mistyped', async () => {
    const validRow = {
      job_id: 'j1',
      last_error_code: 'provider_unavailable',
      attempt_count: 3,
      updated_at: '2026-08-02T00:00:00+00:00',
      retryable: true,
    };
    const base = {
      session_id: 's1',
      worldline: 'steins_gate',
      identity_mode: 'okabe',
      pending_count: 0,
      processing_count: 0,
      failed_count: 1,
    };
    const missingField = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          ...base,
          failed_jobs: [{ ...validRow, updated_at: undefined }],
        }),
      ),
    });
    expect(missingField).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const extraField = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          ...base,
          failed_jobs: [{ ...validRow, state: 'failed' }],
        }),
      ),
    });
    expect(extraField).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const mistypedField = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          ...base,
          failed_jobs: [{ ...validRow, attempt_count: '3' }],
        }),
      ),
    });
    expect(mistypedField).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('topics rows read the real backend field fact_count', async () => {
    const result = await fetchTopics(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
          topics: [
            { topic_id: 't1', display_label: '咖啡', fact_count: 2 },
            { topic_id: 't2', display_label: '研究', fact_count: 0 },
          ],
        }),
      ),
    });
    expect(result.type).toBe('ok');
    if (result.type !== 'ok') return;
    expect(result.payload).toEqual([
      { topicId: 't1', displayLabel: '咖啡', activeFactCount: 2 },
      { topicId: 't2', displayLabel: '研究', activeFactCount: 0 },
    ]);
  });

  it('topics browse fails closed when fact_count is missing or mistyped', async () => {
    const missing = await fetchTopics(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
          // fact_count deliberately missing — must never default to 0.
          topics: [{ topic_id: 't1', display_label: '缺计数' }],
        }),
      ),
    });
    expect(missing).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const mistyped = await fetchTopics(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
          topics: [{ topic_id: 't1', display_label: '错型计数', fact_count: '2' }],
        }),
      ),
    });
    expect(mistyped).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('topics browse fails closed on wrong or missing scope echo', async () => {
    const wrongScope = await fetchTopics(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          scope: { session_id: 's1', worldline: 'beta', identity_mode: 'okabe' },
          topics: [{ topic_id: 't1', display_label: '越界主题', fact_count: 2 }],
        }),
      ),
    });
    expect(wrongScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const missingScope = await fetchTopics(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, { topics: [{ topic_id: 't1', display_label: '无回显主题', fact_count: 1 }] }),
      ),
    });
    expect(missingScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('observations browse fails closed on wrong or missing scope echo', async () => {
    const wrongScope = await fetchObservations(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          scope: { session_id: 'elsewhere', worldline: 'steins_gate', identity_mode: 'okabe' },
          observations: [{ observation_id: 'o1', display_text: '越界观察', status: 'candidate' }],
          pagination: { limit: 20, offset: 0, total: 1, has_more: false },
        }),
      ),
    });
    expect(wrongScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const missingScope = await fetchObservations(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          observations: [],
          pagination: { limit: 20, offset: 0, total: 0, has_more: false },
        }),
      ),
    });
    expect(missingScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });
  });

  it('memory status validates its TOP-LEVEL scope fields (real payload shape)', async () => {
    // The status payload echoes scope as top-level fields, not a scope object:
    // the parser must validate the actual shape, never invent another.
    const wrongScope = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          session_id: 's1',
          worldline: 'beta',
          identity_mode: 'okabe',
          pending_count: 7,
          processing_count: 0,
          failed_count: 9,
          failed_jobs: [{ job_id: 'jx', attempt_count: 1, last_error_code: 'x', retryable: true }],
        }),
      ),
    });
    expect(wrongScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    const missingScope = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, { pending_count: 1, processing_count: 0, failed_count: 0, failed_jobs: [] }),
      ),
    });
    expect(missingScope).toEqual({ type: 'backend-failure', code: 'invalid_payload', status: 200 });

    // Matching top-level scope parses through.
    const matching = await fetchMemoryStatus(scope, {
      fetcher: vi.fn(async () =>
        jsonResponse(200, {
          session_id: 's1',
          worldline: 'steins_gate',
          identity_mode: 'okabe',
          pending_count: 2,
          processing_count: 1,
          failed_count: 0,
          failed_jobs: [],
        }),
      ),
    });
    expect(matching.type).toBe('ok');
    if (matching.type === 'ok') {
      expect(matching.payload.pendingCount).toBe(2);
    }
  });

  it('maps 404 / 409 / 422 / 500 / transport into distinct honest outcomes', async () => {
    const notFound = await correctFactText(
      scope,
      'f1',
      { displayText: 'x', expectedVersion: 1 },
      { fetcher: async () => jsonResponse(404, { detail: { code: 'fact_not_found', message: '' } }) },
    );
    expect(notFound).toEqual({ type: 'not-found' });

    const conflict = await correctFactText(
      scope,
      'f1',
      { displayText: 'x', expectedVersion: 1 },
      {
        fetcher: async () =>
          jsonResponse(409, { detail: { code: 'expected_version_mismatch', message: '' } }),
      },
    );
    expect(conflict).toEqual({ type: 'conflict', code: 'expected_version_mismatch' });

    const validation = await correctFactText(
      scope,
      'f1',
      { displayText: 'x', expectedVersion: 1 },
      { fetcher: async () => jsonResponse(422, { detail: { code: 'empty_display_text', message: '' } }) },
    );
    expect(validation).toEqual({ type: 'validation', code: 'empty_display_text' });

    const backend = await deleteFact(scope, 'f1', 2, {
      fetcher: async () => jsonResponse(500, { detail: { code: 'memory_operation_failed', message: '' } }),
    });
    expect(backend).toEqual({ type: 'backend-failure', code: 'memory_operation_failed', status: 500 });

    const transport = await deleteExperience(scope, 'e1', {
      fetcher: async () => {
        throw new Error('network down');
      },
    });
    expect(transport).toEqual({ type: 'transport-error' });
  });

  it('experience presentation mutation sends is_pinned only to the exact-scope route', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(200, {
        experience_id: 'e1',
        is_pinned: true,
        scope: { session_id: 's1', worldline: 'steins_gate', identity_mode: 'okabe' },
      }),
    );
    const result = await updateExperiencePresentation(scope, 'e1', true, { fetcher });
    expect(result.type).toBe('ok');
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toContain('/api/memory/experiences/e1/presentation?');
    expect(url).toContain('session_id=s1');
    expect(url).toContain('identity_mode=okabe');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ is_pinned: true });
    expect((init as RequestInit).method).toBe('PATCH');
  });

  it('job retry POSTs to the bounded retry seam with full scope', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(200, { job_id: 'j1', state: 'pending' }),
    );
    const result = await retryFailedJob(scope, 'j1', { fetcher });
    expect(result.type).toBe('ok');
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toContain('/api/memory/jobs/j1/retry?');
    expect(url).toContain('session_id=s1');
    expect((init as RequestInit).method).toBe('POST');
  });

  it('failed-jobs rows carry only the §16 contract fields and never state', async () => {
    const fetcher = vi.fn(async () =>
      jsonResponse(200, {
        session_id: 's1',
        worldline: 'steins_gate',
        identity_mode: 'okabe',
        pending_count: 0,
        processing_count: 0,
        failed_count: 2,
        failed_jobs: [
          {
            job_id: 'j1',
            attempt_count: 3,
            last_error_code: 'provider_unavailable',
            updated_at: '2026-08-02T00:00:00+00:00',
            retryable: true,
          },
          {
            // Backend may not even send state; desktop must not synthesize it.
            job_id: 'j2',
            attempt_count: 1,
            last_error_code: 'validation_failed',
            updated_at: '2026-08-01T00:00:00+00:00',
            retryable: false,
          },
        ],
      }),
    );
    const result = await fetchMemoryStatus(scope, { fetcher });
    expect(result.type).toBe('ok');
    if (result.type !== 'ok') return;
    expect(result.payload.failedJobs).toHaveLength(2);
    for (const job of result.payload.failedJobs) {
      expect(Object.keys(job).sort()).toEqual(
        ['attemptCount', 'jobId', 'lastErrorCode', 'retryable', 'updatedAt'].sort(),
      );
    }
    expect(result.payload.failedJobs[0]).toMatchObject({
      jobId: 'j1',
      attemptCount: 3,
      updatedAt: '2026-08-02T00:00:00+00:00',
      retryable: true,
    });
    expect(result.payload.failedJobs[1].retryable).toBe(false);
  });
});
