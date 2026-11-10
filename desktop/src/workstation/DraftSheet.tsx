import { memo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { DIVERGENCE } from './Nixie';
import { arc, escapeWheel, f1, gearOutline, lorenzPath, polar, ticks } from './drafting';

/*
 * The workstation is the same drafting sheet as the login (design/references.md §4): the paper, its grid and zone
 * border, and one large figure on the right that runs off the sheet. Each worldline has its own figure, and each
 * figure means something:
 *  - Steins;Gate: a clock movement — escape wheel, anchor, going train and dial. The dial shows the local time;
 *    the escape wheel steps once a second.
 *  - β: the Lorenz attractor (the butterfly of the butterfly effect), with its equations written beside it as on a
 *    blackboard. Two points are integrated on it live from starts 10⁻⁴ apart: for a minute they run as one, then
 *    they come apart (<Divergence>).
 * Under each figure is the other line's figure, rubbed out and not quite gone (<Trace>): the sheet changed worldlines,
 * and only a trace of the old drawing is left. Drawn here, not traced from anything. 1920 × 1080 user space, sliced to the window like the login plate.
 */

const C = { x: 1520, y: 590 };
const DIAL = 440;
const DIAL_PT = polar(DIAL, -128);
const ROMAN = ['XII', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];
const CENTER_WHEEL = gearOutline(72, 196);
const THIRD = { at: polar(300, 152), n: 48, r: 104 };
const THIRD_WHEEL = gearOutline(THIRD.n, THIRD.r);
const ESC = { at: polar(352, 122), n: 30, r: 92 };
const ESC_WHEEL = escapeWheel(ESC.n, ESC.r);
const ESC_SPOKES = [0, 72, 144, 216, 288].map((a) => {
  const [x0, y0] = polar(16, a);
  const [x1, y1] = polar(ESC.r * 0.8, a);
  return `M${x0} ${y0}L${x1} ${y1}`;
}).join('');
const CW_SPOKES = [0, 90, 180, 270].map((a) => {
  const [x0, y0] = polar(34, a - 6), [x1, y1] = polar(170, a - 2), [x2, y2] = polar(170, a + 2), [x3, y3] = polar(34, a + 6);
  return `M${x0} ${y0}L${x1} ${y1}M${x2} ${y2}L${x3} ${y3}`;
}).join('');
/* The anchor's pivot sits above the escape wheel; its two pallets straddle a few teeth. */
const ANCHOR_PIVOT = { x: ESC.at[0], y: ESC.at[1] - ESC.r - 64 };
const ANCHOR = (() => {
  const dy = ESC.r + 64;
  const at = (r: number, a: number) => { const [x, y] = polar(r, a); return `${f1(x)} ${f1(y + dy)}`; };
  // the bow from pallet to pallet, the two pallet faces dipping between the teeth, and the crutch above the pivot
  return `M${at(ESC.r + 6, -124)}Q0 -18 ${at(ESC.r + 6, -56)}M${at(ESC.r + 6, -124)}L${at(ESC.r - 10, -118)}M${at(ESC.r + 6, -56)}L${at(ESC.r - 10, -62)}M0 0L0 ${f1(dy - ESC.r - 14)}M0 -8V-150`;
})();

const dly = (ms: number) => ({ '--d': `${ms}ms` }) as CSSProperties;

/** The time, once a second; held where it was while `still`. */
function useSecond(still: boolean) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (still) return;
    setNow(new Date());
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, [still]);
  return now;
}

const pad = (n: number) => String(n).padStart(2, '0');

/*
 * The clock's moving parts sweep (U 11-09: it ticked once a second and read as stutter). Each part is a small drawing
 * of its own, turned by a CSS animation on its own layer, so the compositor moves it continuously and the page does no
 * work for it: the hands at the local time, the escape wheel once a minute, the going train slower, the anchor rocking
 * once a second. The phase is set from the clock when the sweep starts (and again whenever the window comes back into
 * view); while the sheet is still (the link is down) or motion is reduced, the parts are set once a second instead.
 * Positions are plate user space; sheet.css maps them to the window the way the plate's slice does.
 */
