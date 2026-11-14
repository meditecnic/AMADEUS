import type { ConversationSummary } from '../components/HistorySidebar';

/**
 * "No.03": a conversation's number on its worldline, in the order the conversations were opened (the oldest is 01),
 * as the original numbers its save slots. It holds while the list reorders by activity. Rows without a creation time
 * count by their last activity.
 */
export function conversationNumbers(rows: readonly ConversationSummary[]): Map<string, number> {
  const opened = (c: ConversationSummary) => Date.parse(c.created_at ?? c.last_active_at) || 0;
  const order = [...rows].sort((a, b) => opened(a) - opened(b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return new Map(order.map((c, i) => [c.id, i + 1]));
}

export const conversationNo = (n: number) => `No.${String(n).padStart(2, '0')}`;
