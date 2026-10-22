import { useCallback, useEffect, useState } from 'react';

/** Upper bound for an exit that never reports its end (hidden tab, removed animation). Longer than any β exit. */
export const EXIT_FALLBACK_MS = 400;

export interface ExitPresence<T> {
  /** What to render: the live value, or the last one while it plays its exit. */
  shown: T | null;
  /** True only for the exit ghost; the business value is already gone and the ghost must be inert. */
  leaving: boolean;
  /** Ends the exit early (animationend). */
  done: () => void;
}

/**
 * Keeps the last value of a closing surface on screen for its exit animation only. Business state has
 * already moved on, so nothing here can delay a selection or deletion. Reduced motion skips the ghost,
 * and a reopen, a direct swap to another value or unmount drops a stale one.
 */
export function useExitPresence<T>(value: T | null, reduced: boolean): ExitPresence<T> {
  const [previous, setPrevious] = useState<T | null>(value);
  const [ghost, setGhost] = useState<T | null>(null);
  if (value !== previous) {
    setPrevious(value);
    setGhost(value === null && !reduced ? previous : null);
  }
  const done = useCallback(() => setGhost(null), []);
  useEffect(() => {
    if (ghost === null) return;
    const timer = window.setTimeout(done, EXIT_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [ghost, done]);
  return { shown: value ?? ghost, leaving: value === null && ghost !== null, done };
}