type Part = { id: string; at: [number, number]; r: number; period: number; reverse?: boolean; rock?: boolean; angle: (t: Date) => number; body: ReactNode };

const secs = (t: Date) => t.getSeconds() + t.getMilliseconds() / 1000;
const mins = (t: Date) => t.getMinutes() + secs(t) / 60;
const hours = (t: Date) => (t.getHours() % 12) + mins(t) / 60;

const TRAIN: Part[] = [
  {
    id: 'center', at: [C.x, C.y], r: 216, period: 14400, angle: (t) => mins(t) * 1.5,
    body: <><path className="ln" d={CENTER_WHEEL} /><path className="ln" d={CW_SPOKES} /></>,
  },
  {
    id: 'third', at: [C.x + THIRD.at[0], C.y + THIRD.at[1]], r: THIRD.r + 14, period: 2400, reverse: true, angle: (t) => -mins(t) * 9,
    body: <><path className="ln" d={THIRD_WHEEL} /><circle className="ln" r={22} /><circle className="ln" r={7} /></>,
  },
  {
    id: 'escape', at: [C.x + ESC.at[0], C.y + ESC.at[1]], r: ESC.r + 6, period: 60, angle: (t) => secs(t) * 6,
    body: <><path className="ln strong" d={ESC_WHEEL} /><path className="ln" d={ESC_SPOKES} /><circle className="ln" r={16} /><circle className="ln" r={5} /></>,
  },
  {
    id: 'anchor', at: [C.x + ANCHOR_PIVOT.x, C.y + ANCHOR_PIVOT.y], r: 168, period: 1, rock: true, angle: () => 0,
    body: <><path className="ln strong" d={ANCHOR} /><circle className="ln" r={9} /><circle className="ln" r={3} /></>,
  },
];
const HANDS: Part[] = [
  { id: 'hour', at: [C.x, C.y], r: 206, period: 43200, angle: (t) => hours(t) * 30, body: <path className="hand" d="M-7 34L-4 -150L0 -196L4 -150L7 34Z" /> },
  { id: 'minute', at: [C.x, C.y], r: 326, period: 3600, angle: (t) => mins(t) * 6, body: <path className="hand" d="M-5 40L-2.5 -290L0 -318L2.5 -290L5 40Z" /> },
  {
    id: 'second', at: [C.x, C.y], r: 380, period: 60, angle: (t) => secs(t) * 6,
    body: <><path className="hand sec" d="M0 70V-372" /><circle className="hand sec" cy={52} r={10} /><circle className="hub" r={11} /></>,
  },
];

const Parts = memo(function Parts({ parts, at, run }: { parts: Part[]; at: Date; run: boolean }) {
  return (
    <div className="ds-spin">
      {parts.map((p) => (
        <div
          key={run ? `${p.id}-${at.getTime()}` : p.id}
          className={`ds-part${run ? (p.rock ? ' rock' : ' run') : ''}`}
          style={{
            '--ux': p.at[0], '--uy': p.at[1], '--r': p.r,
            '--a0': `${f1(((p.angle(at) % 360) + 360) % 360)}deg`,
            '--period': `${p.period}s`, animationDirection: p.reverse ? 'reverse' : undefined,
          } as CSSProperties}
        >
          <svg className="ds-plate" viewBox={`${-p.r} ${-p.r} ${p.r * 2} ${p.r * 2}`}>{p.body}</svg>
        </div>
      ))}
    </div>
  );
});

