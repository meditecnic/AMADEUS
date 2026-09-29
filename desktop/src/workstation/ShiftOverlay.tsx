import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { DIVERGENCE, Nixie } from './Nixie';

/* Worldline shift, after the RE:BOOT capture: the world goes dull → a tunnel of tube silhouettes → the world turns
 * to paper, meter centred, digits run wild, ink and gears scatter → a light streak → digits lock left to right →
 * RGB split flash → back in the same place, on the other line. The visual never claims the backend switch finished;
 * onCommit only hands the request to the existing worldline switch path. */

type Worldline = 'steins_gate' | 'beta';

function gearPath(teeth: number, ro: number, ri: number, hole: number) {
  const pts: string[] = [];
  for (let i = 0; i < teeth; i++) {
    const a = (i / teeth) * Math.PI * 2;
    const s = (Math.PI * 2) / teeth;
    [[a, ri], [a + s * 0.18, ro], [a + s * 0.5, ro], [a + s * 0.68, ri]].forEach(([t, r]) => {
      pts.push(`${(Math.cos(t) * r).toFixed(1)} ${(Math.sin(t) * r).toFixed(1)}`);
    });
  }
  return `M${pts.join('L')}Z M${hole} 0A${hole} ${hole} 0 1 0 ${-hole} 0A${hole} ${hole} 0 1 0 ${hole} 0Z`;
}
const GEARS = [
  { t: 18, ro: 60, x: -38, y: -30, s: 1, r: 220 }, { t: 12, ro: 38, x: 34, y: -36, s: 0.8, r: -260 },
  { t: 24, ro: 84, x: 30, y: 34, s: 1.2, r: 160 }, { t: 10, ro: 30, x: -30, y: 38, s: 0.7, r: -320 },
  { t: 16, ro: 50, x: 44, y: 4, s: 0.9, r: 240 }, { t: 14, ro: 44, x: -46, y: 6, s: 0.85, r: -200 },
];
const BLOBS = [
  'M0-30C18-32 34-14 30 6 26 26 4 34-14 28-32 22-36-4-26-18-20-26-10-29 0-30Z',
  'M-4-20C10-26 26-12 20 4 16 18 0 24-12 16-24 8-18-14-4-20Z',
  'M2-40C26-36 38-8 28 16 18 38-12 42-28 24-42 8-34-22-18-34-10-39-4-40 2-40Z',
];
const LOCK_START = 2350;
const LOCK_STEP = 80;
const COMMIT_AT = 3050;
const FLY_AT = 3350;
const END_AT = 3950;

