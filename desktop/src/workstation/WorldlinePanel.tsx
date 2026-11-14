import { memo, useEffect, useState } from 'react';
import { escapeWheel, f1, gearOutline, lorenzPath, polar, ticks } from './drafting';
import { DIVERGENCE, Nixie, WORLDLINE_NAME, useTunedValue } from './Nixie';
import { Stamp } from './Stamp';
import { shortDate, type Ledger, type Worldline } from './worldlineLedger';

/*
 * 01 the worldline. U 11-09 turned down a plotted chart (草率) and, before it, neon curves (太塑料). Here each line is a
 * card cut from its own sheet, so the page shows the two places rather than a graph of them:
 *  - STEINS;GATE: slate, its clock movement at the local time, taped to the sheet.
 *  - β: dark metal in an amber double rule, the Lorenz attractor and a pinned moth.
 * Both cards keep the same parts in the same places: the name, the reading on eight tubes, a title block of what the
 * line holds (conversations, memories, last used) and its key. The line you are on is lit and stamped 当前. The other
 * card is cold glass; reaching for it (pointer over the card, or focus on its key) warms it and tunes its tubes up to
 * its reading while yours dim. Its key shifts. There is no pick-then-confirm step: the key names where it goes.
 */

const LINES: Worldline[] = ['steins_gate', 'beta'];
const PLATE = { steins_gate: 'PL. I', beta: 'PL. II' } as const;
const count = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(n));
const pad = (n: number) => String(n).padStart(2, '0');

/** The time to the minute, for the clock on the STEINS;GATE card. */
function useMinute() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let id = 0;
    const tick = () => { setNow(new Date()); id = window.setTimeout(tick, 60_000 - (Date.now() % 60_000) + 20); };
    id = window.setTimeout(tick, 60_000 - (Date.now() % 60_000) + 20);
    return () => window.clearTimeout(id);
  }, []);
  return now;
}

/* The clock movement, as on the STEINS;GATE sheet, drawn whole at the card's size: the dial at the local time, and the
   going train beside it (escape wheel under its anchor, a third wheel). 300 × 200 user space. */
const DIAL = { x: 196, y: 100, r: 82 };
const ROMAN = ['XII', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];
const ESC = { x: 64, y: 140, r: 30 };
const ESC_WHEEL = escapeWheel(20, ESC.r);
const ESC_SPOKES = [0, 72, 144, 216, 288].map((a) => {
  const [x0, y0] = polar(6, a);
  const [x1, y1] = polar(ESC.r * 0.8, a);
  return `M${x0} ${y0}L${x1} ${y1}`;
}).join('');
/* the anchor: two pallets in the teeth, its arms up to the pivot, the crutch above */
const ANCHOR = (() => {
  const [lx, ly] = polar(ESC.r + 3, -124);
  const [rx, ry] = polar(ESC.r + 3, -56);
  const [lx2, ly2] = polar(ESC.r - 7, -118);
  const [rx2, ry2] = polar(ESC.r - 7, -62);
  return `M${lx} ${ly}L0 -58L${rx} ${ry}M${lx} ${ly}L${lx2} ${ly2}M${rx} ${ry}L${rx2} ${ry2}M${lx} ${ly}Q0 -40 ${rx} ${ry}M0 -58V-86`;
})();
const THIRD = { x: 122, y: 62, r: 22 };
const THIRD_WHEEL = gearOutline(28, THIRD.r);
const DIAL_TICKS = ticks(60, DIAL.r - 6, DIAL.r - 11, 5, DIAL.r - 15);