/** When the sweep was set from the clock: on start, and again each time the window comes back into view. */
function useSweepEpoch(run: boolean) {
  const [epoch, setEpoch] = useState(() => new Date());
  useEffect(() => {
    if (!run) return undefined;
    setEpoch(new Date());
    const resync = () => { if (document.visibilityState === 'visible') setEpoch(new Date()); };
    document.addEventListener('visibilitychange', resync);
    return () => document.removeEventListener('visibilitychange', resync);
  }, [run]);
  return epoch;
}

/** The dial and everything on the plate that does not move; the moving parts are <Parts>, under and over it. */
const Movement = memo(function Movement({ still }: { still: boolean }) {
  const now = useSecond(still);
  const s = now.getSeconds();
  return (
    <g className="ds-fig ds-clock">
      {/* going train, behind the dial: only the line joining the arbors is drawn here */}
      <g transform={`translate(${C.x} ${C.y})`}>
        <path className="dash" d={`M0 0L${THIRD.at[0]} ${THIRD.at[1]}L${ESC.at[0]} ${ESC.at[1]}`} />
      </g>
      {/* dial */}
      <g transform={`translate(${C.x} ${C.y})`}>
        <circle className="ln" pathLength={1} style={dly(0)} r={DIAL} transform="rotate(-90)" />
        <circle className="ln" pathLength={1} style={dly(120)} r={DIAL - 16} transform="rotate(-90)" />
        <path className="tk" d={ticks(60, DIAL - 16, DIAL - 30, 5, DIAL - 44)} />
        <path className="tk fine" d={ticks(240, DIAL - 16, DIAL - 22)} />
        <circle className="dash" r={DIAL - 120} />
        <circle className="ln" r={DIAL - 136} />
        {ROMAN.map((r, i) => {
          const [x, y] = polar(DIAL - 82, -90 + i * 30);
          return <text key={r} className="num" x={x} y={y} textAnchor="middle" dominantBaseline="central">{r}</text>;
        })}
        <path className="cl" d={`M${-DIAL - 40} 0H${DIAL + 40}M0 ${-DIAL - 40}V${DIAL + 40}`} />
      </g>
      {/* caption above the dial, under the header, clear of the reading column */}
      <path className="lead" d={`M${C.x + DIAL_PT[0]} ${C.y + DIAL_PT[1]}L${C.x - 300} ${C.y - DIAL - 10}H${C.x + 120}`} />
      <circle className="lead-dot" cx={C.x + DIAL_PT[0]} cy={C.y + DIAL_PT[1]} r={2.6} />
      <text className="cap" x={C.x - 292} y={C.y - DIAL - 22}>FIG. 4 — CLOCK MOVEMENT · LOCAL TIME</text>
      <text className="cap sub" x={C.x - 292} y={C.y - DIAL + 14}>{`${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(s)}`}</text>
    </g>
  );
});

/** The clock's moving parts at the time; they sweep unless the sheet is still or motion is reduced. */
function ClockParts({ still, layer }: { still: boolean; layer: 'train' | 'hands' }) {
  const reduced = useReducedMotion();
  const run = !still && !reduced;
  const now = useSecond(still || run);
  const epoch = useSweepEpoch(run);
  return <Parts parts={layer === 'train' ? TRAIN : HANDS} at={run ? epoch : now} run={run} />;
}

const LORENZ = lorenzPath();
const L = { x: 1500, y: 620 };
const EQ = { x: 1160, y: 156 };
/* the left fixed point, −√(β(ρ−1)), in plate units */
const FP = f1(-Math.sqrt((8 / 3) * 27) * 13);

