import { describe, expect, it } from 'vitest';

import { nextMemoryUnwind } from './memoryUnwind';

describe('nextMemoryUnwind', () => {
  it('closes LIST before Evidence even when a Fact is selected', () => {
    expect(nextMemoryUnwind({
      toolsOpen: false,
      hasActiveCriteria: false,
      listVisible: true,
      selectedKind: 'fact',
      expanded: true,
      closeAtRoot: false,
    })).toBe('close-list');
  });

  it('maps Fact to Topic then Topic to Overview', () => {
    expect(nextMemoryUnwind({
      toolsOpen: false,
      hasActiveCriteria: false,
      listVisible: false,
      selectedKind: 'fact',
      expanded: true,
      closeAtRoot: false,
    })).toBe('evidence-to-topic');
    expect(nextMemoryUnwind({
      toolsOpen: false,
      hasActiveCriteria: false,
      listVisible: false,
      selectedKind: 'topic',
      expanded: true,
      closeAtRoot: false,
    })).toBe('topic-to-overview');
  });

  it('Back closes Memory at Overview root and RMB does not', () => {
    const root = {
      toolsOpen: false,
      hasActiveCriteria: false,
      listVisible: false,
      selectedKind: null,
      expanded: false,
    };
    expect(nextMemoryUnwind({ ...root, closeAtRoot: true })).toBe('close-memory');
    expect(nextMemoryUnwind({ ...root, closeAtRoot: false })).toBe('noop');
  });
});
