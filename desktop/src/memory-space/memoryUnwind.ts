export type MemoryUnwindKind = 'fact' | 'experience' | 'topic' | 'continuity_hub' | null;

export type MemoryUnwindInput = {
  toolsOpen: boolean;
  hasActiveCriteria: boolean;
  listVisible: boolean;
  selectedKind: MemoryUnwindKind;
  expanded: boolean;
  closeAtRoot: boolean;
};

export type MemoryUnwindAction =
  | 'clear-criteria'
  | 'close-tools'
  | 'close-list'
  | 'evidence-to-topic'
  | 'topic-to-overview'
  | 'collapse-branch'
  | 'close-memory'
  | 'noop';

export function nextMemoryUnwind(input: MemoryUnwindInput): MemoryUnwindAction {
  if (input.toolsOpen || input.hasActiveCriteria) {
    return input.hasActiveCriteria ? 'clear-criteria' : 'close-tools';
  }
  if (input.listVisible) return 'close-list';
  if (input.selectedKind === 'fact' || input.selectedKind === 'experience') return 'evidence-to-topic';
  if (input.selectedKind === 'topic') return 'topic-to-overview';
  if (input.expanded) return 'collapse-branch';
  return input.closeAtRoot ? 'close-memory' : 'noop';
}
