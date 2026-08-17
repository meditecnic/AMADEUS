import { describe, expect, it } from 'vitest';

import { readingCardFromNode } from './readingContent';

describe('readingCardFromNode', () => {
  it('builds G47 fields from a fact node', () => {
    const card = readingCardFromNode({
      kind: 'fact',
      projection_id: 'fact:1',
      fact_id: '1',
      label: '喜欢黑咖啡',
      is_pinned: false,
      updated_at: '2026-08-14T00:00:00Z',
      topic_id: 't1',
    });
    expect(card).toEqual({
      title: '喜欢黑咖啡',
      kind: '事实',
      kindKey: 'fact',
      summary: '喜欢黑咖啡',
      provenance: {
        pinned: false,
        updatedAt: '2026-08-14T00:00:00Z',
        topicId: 't1',
        conversationId: null,
      },
    });
  });
});
