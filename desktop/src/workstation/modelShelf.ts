import { useSyncExternalStore } from 'react';

/*
 * Which of a service's models show in the composer's model menu (the way opencode and Cherry Studio let you pick
 * the few you use out of hundreds). A service with no shelf shows everything it lists. Kept on this computer for
 * now; amadeus-ops hand-off 2026-11-09 asks the backend to keep it with the provider's settings.
 */

const KEY = 'amadeus_pc_model_shelf';
type Shelf = Record<string, string[]>;
const listeners = new Set<() => void>();
let cache: Shelf | null = null;

function read(): Shelf {
  if (cache) return cache;
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? '{}') as unknown;
    cache = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Shelf : {};
  } catch {
    cache = {};
  }
  return cache;
}

function write(next: Shelf) {
  cache = next;
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* the menu just shows everything */ }
  listeners.forEach((fn) => fn());
}

export function setShelf(providerId: string, ids: string[] | null) {
  const next = { ...read() };
  if (ids === null) delete next[providerId]; else next[providerId] = [...new Set(ids)];
  write(next);
}

export function useModelShelf(): Shelf {
  return useSyncExternalStore(
    (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    read,
    read,
  );
}

/** The models a menu shows for a service: its shelf (plus the one in use), or all of them. */
export function shelved<T extends { id: string }>(shelf: Shelf, providerId: string, rows: T[], inUse?: string): T[] {
  const kept = shelf[providerId];
  if (!kept?.length) return rows;
  return rows.filter((row) => kept.includes(row.id) || row.id === inUse);
}