export function ShiftOverlay({ from, to, onCommit, onDone }: { from: Worldline; to: Worldline; onCommit: () => void; onDone: () => void }) {
  const target = DIVERGENCE[to];
  const [shown, setShown] = useState(DIVERGENCE[from]);
  const [locked, setLocked] = useState(0);
  const reduce = useRef(typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches));
  const committed = useRef(false);
  const finished = useRef(false);
  const root = useRef<HTMLDivElement>(null);
  const [fly, setFly] = useState(false);
  // The big meter flies home into the header meter, so the new line is read where it will stay.
  const startFly = () => {
    const el = root.current;
    const src = el?.querySelector<HTMLElement>('.sh-meter .nixie-row');
    const dst = document.querySelector<HTMLElement>('.ws2-meter .nixie-row');
    if (!el || !src || !dst) return;
    const a = src.getBoundingClientRect();
    const b = dst.getBoundingClientRect();
    el.style.setProperty('--fx', `${b.left + b.width / 2 - (a.left + a.width / 2)}px`);
    el.style.setProperty('--fy', `${b.top + b.height / 2 - (a.top + a.height / 2)}px`);
    el.style.setProperty('--fs', String(b.width / a.width));
    setFly(true);
  };
  const commit = () => { if (!committed.current) { committed.current = true; onCommit(); } };
  const end = () => {
    if (finished.current) return;
    finished.current = true;
    commit();
    onDone();
  };

  useEffect(() => {
    if (reduce.current) {
      setShown(target);
      setLocked(8);
      const a = window.setTimeout(commit, 150);
      const b = window.setTimeout(end, 900);
      return () => { window.clearTimeout(a); window.clearTimeout(b); };
    }
    const t0 = performance.now();
    let flying = false;
    const iv = window.setInterval(() => {
      const t = performance.now() - t0;
      if (t < 1000) return;
      const n = Math.max(0, Math.min(8, Math.floor((t - LOCK_START) / LOCK_STEP) + 1));
      setLocked(n);
      setShown(target.split('').map((c, i) => (i < n || c === '.' ? c : String(Math.floor(Math.random() * 10)))).join(''));
      if (t > COMMIT_AT) commit();
      if (t > FLY_AT && !flying) { flying = true; startFly(); }
      if (t > END_AT) { window.clearInterval(iv); end(); }
    }, 45);
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); end(); } };
    window.addEventListener('keydown', k, true);
    return () => { window.clearInterval(iv); window.removeEventListener('keydown', k, true); };
    // Mount-only timeline.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div ref={root} className={`ws2-shift${reduce.current ? ' reduced' : ''}${fly ? ' fly' : ''}`} onClick={end} role="status" aria-live="polite"
      aria-label={`世界线变动：${to === 'steins_gate' ? 'STEINS;GATE' : 'β'}`}>
      <div className="sh-world">
      <div className="sh-dull" />
      <svg className="sh-tunnel" viewBox="-100 -60 200 120" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        {Array.from({ length: 9 }, (_, i) => (
          <g key={i} style={{ '--k': i } as CSSProperties}>
            {[-1, 1].map((sd) => Array.from({ length: 4 }, (_, j) => (
              <path key={`${sd}${j}`} d={`M${sd * (20 + j * 9)} 30V-14A4.5 4.5 0 0 ${sd > 0 ? 1 : 0} ${sd * (20 + j * 9) + 9 * sd} -14V30`} />
            )))}
          </g>
        ))}
      </svg>
      <div className="sh-paper" />
      <svg className="sh-ink" viewBox="-100 -60 200 120" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <defs>
          <filter id="ws2-inkf" x="-50%" y="-50%" width="200%" height="200%">
            <feTurbulence type="fractalNoise" baseFrequency="0.11" numOctaves="3" seed="7" />
            <feDisplacementMap in="SourceGraphic" scale="7" />
          </filter>
        </defs>
        <g filter="url(#ws2-inkf)">
          {Array.from({ length: 12 }, (_, i) => {
            const a = (i / 12) * Math.PI * 2 + i;
            return <path key={i} d={BLOBS[i % 3]} style={{ '--tx': `${Math.cos(a) * 95}px`, '--ty': `${Math.sin(a) * 60}px`, '--sc': 0.14 + (i % 4) * 0.07, '--d': `${(i % 5) * 40}ms` } as CSSProperties} />;
          })}
          {Array.from({ length: 28 }, (_, i) => {
            const a = i * 2.39996;
            const dist = 40 + (i % 7) * 12;
            return <circle key={`d${i}`} r={0.6 + (i % 4) * 0.55} style={{ '--tx': `${Math.cos(a) * dist}px`, '--ty': `${Math.sin(a) * dist * 0.62}px`, '--sc': 1, '--d': `${60 + (i % 6) * 30}ms` } as CSSProperties} />;
          })}
        </g>
      </svg>
      <svg className="sh-gears" viewBox="-100 -60 200 120" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        {GEARS.map((g, i) => (
          <path key={i} d={gearPath(g.t, g.ro, g.ro * 0.84, g.ro * 0.3)} fillRule="evenodd"
            style={{ '--tx': `${g.x * 2.2}px`, '--ty': `${g.y * 1.6}px`, '--sc': g.s * 0.32, '--rot': `${g.r}deg`, '--d': `${i * 50}ms` } as CSSProperties} />
        ))}
      </svg>
      <div className="sh-streak"><i /></div>
      <div className="sh-rgb" />
      </div>
      <div className="sh-meter">
        <Nixie value={shown} size="xl" base />
        <div className="sh-lock">{locked >= 8 ? (to === 'steins_gate' ? 'STEINS;GATE' : 'β WORLDLINE') : 'DIVERGENCE'}</div>
      </div>
      <div className="sh-skip">点击或 Esc 跳过</div>
    </div>
  );
}