const Clock = memo(function Clock({ at }: { at: Date }) {
  const m = at.getMinutes();
  const h = (at.getHours() % 12) + m / 60;
  return (
    <svg className="fig clock" viewBox="0 0 300 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <path className="dash" d={`M${ESC.x} ${ESC.y}L${THIRD.x} ${THIRD.y}L${DIAL.x} ${DIAL.y}`} />
      <g transform={`translate(${ESC.x} ${ESC.y})`}>
        <g transform={`rotate(${(m * 6) % 18})`}>
          <path className="ln" d={ESC_WHEEL} pathLength={1} />
          <path className="ln fine" d={ESC_SPOKES} />
          <circle className="ln" r={6} /><circle className="ln" r={2} />
        </g>
        <path className="ln" d={ANCHOR} pathLength={1} />
        <circle className="ln" cy={-58} r={3.5} />
      </g>
      <g transform={`translate(${THIRD.x} ${THIRD.y}) rotate(${f1(-m * 9)})`}>
        <path className="ln" d={THIRD_WHEEL} pathLength={1} />
        <circle className="ln fine" r={8} /><circle className="ln" r={2.5} />
      </g>
      <g transform={`translate(${DIAL.x} ${DIAL.y})`}>
        <circle className="ln" r={DIAL.r} pathLength={1} transform="rotate(-90)" />
        <circle className="ln fine" r={DIAL.r - 6} />
        <path className="tk" d={DIAL_TICKS} />
        <circle className="dash" r={DIAL.r - 40} />
        {ROMAN.map((r, i) => {
          const [x, y] = polar(DIAL.r - 26, -90 + i * 30);
          return <text key={r} className="num" x={x} y={y} textAnchor="middle" dominantBaseline="central">{r}</text>;
        })}
        <path className="cl" d={`M${-DIAL.r - 10} 0H${DIAL.r + 10}M0 ${-DIAL.r - 10}V${DIAL.r + 10}`} />
        <g transform={`rotate(${f1(h * 30)})`}><path className="hand" d="M-3 9L-1.6 -36L0 -46L1.6 -36L3 9Z" /></g>
        <g transform={`rotate(${m * 6})`}><path className="hand" d="M-2.2 11L-1.1 -56L0 -66L1.1 -56L2.2 11Z" /></g>
        <circle className="hub" r={3.2} />
      </g>
    </svg>
  );
});

/* The Lorenz attractor, as on the β sheet, whole on the card: the butterfly and its two fixed points. */
const K = 4.2;
const LORENZ = lorenzPath(3000, 0.0075, K);
const FIXED = Math.sqrt((8 / 3) * 27) * K;
const BUTTERFLY = { x: 168, y: 100 };

const Attractor = memo(function Attractor() {
  return (
    <svg className="fig lorenz" viewBox="0 0 300 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g transform={`translate(${BUTTERFLY.x} ${BUTTERFLY.y})`}>
        <path className="cl" d="M-118 0H118M0 -104V104" />
        <path className="orbit" d={LORENZ} pathLength={1} />
        {[-1, 1].map((s) => (
          <g key={s} transform={`translate(${f1(s * FIXED)} ${f1(-2 * K)})`}>
            <circle className="ln" r={2.4} /><path className="ln fine" d="M-7 0H7M0 -7V7" />
          </g>
        ))}
      </g>
    </svg>
  );
});

