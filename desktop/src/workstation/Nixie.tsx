import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { CATHODE_SVG } from '../worldline-engine';

export const DIVERGENCE: Record<'steins_gate' | 'beta', string> = {
  steins_gate: '1.048596',
  beta: '1.129848',
};

export const WORLDLINE_NAME: Record<'steins_gate' | 'beta', string> = {
  steins_gate: 'STEINS;GATE',
  beta: 'β',
};

/*
 * The divergence meter drawn the way the shift engine builds its instrument: eight glass tubes with
 * the engine's own cathode shapes (CATHODE_SVG, the outlines its glyph atlas is rasterized from),
 * the decimal point owning tube #2. Here the glass is a single amber line and, at the larger sizes,
 * blueprint construction lines carry the drawing. One SVG per meter, fixed viewBox: digits never
 * change the meter's box.
 */

const N = 8;
const PITCH = 114;
const ROW_W = PITCH * (N - 1) + 100;
/** Engine lock order (LOCK_AT, relative): tube 0 with the point, then left to right with a slowing cadence. */
const LOCK_SHARE = [0, 0, 0.07, 0.33, 0.5, 0.67, 0.83, 1];

type Size = 'sm' | 'md' | 'lg' | 'xl';

interface Geometry {
  h: number;
  glyph: { x: number; y: number; s: number };
  envelope: string;
  extras: string;
  socket: string | null;
  base: number;
}

const FULL: Geometry = {
  h: 232,
  glyph: { x: 50 - 36 * 0.78, y: 62, s: 0.78 },
  envelope: 'M10 196V60C10 34 28 16 50 16S90 34 90 60V196M44 16V9a6 6 0 0 1 12 0v7',
  extras: 'M20 47H80M30 43c4-5 10-5 14 0M56 43c4-5 10-5 14 0',
  socket: 'M6 196H94V212H6ZM30 212v8M50 212v8M70 212v8',
  base: 222,
};

const COMPACT: Geometry = {
  h: 200,
  glyph: { x: 50 - 36 * 0.96, y: 40, s: 0.96 },
  envelope: 'M8 192V46C8 22 26 6 50 6S92 22 92 46V192',
  extras: '',
  socket: null,
  base: 192,
};

const geometry = (size: Size) => (size === 'sm' ? COMPACT : FULL);

/** Shared view box: the row plus, when the blueprint is drawn, room for construction lines and indices. */
function viewBox(size: Size, blueprint: boolean) {
  const g = geometry(size);
  if (blueprint) return { x: -44, y: -18, w: ROW_W + 88, h: g.h + 52 };
  return { x: -6, y: 0, w: ROW_W + 12, h: g.h + 4 };
}

export interface TubeState {
  /** Text currently on the tubes. */
  text: string;
  /** Per tube: has reached its final digit. */
  locked: boolean[];
  /** Per tube supply level while tuning (voltage flicker), 1 once locked. */
  volt: number[];
  /** Increments for every retune so a lock pulse replays. */
  run: number;
  tuning: boolean;
}

const settledState = (value: string, run = 0): TubeState => ({
  text: value, locked: Array(N).fill(true), volt: Array(N).fill(1), run, tuning: false,
});

/**
 * Retuning: every tube scrambles, slows down and locks in the engine's left-to-right order with a
 * little supply flicker while it searches; locked tubes are steady. `hold` keeps the old reading
 * (e.g. while the shift show covers the meter) and tunes when released. Reduced motion is static.
 */
