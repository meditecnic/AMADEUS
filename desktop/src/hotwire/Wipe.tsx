import { useEffect, useRef } from 'react';

/**
 * Scene change: three slabs cut at −12° (hot, black, bone) sweep left to right, 60 ms apart; the scene
 * underneath swaps while they cover it. Reduced motion: a straight cut, no sweep.
 */
interface Props {
  tone: 'sg' | 'beta';
  reduced?: boolean;
  /** Fires once the screen is fully covered. */
  onCovered: () => void;
  onDone: () => void;
  /** Optional word riding on the hot slab (the easter-egg entrance). */
  word?: string;
}

const COVERED_MS = 300;
const DONE_MS = 680;

export function Wipe({ tone, reduced = false, onCovered, onDone, word }: Props) {
  const cb = useRef({ onCovered, onDone });
  cb.current = { onCovered, onDone };
  useEffect(() => {
    if (reduced) {
      cb.current.onCovered();
      const id = window.setTimeout(() => cb.current.onDone(), 0);
      return () => window.clearTimeout(id);
    }
    const a = window.setTimeout(() => cb.current.onCovered(), word ? COVERED_MS + 700 : COVERED_MS);
    const b = window.setTimeout(() => cb.current.onDone(), word ? DONE_MS + 700 : DONE_MS);
    return () => { window.clearTimeout(a); window.clearTimeout(b); };
  }, [reduced, word]);
  if (reduced) return null;
  return (
    <div className={`hw-wipe${word ? ' has-word' : ''}`} data-wl={tone} aria-hidden="true">
      <i className="s1" />
      <i className="s2" />
      <i className="s3" />
      {word ? <b className="hw-wipe-word">{word}</b> : null}
    </div>
  );
}
