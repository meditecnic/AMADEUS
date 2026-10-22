import { useCallback, useLayoutEffect, useRef, useState, type RefObject, type KeyboardEvent } from 'react';

const BOTTOM_TOLERANCE = 40;
const READING_COOLDOWN = 150;
type Edges = { overflow: boolean; atStart: boolean; atEnd: boolean; showJumpToLatest: boolean };

/** Viewport-only state. Scope follows the published transcript, never a pending target. */
export function useScrollPinning({
  viewportRef, scope, content, messageCount, activity, reducedMotion, paused = false,
}: {
  viewportRef: RefObject<HTMLDivElement | null>;
  scope: string;
  content: unknown;
  messageCount: number;
  activity: string;
  reducedMotion: boolean;
  paused?: boolean;
}) {
  const [edges, setEdges] = useState<Edges>({ overflow: false, atStart: true, atEnd: true, showJumpToLatest: false });
  const pinned = useRef(true);
  const lastUserScroll = useRef(-Infinity);
  const frame = useRef<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef(false);
  const programmatic = useRef<{ top: number; smooth: boolean } | null>(null);
  const enabled = useRef(false);
  const count = useRef(messageCount);

  const cancelPending = useCallback(() => {
    if (frame.current !== null) window.cancelAnimationFrame(frame.current);
    if (timer.current !== null) clearTimeout(timer.current);
    frame.current = timer.current = null;
    pending.current = false;
  }, []);

  const measure = useCallback(() => {
    const el = viewportRef.current;
    const overflow = Boolean(el && el.scrollHeight > el.clientHeight + 1);
    const distance = el ? Math.max(0, el.scrollHeight - el.clientHeight - el.scrollTop) : 0;
    const nearBottom = distance <= BOTTOM_TOLERANCE;
    const next = {
      overflow,
      atStart: !overflow || (el?.scrollTop ?? 0) <= 1,
      atEnd: !overflow || distance <= 1,
      showJumpToLatest: count.current > 0 && overflow && !nearBottom,
    };
    setEdges(old => Object.keys(next).every(key => old[key as keyof Edges] === next[key as keyof Edges]) ? old : next);
    return nearBottom;
  }, [viewportRef]);

  const apply = useCallback((smooth = false) => {
    const el = viewportRef.current;
    if (!el || !enabled.current || !pinned.current) return;
    programmatic.current = { top: Math.max(0, el.scrollHeight - el.clientHeight), smooth };
    if (smooth && typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    else el.scrollTop = el.scrollHeight;
    measure();
  }, [measure, viewportRef]);

  const follow = useCallback(function followLatest() {
    if (!enabled.current || !pinned.current) { measure(); return; }
    pending.current = true;
    const remaining = READING_COOLDOWN - (Date.now() - lastUserScroll.current);
    if (remaining > 0) {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        if (pending.current) followLatest();
      }, remaining);
      measure();
      return;
    }
    cancelPending();
    apply();
    frame.current = window.requestAnimationFrame(() => {
      frame.current = null;
      // A manual event cancels this frame, including the last update in a stream.
      if (Date.now() - lastUserScroll.current >= READING_COOLDOWN) apply();
    });
  }, [apply, cancelPending, measure]);

  const onUserIntent = useCallback(() => {
    lastUserScroll.current = Date.now();
    const wasSmooth = programmatic.current?.smooth;
    programmatic.current = null;
    const wasPending = pending.current || frame.current !== null;
    cancelPending();
    // Interrupt a native smooth jump before the browser can pull the reader back.
    const el = viewportRef.current;
    if (wasSmooth) el?.scrollTo?.({ top: el.scrollTop, behavior: 'instant' });
    if (wasPending && pinned.current) follow();
  }, [cancelPending, follow, viewportRef]);

  const onScroll = useCallback(() => {
    const el = viewportRef.current;
    const own = programmatic.current;
    const isOwnScroll = Boolean(own && el && (own.smooth || Math.abs(el.scrollTop - own.top) <= 1));
    if (!isOwnScroll) onUserIntent();
    const nearBottom = measure();
    if (!isOwnScroll) pinned.current = nearBottom;
    if (!pinned.current) cancelPending();
    if (isOwnScroll && el && own && Math.abs(el.scrollTop - own.top) <= 1) programmatic.current = null;
  }, [cancelPending, measure, onUserIntent, viewportRef]);

  const onKeyDown = useCallback((event: KeyboardEvent) => {
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) onUserIntent();
  }, [onUserIntent]);

  const scrollToLatest = useCallback((options?: { smooth?: boolean }) => {
    cancelPending();
    pinned.current = true;
    lastUserScroll.current = -Infinity;
    const smooth = Boolean(options?.smooth) && !reducedMotion;
    apply(smooth);
    frame.current = window.requestAnimationFrame(() => { frame.current = null; apply(smooth); });
  }, [apply, cancelPending, reducedMotion]);

  useLayoutEffect(() => {
    pinned.current = true;
    lastUserScroll.current = -Infinity;
    programmatic.current = null;
    return cancelPending;
  }, [scope, cancelPending]);

  useLayoutEffect(() => {
    enabled.current = !paused;
    return () => { enabled.current = false; cancelPending(); };
  }, [scope, paused, cancelPending]);

  useLayoutEffect(() => {
    count.current = messageCount;
    follow();
  }, [scope, content, messageCount, activity, paused, follow]);

  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => follow());
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, [scope, messageCount === 0, follow, viewportRef]);

  return { ...edges, onScroll, onUserIntent, onKeyDown, scrollToLatest, cancelPending };
}
