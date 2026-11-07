import { forwardRef, memo, useEffect, useImperativeHandle, useMemo, useRef, type CSSProperties } from 'react';
import { PROBE_AT, PROBE_FIXED, PROBE_SPIN, PROBE_YAW0, probeFrame, type ProbeFrame } from './probe';

/**
 * The drafting plate behind the terminal (1920 × 1080 user space, sliced to the window). The main figure is the
 * satellite, large on the right and running off the sheet; on the left a drive train of three meshing gears and a
 * protractor with a working compass arm, joined by a construction line. Drawn here, not traced from anything.
 * The gears are the terminal's escapement: they tick while idle, ratchet one tooth per keystroke, spin while the
 * password is checked and jam when it is refused. Every caption names something real (design/references.md §3).
 */
export interface BlueprintHandle {
  /** Advance the drive train by n teeth of the large gear (negative turns it back). */
  tick: (n?: number) => void;
  /** A refused password: the train kicks back a tooth and shudders forward again. */
  jam: () => void;
}

interface Gear { cx: number; cy: number; n: number; r: number; phase: number; ratio: number }

const M = 9.556;
/* The train keeps its meshing geometry; it is only moved up into the left corner, the large gear cut by the sheet. */
const TX = 230, TY = 130;
const G1: Gear = { cx: TX, cy: TY, n: 36, r: 172, phase: 112, ratio: 1 };
const G2: Gear = { cx: TX - 100.3, cy: TY + 248.1, n: 20, r: 95.6, phase: 283, ratio: -36 / 20 };
const G3: Gear = { cx: TX + 16.8, cy: TY + 346.4, n: 12, r: 57.3, phase: 220, ratio: 36 / 12 };
const STEP = 360 / G1.n;

const f = (v: number) => Math.round(v * 10) / 10;
const polar = (r: number, deg: number) => {
  const a = (deg * Math.PI) / 180;
  return `${f(r * Math.cos(a))} ${f(r * Math.sin(a))}`;
};

/** Involute-ish outline: straight flanks between root and tip circles, one tooth centred on 0°. */
function gearOutline(n: number, r: number) {
  const ro = r + M * 0.92;
  const rr = r - M * 1.18;
  const s = 360 / n;
  let d = '';
  for (let k = 0; k < n; k++) {
    const a = k * s;
    d += `${k ? 'L' : 'M'}${polar(rr, a - s * 0.5)}L${polar(rr, a - s * 0.29)}L${polar(ro, a - s * 0.15)}L${polar(ro, a + s * 0.15)}L${polar(rr, a + s * 0.29)}`;
  }
  return `${d}Z`;
}

function arc(r: number, from: number, to: number) {
  const large = Math.abs(to - from) > 180 ? 1 : 0;
  return `M${polar(r, from)}A${r} ${r} 0 ${large} 1 ${polar(r, to)}`;
}

const OUT1 = gearOutline(G1.n, G1.r);
const OUT2 = gearOutline(G2.n, G2.r);
const OUT3 = gearOutline(G3.n, G3.r);
const SPOKES1 = Array.from({ length: 6 }, (_, i) => {
  const a = i * 60 + 30;
  return `M${polar(30, a - 9)}L${polar(128, a - 4)}M${polar(30, a + 9)}L${polar(128, a + 4)}`;
}).join('');
const HOLES2 = [45, 135, 225, 315].map((a) => polar(44, a).split(' ').map(Number));

/* Protractor + compass, lower left, below the train. */
const PC = { x: 170, y: 930, r: 318 };
const PTICKS = Array.from({ length: 144 }, (_, i) => {
  const a = i * 2.5;
  const len = i % 12 === 0 ? 20 : i % 2 === 0 ? 11 : 6;
  return `M${polar(PC.r - 22, a)}L${polar(PC.r - 22 - len, a)}`;
}).join('');
const PLABELS = Array.from({ length: 12 }, (_, i) => i * 30);
/* The large gear's caption hangs off its rim, to the right, above the wordmark. */
const G1_LEAD = polar(G1.r + 12, 24).split(' ').map(Number);

