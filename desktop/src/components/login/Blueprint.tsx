import { forwardRef, useEffect, useImperativeHandle, useRef, type CSSProperties } from 'react';

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
 * A probe in two views, upper left: front elevation (dish, body, nozzle, a solar wing each side) and the plan
 * below it, tied together by projection lines. Kept inside x 215–475 so it clears the wordmark at any size.
 */
const SX = 345;
const SY = 205;
const BODY = { w: 64, h: 92 };
const WING = { in: 46, out: 128, h: 40 };
const PY = 340;

function panel(x0: number, x1: number) {
  const y0 = SY - WING.h / 2;
  const y1 = SY + WING.h / 2;
  const cells = [1, 2, 3].map((k) => `M${f(x0 + ((x1 - x0) * k) / 4)} ${y0}V${y1}`).join('') + `M${x0} ${SY}H${x1}`;
  return { frame: `M${x0} ${y0}H${x1}V${y1}H${x0}Z`, cells };
}
function strips(x0: number, x1: number) {
  let d = '';
  const y0 = SY - WING.h / 2;
  for (let k = 1; k < 8; k += 2) d += `M${f(x0 + ((x1 - x0) * k) / 8)} ${y0 + 3}V${SY + WING.h / 2 - 3}`;
  return d;
}
const WL = panel(SX - WING.out, SX - WING.in);
const WR = panel(SX + WING.in, SX + WING.out);
const STRIPS = strips(SX - WING.out, SX - WING.in) + strips(SX + WING.in, SX + WING.out);
const BX0 = SX - BODY.w / 2;
const BX1 = SX + BODY.w / 2;
const BY0 = SY - BODY.h / 2;
const BY1 = SY + BODY.h / 2;

function Probe() {
  return (
    <g className="bp-probe">
      <path className="cl" d={`M${SX} 100V392M${SX - WING.out - 22} ${SY}H${SX + WING.out + 22}M${SX - WING.out + 8} ${PY}H${SX + WING.out - 8}`} />
      <path className="dash" d={[SX - WING.out, SX - WING.in, BX0, BX1, SX + WING.in, SX + WING.out].map((x) => `M${x} ${x === BX0 || x === BX1 ? BY1 + 6 : SY + WING.h / 2 + 6}V${PY - 12}`).join('')} />
      <g className="bp-drift">
        {/* front elevation */}
        <path className="ln" pathLength={1} style={dly(300)} d={`M${BX0} ${BY0}H${BX1}V${BY1}H${BX0}Z`} />
        <path className="ln" pathLength={1} style={dly(460)} d={`M${BX0 + 6} ${BY0 + 14}H${BX1 - 6}M${BX0 + 6} ${BY1 - 14}H${BX1 - 6}M${BX0 + 12} ${SY - 12}h${BODY.w - 24}v24h${-(BODY.w - 24)}z`} />
        <path className="ln" pathLength={1} style={dly(380)} d={`M${SX} ${BY0}V${BY0 - 18}M${SX - 27} ${BY0 - 36}Q${SX} ${BY0 - 6} ${SX + 27} ${BY0 - 36}Z`} />
        <path className="ln" pathLength={1} style={dly(560)} d={`M${SX} ${BY0 - 21}L${SX} ${BY0 - 50}M${SX - 7} ${BY0 - 36}L${SX} ${BY0 - 50}L${SX + 7} ${BY0 - 36}`} />
        <circle className="ln" pathLength={1} style={dly(620)} cx={SX} cy={BY0 - 52} r={2.6} />
        <path className="ln" pathLength={1} style={dly(520)} d={`M${SX - 9} ${BY1}L${SX - 13} ${BY1 + 16}H${SX + 13}L${SX + 9} ${BY1}`} />
        <path className="ln" pathLength={1} style={dly(600)} d={`M${BX0} ${SY}H${SX - WING.in}M${BX1} ${SY}H${SX + WING.in}`} />
        <circle className="ln" pathLength={1} style={dly(640)} cx={SX - WING.in + 4 + 4} cy={SY} r={4} />
        <circle className="ln" pathLength={1} style={dly(660)} cx={SX + WING.in - 8} cy={SY} r={4} />
        <path className="ln" pathLength={1} style={dly(420)} d={WL.frame} />
        <path className="ln" pathLength={1} style={dly(480)} d={WR.frame} />
        <path className="ln" pathLength={1} style={dly(700)} d={WL.cells + WR.cells} />
        <path className="tk" d={STRIPS} />

        {/* plan */}
        <circle className="ln" pathLength={1} style={dly(760)} cx={SX} cy={PY} r={BODY.w / 2} transform={`rotate(-90 ${SX} ${PY})`} />
        <circle className="ln" pathLength={1} style={dly(820)} cx={SX} cy={PY} r={27} />
        <circle className="ln" pathLength={1} style={dly(860)} cx={SX} cy={PY} r={3} />
        <path className="ln" pathLength={1} style={dly(880)} d={`M${SX - WING.out} ${PY - 2}H${SX - WING.in}V${PY + 2}H${SX - WING.out}ZM${SX + WING.in} ${PY - 2}H${SX + WING.out}V${PY + 2}H${SX + WING.in}Z`} />
        <path className="ln" pathLength={1} style={dly(920)} d={`M${BX0} ${PY}H${SX - WING.in}M${BX1} ${PY}H${SX + WING.in}`} />
      </g>
      {/* span */}
      <path className="tk" d={`M${SX - WING.out} 374V386M${SX + WING.out} 374V386M${SX - WING.out + 1} 380H${SX - 26}M${SX + 26} 380H${SX + WING.out - 1}`} />
      <text className="lab" x={SX} y={381} textAnchor="middle" dominantBaseline="middle">{WING.out * 2}</text>
      <text className="cap" x={SX - WING.out} y={420}>FIG. 3 — PROBE · 2 VIEWS</text>
    </g>
  );
}

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

      <Probe />

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
      <text className="cap" x={PC.x - 40} y={PC.y - PC.r - 34}>FIG. 1 — R 318 · 2.5° DIV</text>

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
