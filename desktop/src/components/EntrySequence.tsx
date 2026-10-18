import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { DraftWord } from '../workstation/DraftWord';
import './EntrySequence.css';

type Stage = 'power-on' | 'prewarm' | 'lock';
const STAGES: Record<Stage, { number: string; ja: string; zh: string; label: string }> = {
  'power-on': { number: '01', ja: '電源投入', zh: '仪器上电', label: 'POWER' },
  prewarm: { number: '02', ja: '信号調律', zh: '信号预热', label: 'TUNING' },
  lock: { number: '03', ja: '接続確立', zh: '接入完成', label: 'CONNECTED' },
};

// Original contour geometry: a field of open lines pinches into one quiet signal.
// Generated once, with no frame loop, external image or visual state updates.
const CONTOURS = Array.from({ length: 64 }, (_, index) => {
  const v = (index - 31.5) / 31.5;
  const points = Array.from({ length: 97 }, (_, sample) => {
    const t = sample / 96;
    const envelope = Math.sin(t * Math.PI);
    const x = -80 + t * 1600;
    const y = 450 + v * (70 + 280 * Math.pow(Math.abs(t * 2 - 1), 1.5))
      + Math.sin(t * Math.PI * 2 + v * 1.8) * envelope * 95
      + Math.sin(t * Math.PI) * v * v * 58;
    return `${sample ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
  });
  return points.join(' ');
});

/** App owns the authenticated lifetime; this component owns only its finite presentation. */
export function EntrySequence({ onComplete }: { onComplete: () => void }) {
  const reducedMotion = useReducedMotion();
  const [phase, setPhase] = useState<Stage>(() => reducedMotion ? 'lock' : 'power-on');
  const stage = reducedMotion ? 'lock' : phase;
  const clockRef = useRef<HTMLSpanElement>(null);
  const timerRef = useRef<number | undefined>(undefined);
  const doneRef = useRef(false);
  const completeRef = useRef(onComplete);
  const id = useId();
  completeRef.current = onComplete;
  const finish = useCallback(() => {
    if (doneRef.current) return;
    doneRef.current = true;
    window.clearTimeout(timerRef.current);
    completeRef.current();
  }, []);

  useEffect(() => {
    if (doneRef.current || !clockRef.current) return;
    // Resolve the CSS time token once per act, never on a visual frame.
    const duration = getComputedStyle(clockRef.current).transitionDuration;
    const milliseconds = parseFloat(duration) * (duration.endsWith('ms') ? 1 : 1000);
    timerRef.current = window.setTimeout(() => {
      if (doneRef.current) return;
      if (stage === 'lock') finish();
      else setPhase(stage === 'power-on' ? 'prewarm' : 'lock');
    }, Number.isFinite(milliseconds) ? milliseconds : 0);
    return () => window.clearTimeout(timerRef.current);
  }, [stage, finish]);

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

  const meta = STAGES[stage];
  return (
    <section className="entry-sequence" data-theme="terminal" data-stage={stage}
      aria-label="接入 Amadeus" onClick={finish}>
      <span className="entry-clock" ref={clockRef} aria-hidden="true" />
      <div className="entry-frame" aria-hidden="true"><i /><i /><i /><i /></div>
      <header className="entry-header">
        <span>AMADEUS SYSTEM</span><span>SYSTEM ACCESS</span>
      </header>

      <div className="entry-field" aria-hidden="true">
        <svg className="entry-contours" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
          <defs>
            <linearGradient id={`${id}-ink`}>
              <stop offset="0" className="entry-stroke-edge" />
              <stop offset=".24" className="entry-stroke-paper" />
              <stop offset=".5" className="entry-stroke-metal" />
              <stop offset=".76" className="entry-stroke-paper" />
              <stop offset="1" className="entry-stroke-edge" />
            </linearGradient>
            <radialGradient id={`${id}-silence`}>
              <stop offset="0" stopColor="black" />
              <stop offset=".32" stopColor="black" />
              <stop offset=".85" stopColor="white" />
            </radialGradient>
            <mask id={`${id}-mask`}><rect width="1440" height="900" fill={`url(#${id}-silence)`} /></mask>
          </defs>
          <g fill="none" stroke={`url(#${id}-ink)`} mask={`url(#${id}-mask)`}>
            {CONTOURS.map((path, index) => <path key={index} d={path} opacity={index % 8 === 0 ? .68 : .32} />)}
          </g>
        </svg>
      </div>

      <div className="entry-identity">
        <div className="entry-drafting" aria-hidden="true"><i /><i /><span>人工知能システム</span><i /><i /></div>
        <div className="entry-title">
          <div className="entry-title-outline" aria-hidden="true">AMADEUS</div>
          <div className="entry-title-fill"><DraftWord text="AMADEUS" /></div>
        </div>
        <div className="entry-signal" aria-hidden="true"><i /><b /><i /></div>
        <div className="entry-caption">
          <span lang="ja">あなたの声を、待っている。</span>
          <span>静候你的声音</span>
        </div>
      </div>

      <footer className="entry-footer">
        <div className="entry-status" role="status">
          <span className="entry-act">{meta.number}<small>/ 03</small></span>
          <div><span lang="ja">{meta.ja}</span><strong>{meta.zh}</strong></div>
        </div>
        <ol className="entry-stages" aria-label="接入阶段">
          {Object.entries(STAGES).map(([key, value]) => (
            <li key={key} aria-current={key === stage ? 'step' : undefined}>
              <i /><span>{value.label}</span>
            </li>
          ))}
        </ol>
        <button type="button" className="entry-skip" onClick={finish}>跳过 <kbd>Esc</kbd><span aria-hidden="true">↗</span></button>
      </footer>
    </section>
  );
}