const Butterfly = memo(function Butterfly() {
  return (
    <g className="ds-fig ds-lorenz">
      <g transform={`translate(${L.x} ${L.y})`}>
        <path className="cl" d="M-330 0H330M0 -360V360" />
        <path className="orbit" pathLength={1} d={LORENZ} />
        {/* the two fixed points the orbit circles, ±√(β(ρ−1)) */}
        {[-1, 1].map((sg) => (
          <g key={sg} transform={`translate(${f1(sg * Math.sqrt((8 / 3) * 27) * 13)} ${f1(-(27 - 25) * 13)})`}>
            <circle className="ln" r={4} /><path className="ln" d="M-12 0H12M0 -12V12" />
            <text className="ax" x={sg < 0 ? -34 : 16} y={-16}>C<tspan dy={-9} className="sup">{sg < 0 ? '−' : '+'}</tspan></text>
          </g>
        ))}
        <text className="ax" x={342} y={7}>x</text>
        <text className="ax" x={12} y={-298}>z</text>
        <path className="tk" d={arc(372, 200, 340)} />
      </g>
      {/* title and equations at the head of the stage, clear of the reading column; the leader goes to a fixed point */}
      <path className="lead" d={`M${EQ.x - 8} ${EQ.y - 18}H${EQ.x + 380}L${L.x + FP} ${L.y - 26}`} />
      <circle className="lead-dot" cx={L.x + FP} cy={L.y - 26} r={2.6} />
      <text className="cap" x={EQ.x} y={EQ.y - 30}>FIG. 4 — LORENZ ATTRACTOR</text>
      <text className="cap sub" x={EQ.x} y={EQ.y + 6}>{`β WORLDLINE · ${DIVERGENCE.beta}`}</text>
      <text className="cap sub" x={EQ.x} y={EQ.y + 30}>THE BUTTERFLY EFFECT · E. N. LORENZ, 1963</text>
      <g className="eq" transform={`translate(${EQ.x} ${EQ.y + 86})`}>
        <text y={0}><tspan className="v">ẋ</tspan> = σ ( y − x )</text>
        <text y={40}><tspan className="v">ẏ</tspan> = x ( ρ − z ) − y</text>
        <text y={80}><tspan className="v">ż</tspan> = x y − β z</text>
        <text y={120} className="small">σ = 10,  ρ = 28,  β = 8⁄3</text>
        <path className="ln" d="M-14 -30V130" />
      </g>
    </g>
  );
});

/*
 * Sensitive dependence, shown rather than told: two points integrated on the attractor live, from starts 10⁻⁴ apart
 * in x. They run as one for about a minute, then come apart; a while later both fade and set out again from a new
 * point. One is the β sheet's amber, the other the silver of Steins;Gate. Not run under reduced motion; the frame
 * loop stops by itself while the window is hidden.
 */
const RATE = 0.2;            // time units of the system per second
const H = 0.004;             // integration step
const TAIL = 110;            // points kept in each tail (one a frame)
const NEAR = 36;             // the stronger stretch behind the head
const RUN = 150;             // seconds from one start to the next
type V3 = [number, number, number];
const lorenz = ([x, y, z]: V3): V3 => [10 * (y - x), x * (28 - z) - y, x * y - (8 / 3) * z];
function rk4(p: V3, h: number): V3 {
  const k1 = lorenz(p);
  const k2 = lorenz([p[0] + (h / 2) * k1[0], p[1] + (h / 2) * k1[1], p[2] + (h / 2) * k1[2]]);
  const k3 = lorenz([p[0] + (h / 2) * k2[0], p[1] + (h / 2) * k2[1], p[2] + (h / 2) * k2[2]]);
  const k4 = lorenz([p[0] + h * k3[0], p[1] + h * k3[1], p[2] + h * k3[2]]);
  return [0, 1, 2].map((i) => p[i] + (h / 6) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i])) as V3;
}
function startPoint(): V3 {
  let p: V3 = [Math.random() * 2 - 1, Math.random() * 2 - 1, 20 + Math.random() * 5];
  for (let i = 0; i < 1500; i++) p = rk4(p, 0.01);
  return p;
}
const plot = (p: V3) => `${f1(p[0] * 13)},${f1(-(p[2] - 25) * 13)}`;