const dly = (ms: number) => ({ '--d': `${ms}ms` }) as CSSProperties;

/*
 * The satellite, drawn turned and tilted (see probe.ts). It turns slowly about its own axis: the parts that are not
 * solids of revolution are re-projected about 30 times a second, straight into the paths (no React render per
 * frame). Around the form it is masked down so the fields stay legible. Reduced motion keeps the still drawing.
 */
const FRAME_KEYS = ['faces', 'hidden', 'body', 'marks', 'grille', 'struts', 'booms', 'wings', 'cells', 'strips', 'axis'] as const;
const SPIN_DELAY = 1600;
const SPIN_RAMP = 2400;

const Probe = memo(function Probe({ spin }: { spin: boolean }) {
  const first = useMemo(() => probeFrame(PROBE_YAW0), []);
  const els = useRef<Partial<Record<keyof ProbeFrame, SVGPathElement | null>>>({});

  useEffect(() => {
    if (!spin || typeof requestAnimationFrame !== 'function') return;
    let raf = 0;
    let t0 = 0;
    let last = 0;
    let drawn = 0;
    let yaw = PROBE_YAW0;
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      if (!t0) { t0 = last = drawn = now; return; }
      /* eases in once the sheet has been drawn; a long gap (hidden window) does not jump the turn */
      const k = Math.min(1, Math.max(0, (now - t0 - SPIN_DELAY) / SPIN_RAMP));
      yaw += (Math.min(now - last, 100) / 1000) * PROBE_SPIN * k * k * (3 - 2 * k);
      last = now;
      if (k === 0 || now - drawn < 33) return;
      drawn = now;
      yaw %= 360;
      const fr = probeFrame(yaw);
      for (const key of FRAME_KEYS) els.current[key]?.setAttribute('d', fr[key]);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [spin]);

  const at = (key: keyof ProbeFrame) => (el: SVGPathElement | null) => { els.current[key] = el; };
  const F = PROBE_FIXED;
  return (
    <g className="bp-probe">
      <g mask="url(#bp-clear)">
        <g className="bp-drift" style={{ transformOrigin: `${PROBE_AT.x}px ${PROBE_AT.y}px` }}>
          <path ref={at('faces')} className="face" d={first.faces} />
          <path className="cl" d={F.axis} />
          <path ref={at('axis')} className="cl" d={first.axis} />
          <path ref={at('hidden')} className="dash" d={first.hidden} />
          <path ref={at('body')} className="ln" pathLength={1} style={dly(300)} d={first.body} />
          <path ref={at('marks')} className="ln" pathLength={1} style={dly(520)} d={first.marks} />
          <path ref={at('grille')} className="tk" d={first.grille} />
          <path className="ln" pathLength={1} style={dly(380)} d={F.mast + F.dish} />
          <path className="ln" pathLength={1} style={dly(440)} d={F.rim} />
          <path className="ln" pathLength={1} style={dly(640)} d={F.rimInner} />
          <path ref={at('struts')} className="ln" pathLength={1} style={dly(700)} d={first.struts} />
          <path className="ln" pathLength={1} style={dly(700)} d={F.feed} />
          <path className="ln" pathLength={1} style={dly(560)} d={F.nozzle} />
          <path ref={at('booms')} className="ln" pathLength={1} style={dly(600)} d={first.booms} />
          <path ref={at('wings')} className="ln face" pathLength={1} style={dly(420)} d={first.wings} />
          <path ref={at('cells')} className="ln" pathLength={1} style={dly(760)} d={first.cells} />
          <path ref={at('strips')} className="tk" d={first.strips} />
          {/* the sense of turn */}
          <path className="dash" d={F.spin} />
          <path className="ln" pathLength={1} style={dly(900)} d={F.arrow} />
        </g>
      </g>
      {/* The figure's caption: the day the satellite came down on Akihabara in the original story. */}
      <path className="lead" d={`M${F.head.x} ${F.head.y}l-40 -16H${F.head.x - 520}`} />
      <circle className="lead-dot" cx={F.head.x} cy={F.head.y} r={2.6} />
      <text className="cap" x={F.head.x - 512} y={F.head.y - 26}>FIG. 1 — ARTIFICIAL SATELLITE</text>
      <text className="cap sub" x={F.head.x - 512} y={F.head.y + 8}>AKIHABARA · 2010.07.28</text>
    </g>
  );
});

export const Blueprint = forwardRef<BlueprintHandle, { reduced: boolean; busy: boolean; divergence: string }>(function Blueprint({ reduced, busy, divergence }, ref) {
  const gears = useRef<(SVGGElement | null)[]>([]);
  const teeth = useRef(0);

  const place = (fast = false) => {
    [G1, G2, G3].forEach((g, i) => {
      const el = gears.current[i];
      if (!el) return;
      el.style.transitionDuration = fast ? '.12s' : '';
      el.style.transform = `translate(${g.cx}px, ${g.cy}px) rotate(${f(g.phase + teeth.current * STEP * g.ratio)}deg)`;
    });
  };

  useImperativeHandle(ref, () => ({
    tick: (n = 1) => {
      if (reduced) return;
      teeth.current += n;
      place();
    },
    jam: () => {
      if (reduced) return;
      teeth.current -= 1;
      place(true);
      window.setTimeout(() => { teeth.current += 1; place(); }, 130);
    },
  }), [reduced]);

  useEffect(() => { place(); }, []);

  // The escapement: a calm tick while idle, a fast run while the terminal is checking.
  useEffect(() => {
    if (reduced) return;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      teeth.current += 1;
      place();
    }, busy ? 170 : 1600);
    return () => window.clearInterval(id);
  }, [busy, reduced]);

  const gearRef = (i: number) => (el: SVGGElement | null) => { gears.current[i] = el; };

  return (
    <svg className="bp-plate" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        {/* The sheet's grid, projected onto the model's faces as on a lit drafting model. */}
        <pattern id="bp-mesh" width="16" height="16" patternUnits="userSpaceOnUse">
          <rect width="16" height="16" fill="rgba(241, 240, 235, .035)" />
          <path d="M16 0H0V16" fill="none" stroke="rgba(241, 240, 235, .16)" strokeWidth=".8" />
        </pattern>
        {/* The satellite gives way around the wordmark and the fields: it stays behind them, never through them. */}
        <radialGradient id="bp-clear-g" cx="960" cy="560" r="560" gradientUnits="userSpaceOnUse" gradientTransform="translate(960 560) scale(1 .5) translate(-960 -560)">
          <stop offset="0" stopColor="#fff" stopOpacity=".22" />
          <stop offset=".62" stopColor="#fff" stopOpacity=".4" />
          <stop offset="1" stopColor="#fff" stopOpacity="1" />
        </radialGradient>
        <mask id="bp-clear" maskUnits="userSpaceOnUse" x="-200" y="-200" width="2320" height="1480">
          <rect x="-200" y="-200" width="2320" height="1480" fill="url(#bp-clear-g)" />
        </mask>
      </defs>

      {/* centre lines and construction */}
      <path className="cl" d="M0 540H1920M960 0V1080" />
      <g className="bp-cons">
        <path className="dash" d={`M${PC.x} ${PC.y}L${G3.cx} ${G3.cy}`} />
        <path className="dash" d={`M${G1.cx} ${G1.cy}L${PROBE_AT.x} ${PROBE_AT.y}`} />
      </g>
      <g className="bp-marks">
        {[[720, 980], [1210, 120]].map(([x, y], i) => (
          <path key={i} className="ln" pathLength={1} style={dly(700 + i * 70)} d={`M${x - 9} ${y}h18M${x} ${y - 9}v18`} />
        ))}
      </g>

      <Probe spin={!reduced} />

      {/* protractor and the compass that keeps drawing it */}
      <g className="bp-pro" transform={`translate(${PC.x} ${PC.y})`}>
        <circle className="ln" pathLength={1} style={dly(160)} r={PC.r} transform="rotate(-90)" />
        <circle className="ln" pathLength={1} style={dly(260)} r={PC.r - 22} transform="rotate(-90)" />
        <path className="tk" d={PTICKS} />
        <circle className="dash" r={PC.r * 0.62} />
        <circle className="ln" pathLength={1} style={dly(420)} r={34} />
        <path className="ln" pathLength={1} style={dly(300)} d={`M${-PC.r - 46} 0H${PC.r + 46}M0 ${-PC.r - 46}V${PC.r + 46}`} />
        {PLABELS.map((a) => {
          const [x, y] = polar(PC.r + 17, a - 90).split(' ').map(Number);
          return <text key={a} className="lab" x={x} y={y} textAnchor="middle" dominantBaseline="middle">{a}</text>;
        })}
        <g className="bp-arm">
          <path d={arc(PC.r, -34, -1)} className="trail t1" />
          <path d={arc(PC.r, -14, -1)} className="trail t2" />
          <path d={`M0 0L${polar(PC.r + 10, 0)}`} className="leg" />
          <circle cx={PC.r} cy={0} r={3.2} className="tip" />
          <circle r={5} className="pivot" />
        </g>
      </g>
      {/* the worldline the terminal opens on (1.048596 for Steins;Gate, 1.129848 for β) */}
      <path className="lead" d={`M${PC.x + PC.r + 52} ${PC.y - 54}H${PC.x + PC.r + 470}`} />
      <text className="cap" x={PC.x + PC.r + 60} y={PC.y - 66}>FIG. 3 — DIVERGENCE</text>
      <text className="cap val" x={PC.x + PC.r + 60} y={PC.y - 22}>{divergence}</text>

      {/* drive train */}
      <g className="bp-train">
        <circle className="dash" cx={G1.cx} cy={G1.cy} r={G1.r} />
        <circle className="dash" cx={G2.cx} cy={G2.cy} r={G2.r} />
        <circle className="dash" cx={G3.cx} cy={G3.cy} r={G3.r} />
        <path className="ln" pathLength={1} style={dly(900)} d={`M${G1.cx} ${G1.cy}L${G2.cx} ${G2.cy}L${G3.cx} ${G3.cy}`} />
        <g ref={gearRef(0)} className="bp-gear">
          <path className="ln" pathLength={1} style={dly(320)} d={OUT1} />
          <circle className="ln" pathLength={1} style={dly(520)} r={132} />
          <path className="ln" pathLength={1} style={dly(700)} d={SPOKES1} />
          <circle className="ln" pathLength={1} style={dly(760)} r={30} />
          <circle className="ln" pathLength={1} style={dly(800)} r={12} />
          <path className="ln" pathLength={1} style={dly(820)} d="M-7 -12h14v6h-14z" />
        </g>
        <g ref={gearRef(1)} className="bp-gear">
          <path className="ln" pathLength={1} style={dly(480)} d={OUT2} />
          <circle className="ln" pathLength={1} style={dly(640)} r={72} />
          {HOLES2.map(([x, y], i) => <circle key={i} className="ln" pathLength={1} style={dly(760 + i * 40)} cx={x} cy={y} r={15} />)}
          <circle className="ln" pathLength={1} style={dly(860)} r={11} />
        </g>
        <g ref={gearRef(2)} className="bp-gear">
          <path className="ln" pathLength={1} style={dly(620)} d={OUT3} />
          <circle className="ln" pathLength={1} style={dly(780)} r={34} />
          <circle className="ln" pathLength={1} style={dly(900)} r={8} />
          <path className="ln" pathLength={1} style={dly(940)} d="M0 -34V34M-34 0H34" />
        </g>
        {/* they are an escapement: one tooth for each key you press */}
        <path className="lead" d={`M${G1.cx + G1_LEAD[0]} ${G1.cy + G1_LEAD[1]}l46 46H${G1.cx + G1_LEAD[0] + 400}`} />
        <circle className="lead-dot" cx={G1.cx + G1_LEAD[0]} cy={G1.cy + G1_LEAD[1]} r={2.6} />
        <text className="cap" x={G1.cx + G1_LEAD[0] + 56} y={G1.cy + G1_LEAD[1] + 36}>FIG. 2 — ESCAPEMENT</text>
      </g>
    </svg>
  );
});
