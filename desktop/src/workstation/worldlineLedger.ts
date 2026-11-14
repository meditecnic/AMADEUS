import { useEffect, useState } from 'react';
import { apiFetch } from '../apiBase';

/*
 * What each world line holds, for the ledger under the worldline figure: how many conversations, how many memories
 * (facts and experiences, both identity modes), and when it was last used. Read when the page opens; a line whose
 * numbers cannot be read shows dashes, never zero.
 */

export type Worldline = 'steins_gate' | 'beta';
export type LedgerLine = { conversations: number | null; memories: number | null; last: string | null };
export type Ledger = Partial<Record<Worldline, LedgerLine>>;

async function conversationsOf(sessionId: string, worldline: Worldline): Promise<{ count: number; last: string | null } | null> {
  try {
    const response = await apiFetch(`/api/conversations?session_id=${encodeURIComponent(sessionId)}&worldline=${encodeURIComponent(worldline)}`);
    if (!response.ok) return null;
    const rows = await response.json() as { last_active_at?: string }[];
    if (!Array.isArray(rows)) return null;
    const last = rows.map((row) => row.last_active_at ?? '').filter(Boolean).sort().pop() ?? null;
    return { count: rows.length, last };
  } catch {
    return null;
  }
}

async function memoriesOf(sessionId: string, worldline: Worldline): Promise<number | null> {
  const counts = await Promise.all((['self', 'okabe'] as const).map(async (mode) => {
    try {
      const query = new URLSearchParams({ session_id: sessionId, worldline, identity_mode: mode });
      const response = await apiFetch(`/api/memory/chart?${query}`);
      if (!response.ok) return null;
      const body = await response.json() as { counts?: { facts?: unknown; experiences?: unknown } };
      const facts = body.counts?.facts;
      const experiences = body.counts?.experiences;
      return typeof facts === 'number' && typeof experiences === 'number' ? facts + experiences : null;
    } catch {
      return null;
    }
  }));
  return counts.some((n) => n === null) ? null : (counts as number[]).reduce((a, b) => a + b, 0);
}

/** Both lines in one request (GET /api/worldlines/summary, the same counts as the six requests below). */
async function summaryOf(sessionId: string): Promise<Ledger | 'unavailable' | null> {
  try {
    const response = await apiFetch(`/api/worldlines/summary?session_id=${encodeURIComponent(sessionId)}`);
    if (response.status === 503) {
      // Only a data move blocks every read until restart; other 503s (one line's store down) still allow per-line reads.
      const body = await response.json().catch(() => null) as { detail?: { code?: unknown } } | null;
      return body?.detail?.code === 'data_relocating' ? 'unavailable' : null;
    }
    if (!response.ok) return null;
    const body = await response.json() as Partial<Record<Worldline, { conversations?: unknown; memories?: unknown; last_active_at?: unknown }>>;
    const line = (key: Worldline): LedgerLine | null => {
      const row = body?.[key];
      if (!row || typeof row.conversations !== 'number' || typeof row.memories !== 'number') return null;
      return { conversations: row.conversations, memories: row.memories, last: typeof row.last_active_at === 'string' ? row.last_active_at : null };
    };
    const sg = line('steins_gate');
    const beta = line('beta');
    return sg && beta ? { steins_gate: sg, beta } : null;
  } catch {
    return null;
  }
}

export function useWorldlineLedger(sessionId: string, open: boolean): Ledger {
  const [ledger, setLedger] = useState<Ledger>({});
  useEffect(() => {
    if (!open || !sessionId) return;
    let live = true;
    void (async () => {
      const summary = await summaryOf(sessionId);
      if (summary === 'unavailable') return;
      if (summary) { if (live) setLedger(summary); return; }
      // The summary fails as a whole when one line cannot be read; line by line still shows the one that can.
      const rows = await Promise.all((['steins_gate', 'beta'] as const).map(async (line) => {
        const [talk, memories] = await Promise.all([conversationsOf(sessionId, line), memoriesOf(sessionId, line)]);
        return [line, { conversations: talk?.count ?? null, memories, last: talk?.last ?? null }] as const;
      }));
      if (live) setLedger(Object.fromEntries(rows));
    })();
    return () => { live = false; };
  }, [sessionId, open]);
  return ledger;
}

/** 11-09 */
export function shortDate(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
