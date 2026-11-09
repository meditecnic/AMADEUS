import { useCallback, useEffect, useState } from 'react';
import type { ConversationSummary } from '../components/HistorySidebar';

/*
 * NEW in the session list: a conversation whose last message landed after you last had it open on this computer
 * (her reply finishing after you moved on). The server bumps `last_active_at` only when a message is stored, so a
 * rename or a pin never marks a row. Kept per device; a conversation never opened here is never NEW.
 */
const KEY = 'amadeus_seen_conversations_v1';
type Seen = Record<string, string>;

function read(): Seen {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Seen) : {};
  } catch {
    return {};
  }
}

function write(seen: Seen) {
  try { localStorage.setItem(KEY, JSON.stringify(seen)); } catch { /* storage refused: the marks just don't persist */ }
}

const time = (iso: string | undefined) => (iso ? Date.parse(iso) || 0 : 0);

export function useSeenConversations(rows: readonly ConversationSummary[], activeId: string | null) {
  const [seen, setSeen] = useState<Seen>(read);
  const activeAt = rows.find((row) => row.id === activeId)?.last_active_at;
  useEffect(() => {
    if (!activeId || !activeAt) return;
    setSeen((prev) => {
      if (time(prev[activeId]) >= time(activeAt)) return prev;
      let next: Seen = { ...prev, [activeId]: activeAt };
      // keep the record small: past a few hundred entries, forget conversations no longer listed
      if (Object.keys(next).length > 300) {
        const listed = new Set(rows.map((row) => row.id));
        next = Object.fromEntries(Object.entries(next).filter(([id]) => listed.has(id)));
      }
      write(next);
      return next;
    });
  }, [activeId, activeAt, rows]);
  return useCallback(
    (row: ConversationSummary) => row.id !== activeId && Boolean(seen[row.id]) && time(row.last_active_at) > time(seen[row.id]),
    [seen, activeId],
  );
}
