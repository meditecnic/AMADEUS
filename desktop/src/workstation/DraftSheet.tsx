import { memo, useEffect, useRef, useState, type CSSProperties } from 'react';
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

function useSecond() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

const pad = (n: number) => String(n).padStart(2, '0');

const Movement = memo(function Movement() {
  const now = useSecond();
  const s = now.getSeconds();
  const m = now.getMinutes() + s / 60;
  const h = (now.getHours() % 12) + m / 60;
  return (
    <g className="ds-fig ds-clock">
      {/* going train, behind the dial */}
      <g transform={`translate(${C.x} ${C.y})`}>
        <path className="dash" d={`M0 0L${THIRD.at[0]} ${THIRD.at[1]}L${ESC.at[0]} ${ESC.at[1]}`} />
        <g transform={`rotate(${f1(m * 6 * 0.25)})`}><path className="ln" pathLength={1} style={dly(200)} d={CENTER_WHEEL} /><path className="ln" pathLength={1} style={dly(500)} d={CW_SPOKES} /></g>
        <g transform={`translate(${THIRD.at[0]} ${THIRD.at[1]}) rotate(${f1(-m * 6 * 1.5)})`}>
          <path className="ln" pathLength={1} style={dly(320)} d={THIRD_WHEEL} />
          <circle className="ln" r={22} /><circle className="ln" r={7} />
        </g>
        {/* the escape wheel steps one tooth's half every second, as the seconds hand does */}
        <g transform={`translate(${ESC.at[0]} ${ESC.at[1]})`}>
          <g className="ds-esc" transform={`rotate(${s * 6})`}>
            <path className="ln strong" pathLength={1} style={dly(420)} d={ESC_WHEEL} />
            <path className="ln" d={ESC_SPOKES} />
            <circle className="ln" r={16} /><circle className="ln" r={5} />
          </g>
        </g>
      </g>
      {/* the anchor rocks once a second */}
      <g transform={`translate(${C.x + ANCHOR_PIVOT.x} ${C.y + ANCHOR_PIVOT.y})`}>
        <g className="ds-anchor" transform={`rotate(${s % 2 ? 3 : -3})`}>
          <path className="ln strong" pathLength={1} style={dly(600)} d={ANCHOR} />
          <circle className="ln" r={9} /><circle className="ln" r={3} />
        </g>
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
        {/* hands: local time */}
        <g transform={`rotate(${f1(h * 30)})`}><path className="hand" d="M-7 34L-4 -150L0 -196L4 -150L7 34Z" /></g>
        <g transform={`rotate(${f1(m * 6)})`}><path className="hand" d="M-5 40L-2.5 -290L0 -318L2.5 -290L5 40Z" /></g>
        <g transform={`rotate(${s * 6})`}><path className="hand sec" d="M0 70V-372" /><circle className="hand sec" cy={52} r={10} /></g>
        <circle className="hub" r={11} />
      </g>
      {/* caption above the dial, under the header, clear of the reading column */}
      <path className="lead" d={`M${C.x + DIAL_PT[0]} ${C.y + DIAL_PT[1]}L${C.x - 300} ${C.y - DIAL - 40}H${C.x + 120}`} />
      <circle className="lead-dot" cx={C.x + DIAL_PT[0]} cy={C.y + DIAL_PT[1]} r={2.6} />
      <text className="cap" x={C.x - 292} y={C.y - DIAL - 52}>FIG. 4 — CLOCK MOVEMENT · LOCAL TIME</text>
      <text className="cap sub" x={C.x - 292} y={C.y - DIAL - 16}>{`${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(s)}`}</text>
    </g>
  );
});

const LORENZ = lorenzPath();
const L = { x: 1500, y: 620 };
const EQ = { x: 1160, y: 132 };
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

function Divergence() {
  const ref = useRef<SVGGElement>(null);
  const [run, setRun] = useState(() => !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
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
    <svg className="ds-plate ds-pair" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
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
    <svg className="ds-plate ds-trace" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
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

export function DraftSheet({ worldline }: { worldline: 'steins_gate' | 'beta' }) {
  return (
    <div className={`ds-sheet ds-${worldline === 'beta' ? 'beta' : 'sg'}`} aria-hidden="true">
      <div className="ds-paper" />
      <div className="ds-grid" />
      <Trace of={worldline === 'beta' ? 'clock' : 'lorenz'} />
      <svg className="ds-plate" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
        {worldline === 'beta' ? <Butterfly /> : <Movement />}
      </svg>
      {worldline === 'beta' && <Divergence />}
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
      <div className="ds-zones">
        <div className="z right">{ROWS.map((n) => <span key={n}>{n}</span>)}</div>
        <div className="z bottom">{COLS.map((n) => <span key={n}>{n}</span>)}</div>
      </div>
    </div>
  );
}