function LineCard({ line, current, aim, busy, ledger, onAim, onShift }: {
  line: Worldline;
  current: Worldline;
  aim: boolean;
  busy: boolean;
  ledger: Ledger[Worldline];
  onAim: (on: boolean) => void;
  onShift: () => void;
}) {
  const here = line === current;
  const reading = DIVERGENCE[line];
  // A cold card's tubes hold the reading you are on, dark, and tune up to their own when you reach for them.
  const tubes = useTunedValue(here || aim ? reading : DIVERGENCE[current], { duration: 760 });
  const now = useMinute();
  const name = WORLDLINE_NAME[line];
  const cls = line === 'beta' ? 'beta' : 'sg';
  return (
    <article
      className={`ws2-wlc ${cls}${here ? ' cur' : ''}${aim ? ' aim' : ''}`}
      aria-label={`${name} 世界线${here ? '（当前）' : ''}`}
      onPointerEnter={here ? undefined : () => onAim(true)}
      onPointerLeave={here ? undefined : () => onAim(false)}
    >
      <div className="face">
        <div className="plate">
          {line === 'beta' ? <Attractor /> : <Clock at={now} />}
          {line === 'beta' ? <img className="moth" src="/assets/sheet/specimen-2.webp" alt="" draggable={false} decoding="async" /> : null}
          <span className="pl" aria-hidden="true">{PLATE[line]}</span>
          <span className="cap" aria-hidden="true">
            {line === 'beta' ? 'LORENZ ATTRACTOR' : `CLOCK MOVEMENT · ${pad(now.getHours())}:${pad(now.getMinutes())}`}
          </span>
        </div>
        <h3 className="nm">{line === 'beta' ? <>β<small>WORLDLINE</small></> : name}</h3>
        <div className="meter">
          <Nixie value={reading} state={tubes} label={reading} size="sm" blueprint={false} ariaLabel={`${name} 世界线变动率 ${reading}%`} />
          <p className="read" aria-hidden="true"><span>DIVERGENCE</span><span>{reading} %</span></p>
        </div>
        <dl className="block">
          <div><dt>会话</dt><dd>{count(ledger?.conversations)}</dd></div>
          <div><dt>记忆</dt><dd>{count(ledger?.memories)}</dd></div>
          <div><dt>最近</dt><dd>{ledger ? shortDate(ledger.last) : '—'}</dd></div>
        </dl>
        <div className="act">
          {here ? (
            <span className="here"><Stamp className="ws2-wlc-stamp" tilt={-5}>当前所在</Stamp><span className="ws2-sr-only">当前所在</span></span>
          ) : (
            <button type="button" className="ws2-m-btn armed" disabled={busy} onClick={onShift}
              onFocus={() => onAim(true)} onBlur={() => onAim(false)}>
              跃迁至 {name}
            </button>
          )}
        </div>
      </div>
      {/* how each card is held to the sheet: tape on the slate, solder stubs off the metal */}
      {line === 'beta' ? <><i className="stub a" aria-hidden="true" /><i className="stub b" aria-hidden="true" /></> : <i className="tape" aria-hidden="true" />}
    </article>
  );
}

export function WorldlinePanel({ worldline, busy, onShift, ledger = {} }: {
  worldline: Worldline;
  /** A shift is already running. */
  busy: boolean;
  /** The caller decides whether an active reply needs a stop-and-shift prompt first. */
  onShift: (to: Worldline) => void;
  /** What each line holds (conversations, memories, last used). */
  ledger?: Ledger;
}) {
  const [aim, setAim] = useState(false);
  useEffect(() => { setAim(false); }, [worldline]);
  const aiming = aim && !busy;
  const other: Worldline = worldline === 'beta' ? 'steins_gate' : 'beta';
  const delta = Math.abs(Number(DIVERGENCE.beta) - Number(DIVERGENCE.steins_gate)).toFixed(6);
  return (
    <div className={`ws2-wl${aiming ? ' aiming' : ''}`} data-target={other === 'beta' ? 'beta' : 'sg'}>
      <div className="ws2-wl-pair">
        {LINES.map((line) => (
          <LineCard
            key={line}
            line={line}
            current={worldline}
            aim={aiming && line === other}
            busy={busy}
            ledger={ledger[line]}
            onAim={setAim}
            onShift={() => onShift(line)}
          />
        ))}
      </div>
      {/* how far apart the two readings are, dimensioned between the cards as a drawing gives a size */}
      <div className={`ws2-wl-dim${aiming ? ' is-aimed' : ''}`} aria-hidden="true"><span className="d">Δ {delta}</span></div>
      <p className="ws2-m-note">两条世界线各自保存会话、草稿与记忆，互不相通。目标线就绪后才算切换完成。</p>
    </div>
  );
}
