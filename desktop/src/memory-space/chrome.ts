export type MemorySpaceShortcut = 'search' | 'list' | 'view-mode';

export function memorySpaceShortcut(event: {
  key: string;
  target?: { tagName?: string } | EventTarget | null;
}): MemorySpaceShortcut | null {
  const target = event.target as { tagName?: string } | null | undefined;
  const tag = target && 'tagName' in target ? target.tagName : undefined;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return null;
  if (event.key === '/' || event.key === 'f' || event.key === 'F') return 'search';
  if (event.key === 'l' || event.key === 'L') return 'list';
  if (event.key === '2') return 'view-mode';
  return null;
}

export const DEFAULT_CHROME = ['return', 'memory-soul', 'plaque'] as const;
export const OVERFLOW_CHROME = ['search', 'list', 'view-mode', 'identity', 'worldline'] as const;