export function useTunedValue(value: string, opts: { hold?: boolean; duration?: number } = {}): TubeState {
  const { hold = false, duration = 950 } = opts;
  const reducedMotion = useReducedMotion();
  const [state, setState] = useState<TubeState>(() => settledState(value));
  const current = useRef(state);
  current.current = state;
  useEffect(() => {
    if (hold) return;
    const from = current.current;
    if (from.text === value && !from.tuning) return;
    if (reducedMotion) {
      setState(settledState(value, from.run + 1));
      return;
    }
    const run = from.run + 1;
    const lead = duration * 0.3;
    const lockAt = LOCK_SHARE.map(k => lead + k * (duration - lead));
    const next = Array(N).fill(0);
    const digits = from.text.split('');
    const volt = Array(N).fill(0.7);
    const locked = Array(N).fill(false);
    let start = -1;
    let raf = 0;
    const step = (now: number) => {
      if (start < 0) start = now;
      const t = now - start;
      let changed = false;
      for (let i = 0; i < N; i++) {
        if (locked[i]) continue;
        if (value[i] === '.' || t >= lockAt[i]) {
          locked[i] = true;
          digits[i] = value[i];
          volt[i] = 1;
          changed = true;
          continue;
        }
        if (t < next[i]) continue;
        // decelerating scramble: ~30 changes/s at first, ~6/s just before the lock
        const p = t / lockAt[i];
        next[i] = t + 32 + 140 * p * p;
        let d = Math.floor(Math.random() * 10);
        if (String(d) === digits[i]) d = (d + 1 + Math.floor(Math.random() * 9)) % 10;
        digits[i] = String(d);
        volt[i] = Math.random() < 0.12 ? 0.22 + Math.random() * 0.2 : 0.55 + Math.random() * 0.45;
        changed = true;
      }
      const done = locked.every(Boolean);
      if (changed) setState({ text: digits.join(''), locked: [...locked], volt: [...volt], run, tuning: !done });
      if (!done) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, hold, reducedMotion, duration]);
  return state;
}

function Glyph({ c, g, ghost }: { c: string; g: Geometry; ghost?: boolean }) {
  const t = `translate(${g.glyph.x} ${g.glyph.y}) scale(${g.glyph.s})`;
  if (ghost) {
    return <g transform={t} className="nx-stack">{CATHODE_SVG.map((d, i) => <path key={i} d={d.body} />)}</g>;
  }
  if (c === '.') return <g transform={t}><circle cx="66" cy="138" r="5.5" className="nx-dot" /></g>;
  const d = CATHODE_SVG[Number(c)];
  if (!d) return null;
  return (
    <g transform={t}>
      <path d={d.wire} className="nx-wire" />
      <path d={d.body} className="nx-body" />
      <path d={d.body} className="nx-core" />
    </g>
  );
}

export function Nixie({ value, size = 'md', label, state, title, ariaLabel, unlit = false, blueprint: drawBlueprint }: {
  value: string;
  size?: Size;
  /** Replaces the default accessible name (e.g. for a labelled preview). */
  ariaLabel?: string;
  /** Glass and cathode stack only: an instrument waiting for a reading. */
  unlit?: boolean;
  /** Settled reading for assistive tech while the visible digits tune. */
  label?: string;
  /** Live tuning state from useTunedValue; its text is what the tubes show. */
  state?: TubeState;
  /** Optional caption on the blueprint (lg/xl). */
  title?: string;
  /** Construction lines, indices and captions; on by default at lg/xl. A labelled panel can leave them off. */
  blueprint?: boolean;
}) {
  const g = geometry(size);
  const blueprint = drawBlueprint ?? (size === 'lg' || size === 'xl');
  const vb = viewBox(size, blueprint);
  const id = useId();
  const text = state?.text ?? value;
  const box = `${vb.x} ${vb.y} ${vb.w} ${vb.h}`;
  return (
    <div className={`nixie nixie-${size}${state?.tuning ? ' is-tuning' : ''}${unlit ? ' is-unlit' : ''}`} role="img"
      aria-label={ariaLabel ?? `World line divergence ${label ?? value}%`}
      style={{ '--vb-w': vb.w, '--vb-h': vb.h } as CSSProperties}>
      <svg className="nx-frame" viewBox={box} aria-hidden="true" focusable="false">
        {blueprint && (
          <g className="nx-blueprint">
            <path d={`M${vb.x + 6} 16H${ROW_W + 38}M${vb.x + 6} ${g.base}H${ROW_W + 38}`} className="nx-datum" />
            {Array.from({ length: N }, (_, i) => (
              <path key={i} d={`M${i * PITCH + 50} -12V${g.h + 18}`} className="nx-axis" />
            ))}
            <path d={`M-24 ${g.h + 12}H${ROW_W + 24}${Array.from({ length: N }, (_, i) => `M${i * PITCH + 50} ${g.h + 8}v8`).join('')}`} className="nx-ticks" />
            {Array.from({ length: N }, (_, i) => (
              <text key={i} x={i * PITCH + 50} y={g.h + 30} className="nx-index">{String(i + 1).padStart(2, '0')}</text>
            ))}
            <text x={vb.x + 6} y={4} className="nx-fig">{title ?? 'FIG.1 · DIVERGENCE METER'}</text>
            <text x={ROW_W + 38} y={4} className="nx-fig end">IN-14 × 8 · %</text>
          </g>
        )}
        <path d={`M-4 ${g.base}H${ROW_W + 4}`} className="nx-plinth" />
        {Array.from({ length: N }, (_, i) => (
          <g key={i} transform={`translate(${i * PITCH} 0)`} className="nx-tube">
            <path d={g.envelope} className="nx-glass" />
            {g.extras && <path d={g.extras} className="nx-mica" />}
            {g.socket && <path d={g.socket} className="nx-socket" />}
            <Glyph c="" g={g} ghost />
          </g>
        ))}
      </svg>
      {!unlit && <svg className="nx-glow" viewBox={box} aria-hidden="true" focusable="false">
        {text.split('').slice(0, N).map((c, i) => {
          const tuning = !!state && !state.locked[i];
          const pulse = !!state && state.run > 0 && state.locked[i] && c !== '.';
          return (
            <g key={i} transform={`translate(${i * PITCH} 0)`}>
              <g className="nx-lit" style={{ '--n': i } as CSSProperties}>
                <g key={pulse ? `${id}-${state!.run}` : 'steady'} className={tuning ? 'nx-v tuning' : pulse ? 'nx-v lock' : 'nx-v'}
                  opacity={tuning ? state!.volt[i] : undefined}>
                  <Glyph c={c} g={g} />
                </g>
              </g>
            </g>
          );
        })}
      </svg>}
    </div>
  );
}
