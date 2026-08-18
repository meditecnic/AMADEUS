import { describe, expect, it } from 'vitest';

import {
  buildProjectionUrl,
  criteriaChips,
  EMPTY_CRITERIA,
  hasActiveCriteria,
  normalizeCriteria,
} from './criteria';

describe('projection criteria', () => {
  it('forwards every P1 criterion on the canonical overview URL', () => {
    const url = buildProjectionUrl(
      { sessionId: 's1', worldline: 'beta', identityMode: 'okabe' },
      {
        query: '咖啡',
        kinds: ['fact', 'topic'],
        topicId: 't9',
        pinnedOnly: true,
        updatedFrom: '2026-01-01T00:00:00Z',
        updatedTo: '2026-08-01T00:00:00Z',
      },
    );
    const parsed = new URL(url, 'http://local.test');
    expect(parsed.pathname).toBe('/api/memory/graph/projection');
    expect(parsed.searchParams.get('session_id')).toBe('s1');
    expect(parsed.searchParams.get('worldline')).toBe('beta');
    expect(parsed.searchParams.get('identity_mode')).toBe('okabe');
    expect(parsed.searchParams.get('view')).toBe('overview');
    expect(parsed.searchParams.get('query')).toBe('咖啡');
    expect(parsed.searchParams.getAll('kind')).toEqual(['fact', 'topic']);
    expect(parsed.searchParams.get('topic_id')).toBe('t9');
    expect(parsed.searchParams.get('pinned_only')).toBe('true');
    expect(parsed.searchParams.get('updated_from')).toBe('2026-01-01T00:00:00Z');
    expect(parsed.searchParams.get('updated_to')).toBe('2026-08-01T00:00:00Z');
  });

  it('omits inactive criteria so the client does not invent filters', () => {
    const url = buildProjectionUrl(
      { sessionId: 's1', worldline: 'steins_gate', identityMode: 'self' },
      EMPTY_CRITERIA,
    );
    const parsed = new URL(url, 'http://local.test');
    expect(parsed.searchParams.get('query')).toBeNull();
    expect(parsed.searchParams.getAll('kind')).toEqual([]);
    expect(parsed.searchParams.get('topic_id')).toBeNull();
    expect(parsed.searchParams.get('pinned_only')).toBeNull();
    expect(hasActiveCriteria(EMPTY_CRITERIA)).toBe(false);
    expect(hasActiveCriteria(normalizeCriteria({ query: '  拿铁  ' }))).toBe(true);
    expect(criteriaChips(normalizeCriteria({ query: '拿铁', pinnedOnly: true }))).toHaveLength(2);
  });
});
