import { useCallback, useEffect, useRef } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { SignGate } from './SignGate';
import './EntrySequence.css';

/** One continuous scene: the connection signal lights the terminal, its
 * material converges on the seam and the gate travels apart, handing the
 * viewport to the live workstation mounted beneath. */
export function EntrySequence({ onComplete }: { onComplete: () => void }) {
  const reducedMotion = useReducedMotion();
  const clockRef = useRef<HTMLSpanElement>(null);
  const timerRef = useRef<number | undefined>(undefined);
  const doneRef = useRef(false);
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;

  const finish = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    window.clearTimeout(timerRef.current);
    completeRef.current();
  }, []);

  useEffect(() => {
    if (doneRef.current || !clockRef.current) return;
    // Resolve the timeline token once per sequence, never on a visual frame.
    const duration = getComputedStyle(clockRef.current).transitionDuration;
    const milliseconds = parseFloat(duration) * (duration.endsWith('ms') ? 1 : 1000);
    timerRef.current = window.setTimeout(finish, Number.isFinite(milliseconds) ? milliseconds : 0);
    return () => window.clearTimeout(timerRef.current);
  }, [finish, reducedMotion]);

  useEffect(() => {
    const skip = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      finish();
    };
    window.addEventListener('keydown', skip, true);
    return () => window.removeEventListener('keydown', skip, true);
  }, [finish]);

  const screen = (
    <>
      <span className="entry-grid" />
      <SignGate className="entry-gate" />
      <span className="entry-columns" />
      <span className="entry-scanlines" />
      <span className="entry-vignette" />
      <span className="entry-chrome entry-chrome-top">AMADEUS SYSTEM</span>
      <span className="entry-chrome entry-chrome-foot">SYSTEM ACCESS</span>
    </>
  );

  return (
    <section
      className="entry-sequence"
      data-theme="terminal"
      data-motion={reducedMotion ? 'reduced' : 'full'}
      aria-label="接入 Amadeus 工作台"
      onClick={finish}
    >
      <span className="entry-clock" ref={clockRef} aria-hidden="true" />
      <div className="entry-pane entry-pane-upper" aria-hidden="true"><div className="entry-screen">{screen}</div></div>
      <div className="entry-pane entry-pane-lower" aria-hidden="true"><div className="entry-screen">{screen}</div></div>
      <span className="entry-seam" aria-hidden="true" />
      <span className="entry-bloom" aria-hidden="true" />
      <button type="button" className="entry-skip" onClick={finish}>
        跳过 <kbd>Esc</kbd><span aria-hidden="true">↗</span>
      </button>
      <p className="entry-announcement" role="status">正在接入工作台</p>
    </section>
  );
}
