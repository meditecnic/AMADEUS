import { useSyncExternalStore } from 'react';
import { apiFetch } from '../apiBase';

/*
 * Which of a service's models show in the composer's model menu (the way opencode and Cherry Studio let you pick
 * the few you use out of hundreds). A service with no shelf shows everything it lists.
 *
 * The backend keeps it with the provider's settings (`visible_models`, PATCH /api/providers/{id}/settings). The
 * copy here is what the page reads: it is filled from the provider list each time that loads, and a change is
 * shown at once and then written through. A shelf made before the backend kept it (localStorage) is sent up once.
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
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* the backend has it */ }
  listeners.forEach((fn) => fn());
}

function push(providerId: string, ids: string[]) {
  void apiFetch(`/api/providers/${encodeURIComponent(providerId)}/settings`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visible_models: ids }),
  }).catch(() => undefined); // kept here; the next change or load tries again
}

export function setShelf(providerId: string, ids: string[] | null) {
  const next = { ...read() };
  const list = ids === null ? [] : [...new Set(ids)];
  if (list.length) next[providerId] = list; else delete next[providerId];
  write(next);
  push(providerId, list);
}

/** Take the shelves the provider list came back with; a local shelf the backend has not seen yet is sent up. */
export function adoptShelves(rows: { id: string; visible_models?: string[] | null }[]) {
  const local = read();
  const next: Shelf = {};
  let changed = false;
  for (const row of rows) {
    if (!Array.isArray(row.visible_models)) { // an older backend: keep what is here
      if (local[row.id]) next[row.id] = local[row.id];
      continue;
    }
    if (row.visible_models.length) next[row.id] = row.visible_models;
    else if (local[row.id]?.length) { next[row.id] = local[row.id]; push(row.id, local[row.id]); }
    if (JSON.stringify(next[row.id] ?? null) !== JSON.stringify(local[row.id] ?? null)) changed = true;
  }
  if (changed || Object.keys(local).some((id) => !(id in next))) write(next);
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
