import { useEffect, useReducer, useRef } from 'react';

/** Longer than any arrival animation (β line settle ≈ 416ms, sentence 260ms + 78ms beat). */
export const ARRIVAL_WINDOW_MS = 1000;

type Clock = () => number;

export interface ArrivalMemory {
  /** Starts a render pass. A new scope's first pass only records what is already on screen. */
  begin(scope: string): void;
  /** True while a key first seen during a live stream is still inside its arrival window. */
  arriving(key: string, live: boolean): boolean;
  /** Milliseconds until the last arrival marked in this pass closes its window, or null when none is open. */
  settlesIn(): number | null;
}

/** A blank draft materializing into its conversation (same session and worldline) is one continuing transcript. */
export function continuesTranscript(previous: string, next: string) {
  const cut = previous.lastIndexOf(':');
  return previous.slice(cut + 1) === 'draft' && next.lastIndexOf(':') === cut && next.slice(0, cut) === previous.slice(0, cut);
}

export function createArrivalMemory(clock: Clock = () => performance.now()): ArrivalMemory {
  let scope: string | null = null;
  let seen = new Map<string, number>();
  let seeding = true;
  let latest = -Infinity;
  return {
    begin(next) {
      latest = -Infinity;
      if (scope === next) { seeding = false; return; }
      const carry = scope !== null && continuesTranscript(scope, next);
      if (!carry) seen = new Map();
      seeding = !carry;
      scope = next;
    },
    arriving(key, live) {
      let first = seen.get(key);
      if (first === undefined) {
        first = seeding || !live ? -Infinity : clock();
        seen.set(key, first);
      }
      const open = clock() - first < ARRIVAL_WINDOW_MS;
      if (open) latest = Math.max(latest, first);
      return open;
    },
    settlesIn() {
      return latest === -Infinity ? null : Math.max(0, latest + ARRIVAL_WINDOW_MS - clock());
    },
  };
}

/** Presentation-only memory, scoped to the committed transcript; it never feeds business state. */
export function useArrivalMemory(scope: string, clock?: Clock) {
  const memory = useRef<ArrivalMemory | null>(null);
  memory.current ??= createArrivalMemory(clock);
  memory.current.begin(scope);
  const [, settle] = useReducer((n: number) => n + 1, 0);
  // One quiet render after the window drops the arrival classes, so a later display toggle cannot replay them.
  useEffect(() => {
    const due = memory.current?.settlesIn();
    if (due == null) return;
    const timer = window.setTimeout(settle, due + 16);
    return () => window.clearTimeout(timer);
  });
  return memory.current;
}

export interface SentenceArrivals { ja: ReadonlySet<number>; zh: ReadonlySet<number> }

/** Japanese and Chinese are remembered separately so a late subtitle never replays its resolved original. */
export function sentenceArrivals(
  memory: ArrivalMemory,
  messageId: string,
  segments: ReadonlyArray<{ id: number; ja: string; zh: string }> | undefined,
  live: boolean,
): SentenceArrivals {
  const ja = new Set<number>();
  const zh = new Set<number>();
  for (const segment of segments ?? []) {
    if (segment.ja && memory.arriving(`j:${messageId}:${segment.id}`, live)) ja.add(segment.id);
    if (segment.zh && memory.arriving(`z:${messageId}:${segment.id}`, live)) zh.add(segment.id);
  }
  return { ja, zh };
}
