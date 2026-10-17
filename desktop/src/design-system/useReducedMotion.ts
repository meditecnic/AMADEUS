import { useSyncExternalStore } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';
const subscribers = new Set<() => void>();
let media: MediaQueryList | undefined;

function readMedia() {
  return typeof window === 'undefined' ? undefined : window.matchMedia?.(QUERY);
}

function notify() {
  subscribers.forEach((subscriber) => subscriber());
}

function subscribe(subscriber: () => void) {
  if (subscribers.size === 0) {
    media = readMedia();
    if (media?.addEventListener) media.addEventListener('change', notify);
    else media?.addListener?.(notify);
  }
  subscribers.add(subscriber);
  return () => {
    subscribers.delete(subscriber);
    if (subscribers.size > 0) return;
    if (media?.removeEventListener) media.removeEventListener('change', notify);
    else media?.removeListener?.(notify);
    media = undefined;
  };
}

/** Shared, live preference; callers may choose a conservative unavailable/SSR fallback. */
export function useReducedMotion(unavailable = false) {
  return useSyncExternalStore(
    subscribe,
    () => (media ?? readMedia())?.matches ?? unavailable,
    () => unavailable,
  );
}
