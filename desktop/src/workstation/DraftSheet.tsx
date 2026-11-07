import { memo, useEffect, useState, type CSSProperties } from 'react';
import { DIVERGENCE } from './Nixie';
import { arc, escapeWheel, f1, gearOutline, lorenzPath, polar, ticks } from './drafting';

/*
 * The workstation is the same drafting sheet as the login (design/references.md §4): the paper, its grid and zone
 * border, and one large figure on the right that runs off the sheet. Each worldline has its own figure, and each
 * figure means something:
 *  - Steins;Gate: a clock movement — escape wheel, anchor, going train and dial. The dial shows the local time;
 *    the escape wheel steps once a second.
 *  - β: the Lorenz attractor (the butterfly), with its equations written beside it as on a blackboard.
 * Drawn here, not traced from anything. 1920 × 1080 user space, sliced to the window like the login plate.
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
          </g>
        ))}
        <path className="tk" d={arc(372, 200, 340)} />
      </g>
      {/* title and equations at the head of the stage, clear of the reading column; the leader goes to a fixed point */}
      <path className="lead" d={`M${EQ.x - 8} ${EQ.y - 18}H${EQ.x + 380}L${L.x + FP} ${L.y - 26}`} />
      <circle className="lead-dot" cx={L.x + FP} cy={L.y - 26} r={2.6} />
      <text className="cap" x={EQ.x} y={EQ.y - 30}>FIG. 4 — LORENZ ATTRACTOR</text>
      <text className="cap sub" x={EQ.x} y={EQ.y + 6}>{`β WORLDLINE · ${DIVERGENCE.beta}`}</text>
      <g className="eq" transform={`translate(${EQ.x} ${EQ.y + 62})`}>
        <text y={0}><tspan className="v">ẋ</tspan> = σ ( y − x )</text>
        <text y={40}><tspan className="v">ẏ</tspan> = x ( ρ − z ) − y</text>
        <text y={80}><tspan className="v">ż</tspan> = x y − β z</text>
        <text y={120} className="small">σ = 10,  ρ = 28,  β = 8⁄3</text>
        <path className="ln" d="M-14 -30V130" />
      </g>
    </g>
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
      <svg className="ds-plate" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
        {worldline === 'beta' ? <Butterfly /> : <Movement />}
      </svg>
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