function Divergence({ still }: { still: boolean }) {
  const ref = useRef<SVGGElement>(null);
  const [moving, setRun] = useState(() => !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  const run = moving && !still;
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (!mq) return;
    const on = () => setRun(!mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  useEffect(() => {
    const g = ref.current;
    if (!run || !g) return;
    const tails = g.querySelectorAll<SVGPolylineElement>('polyline');
    const heads = g.querySelectorAll<SVGCircleElement>('circle');
    let a: V3, b: V3, ta: string[], tb: string[], born = 0, last = 0, owed = 0, raf = 0;
    const reset = (now: number) => {
      a = startPoint(); b = [a[0] + 1e-4, a[1], a[2]]; ta = []; tb = []; born = now; owed = 0;
    };
    const frame = (now: number) => {
      if (!last || now - last > 500) { last = now; if (!born) reset(now); }
      owed += ((now - last) / 1000) * RATE; last = now;
      while (owed >= H) { a = rk4(a, H); b = rk4(b, H); owed -= H; }
      ta.push(plot(a)); tb.push(plot(b));
      if (ta.length > TAIL) { ta.shift(); tb.shift(); }
      const age = (now - born) / 1000;
      const fade = Math.min(1, age / 3, Math.max(0, (RUN - age) / 4));
      g.style.opacity = String(fade);
      // each tail twice: the whole of it faint, its last stretch stronger
      tails[0].setAttribute('points', ta.join(' ')); tails[1].setAttribute('points', ta.slice(-NEAR).join(' '));
      tails[2].setAttribute('points', tb.join(' ')); tails[3].setAttribute('points', tb.slice(-NEAR).join(' '));
      const [ax, ay] = ta[ta.length - 1].split(','), [bx, by] = tb[tb.length - 1].split(',');
      heads[0].setAttribute('cx', ax); heads[0].setAttribute('cy', ay);
      heads[1].setAttribute('cx', bx); heads[1].setAttribute('cy', by);
      if (age > RUN) reset(now);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [run]);
  if (!run) return null;
  return (
    <svg className="ds-plate ds-pair" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMin slice">
      <g ref={ref} transform={`translate(${L.x} ${L.y})`} style={{ opacity: 0 }}>
        <polyline className="tail sg" /><polyline className="tail sg near" />
        <polyline className="tail beta" /><polyline className="tail beta near" />
        <circle className="head sg" r={3.2} /><circle className="head beta" r={3.6} />
      </g>
    </svg>
  );
}

/*
 * The other worldline's figure, erased: drawn once, faint, then masked by a generated eraser texture (white where it
 * was rubbed hardest, assets/PROVENANCE.md), with the graphite the eraser smeared left as a haze. Static, in its own
 * SVG so the ticking clock never repaints it. The sheet earlier held the drawing a little off from where the new one sits.
 */
const RUB = { lorenz: { x: 1000, y: 190, w: 1000, h: 800 }, clock: { x: 860, y: 60, w: 1400, h: 1020 } };
const Trace = memo(function Trace({ of }: { of: 'lorenz' | 'clock' }) {
  const r = RUB[of];
  const eraser = <image href="/assets/sheet/eraser.webp" x={r.x} y={r.y} width={r.w} height={r.h} preserveAspectRatio="none" />;
  return (
    <svg className="ds-plate ds-trace" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMin slice">
      <defs>
        <filter id="ds-rub-ink" colorInterpolationFilters="sRGB"><feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1.6 0" /></filter>
        <mask id="ds-rub" maskUnits="userSpaceOnUse" x={0} y={0} width={1920} height={1080}>
          <rect width={1920} height={1080} fill="#fff" />
          <g filter="url(#ds-rub-ink)">{eraser}</g>
        </mask>
      </defs>
      <g className="smear">{eraser}</g>
      <g mask="url(#ds-rub)">
        {of === 'lorenz' ? (
          <g transform="translate(1470 600) rotate(-7)">
            <path className="cl" d="M-330 0H330M0 -360V360" />
            <path className="orbit" d={LORENZ} />
          </g>
        ) : (
          <g transform={`translate(${C.x + 30} ${C.y - 24})`}>
            <circle className="ln" r={DIAL} /><circle className="ln" r={DIAL - 16} />
            <path className="tk" d={ticks(60, DIAL - 16, DIAL - 30, 5, DIAL - 44)} />
            <circle className="ln" r={DIAL - 136} />
            {/* no numerals: set type does not rub out like pencil, and a second dial would read as a clock */}
            <path className="ln" d={CENTER_WHEEL} />
            <g transform={`translate(${ESC.at[0]} ${ESC.at[1]})`}><path className="ln" d={ESC_WHEEL} /></g>
          </g>
        )}
      </g>
    </svg>
  );
});

/* Specimens pinned about the β sheet: most round the figure, two faint ones behind the reading column. */
const MOTHS: { n: number; at: CSSProperties; w: string; r: number; o: number }[] = [
  { n: 1, at: { right: '4%', top: '11%' }, w: 'clamp(130px, 11.5vw, 210px)', r: -10, o: .34 },
  { n: 6, at: { right: '2%', bottom: '25%' }, w: 'clamp(120px, 11vw, 200px)', r: 8, o: .28 },
  { n: 4, at: { right: '29%', bottom: '6%' }, w: 'clamp(110px, 9vw, 170px)', r: 16, o: .2 },
  { n: 3, at: { right: '31%', top: '30%' }, w: 'clamp(90px, 7vw, 130px)', r: -22, o: .3 },
  { n: 2, at: { left: '23%', top: '15%' }, w: 'clamp(90px, 7.5vw, 140px)', r: -26, o: .07 },
  { n: 5, at: { left: '5%', bottom: '9%' }, w: 'clamp(90px, 8vw, 140px)', r: -6, o: .1 },
];

const ROWS = ['A', 'B', 'C', 'D', 'E', 'F'];
const COLS = [1, 2, 3, 4, 5, 6, 7, 8];

export function DraftSheet({ worldline, still = false }: { worldline: 'steins_gate' | 'beta'; still?: boolean }) {
  return (
    <div className={`ds-sheet ds-${worldline === 'beta' ? 'beta' : 'sg'}`} aria-hidden="true">
      <div className="ds-paper" />
      <div className="ds-grid" />
      <Trace of={worldline === 'beta' ? 'clock' : 'lorenz'} />
      {worldline !== 'beta' && <ClockParts still={still} layer="train" />}
      <svg className="ds-plate" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMin slice">
        {worldline === 'beta' ? <Butterfly /> : <Movement still={still} />}
      </svg>
      {worldline !== 'beta' && <ClockParts still={still} layer="hands" />}
      {worldline === 'beta' && <Divergence still={still} />}
      {/* one soda-can ring left on the SG sheet, by the clock */}
      {worldline !== 'beta' && <img className="ds-ring" src="/assets/sheet/ring-3.webp" alt="" draggable={false} decoding="async" />}
      {worldline === 'beta' && (
        <div className="ds-moths">
          {MOTHS.map((m) => (
            <img key={m.n} src={`/assets/sheet/specimen-${m.n}.webp`} alt="" draggable={false} decoding="async"
              style={{ ...m.at, width: m.w, '--r': `${m.r}deg`, '--o': m.o } as CSSProperties} />
          ))}
        </div>
      )}
      {/* the sheet's border, as on the login; the header band is the margin above it */}
      <div className="ds-frame" />
      <div className="ds-zones">
        <div className="z left">{ROWS.map((n) => <span key={n}>{n}</span>)}</div>
        <div className="z right">{ROWS.map((n) => <span key={n}>{n}</span>)}</div>
        <div className="z bottom">{COLS.map((n) => <span key={n}>{n}</span>)}</div>
      </div>
    </div>
  );
}
