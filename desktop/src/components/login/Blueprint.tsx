import { forwardRef, memo, useEffect, useImperativeHandle, useMemo, useRef, type CSSProperties } from 'react';
import { PROBE_AT, PROBE_FIXED, PROBE_SPIN, PROBE_YAW0, probeFrame, type ProbeFrame } from './probe';

/**
 * The drafting plate behind the terminal (1920 × 1080 user space, sliced to the window):
 * a drive train of three meshing gears, a protractor with a working compass arm, centre lines and construction.
 * Drawn here, not traced from anything. The gears are the terminal's escapement: they tick while idle,
 * ratchet one tooth per keystroke, spin while the password is checked and jam when it is refused.
 */
export interface BlueprintHandle {
  /** Advance the drive train by n teeth of the large gear (negative turns it back). */
  tick: (n?: number) => void;
  /** A refused password: the train kicks back a tooth and shudders forward again. */
  jam: () => void;
}

interface Gear { cx: number; cy: number; n: number; r: number; phase: number; ratio: number }

const M = 9.556;
const G1: Gear = { cx: 1712, cy: 294, n: 36, r: 172, phase: 112, ratio: 1 };
const G2: Gear = { cx: 1611.7, cy: 542.1, n: 20, r: 95.6, phase: 283, ratio: -36 / 20 };
const G3: Gear = { cx: 1728.8, cy: 640.4, n: 12, r: 57.3, phase: 220, ratio: 36 / 12 };
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

/* Protractor + compass, lower left. */
const PC = { x: 196, y: 842, r: 318 };
const PTICKS = Array.from({ length: 144 }, (_, i) => {
  const a = i * 2.5;
  const len = i % 12 === 0 ? 20 : i % 2 === 0 ? 11 : 6;
  return `M${polar(PC.r - 22, a)}L${polar(PC.r - 22 - len, a)}`;
}).join('');
const PLABELS = Array.from({ length: 12 }, (_, i) => i * 30);

const dly = (ms: number) => ({ '--d': `${ms}ms` }) as CSSProperties;

/*
 * A probe, upper left, drawn turned and tilted (see probe.ts). Kept up and left of the wordmark at any size.
 * It turns slowly about its own axis: the parts that are not solids of revolution are re-projected about 30 times
 * a second, straight into the paths (no React render per frame). Reduced motion keeps the still drawing.
 */
const FRAME_KEYS = ['hidden', 'body', 'marks', 'grille', 'struts', 'booms', 'wings', 'cells', 'strips', 'axis'] as const;
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
      <g className="bp-drift" style={{ transformOrigin: `${PROBE_AT.x}px ${PROBE_AT.y}px` }}>
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
        <path ref={at('wings')} className="ln" pathLength={1} style={dly(420)} d={first.wings} />
        <path ref={at('cells')} className="ln" pathLength={1} style={dly(760)} d={first.cells} />
        <path ref={at('strips')} className="tk" d={first.strips} />
        {/* the sense of turn */}
        <path className="dash" d={F.spin} />
        <path className="ln" pathLength={1} style={dly(900)} d={F.arrow} />
        <text className="lab" x={F.omega.x} y={F.omega.y}>{`\u03c9 ${PROBE_SPIN}\u00b0/s`}</text>
      </g>
      <text className="cap" x={PROBE_AT.x - 172} y={PROBE_AT.y + 152}>FIG. 3 — PROBE · ISO</text>
    </g>
  );
});

export const Blueprint = forwardRef<BlueprintHandle, { reduced: boolean; busy: boolean }>(function Blueprint({ reduced, busy }, ref) {
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
      {/* centre lines and construction */}
      <path className="cl" d="M0 540H1920M960 0V1080" />
      <g className="bp-cons">
        <path className="dash" d={`M${PC.x} ${PC.y}L${G1.cx} ${G1.cy}`} />
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
      <text className="cap" x={PC.x + 12} y={PC.y - PC.r - 34}>FIG. 1 — R 318 · 2.5° DIV</text>

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
        <path className="ln" pathLength={1} style={dly(1000)} d={`M${G3.cx - 30} ${G3.cy + 58}L${G3.cx - 64} ${G3.cy + 118}H${G3.cx - 400}`} />
        <text className="cap" x={G3.cx - 72} y={G3.cy + 108} textAnchor="end">FIG. 2 — DRIVE · Z36 / Z20 / Z12</text>
        <text className="lab" x={G3.cx + 74} y={G3.cy + 52}>m 9.56</text>
      </g>
    </svg>
  );
});
