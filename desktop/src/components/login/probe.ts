/**
 * The probe on the drafting plate, drawn as an axonometric view: the model is a few boxes, rings and a dish in
 * its own 3-D space, turned about its long axis (yaw), tilted, then projected orthographically onto the plate
 * (1920 × 1080 user space). Edges on faces turned away from the viewer are drawn as hidden (dashed) lines, as on
 * a real drawing; as the probe turns, edges pass from seen to hidden. Original geometry, not traced from any image.
 */
type V3 = [number, number, number];
type P2 = [number, number];

const DEG = Math.PI / 180;
const PITCH = 20 * DEG;
const ROLL = 30 * DEG;
/** Where the body's centre lands on the plate, and the drawing's scale. */
export const PROBE_AT = { x: 380, y: 240, s: 0.92 };
/** Resting yaw (the still drawing, and where the turn starts), and the turn rate. */
export const PROBE_YAW0 = -30;
export const PROBE_SPIN = 9;

const cp = Math.cos(PITCH), sp = Math.sin(PITCH);
const cr = Math.cos(ROLL), sr = Math.sin(ROLL);
const r1 = (n: number) => Math.round(n * 10) / 10;
const fmt = ([a, b]: P2) => `${r1(a)} ${r1(b)}`;

function view(yawDeg: number) {
  const cy = Math.cos(yawDeg * DEG), sy = Math.sin(yawDeg * DEG);
  const rot = ([x, y, z]: V3): V3 => {
    const x1 = x * cy + z * sy;
    const z1 = -x * sy + z * cy;
    const y2 = y * cp - z1 * sp;
    const z2 = y * sp + z1 * cp;
    return [x1 * cr - y2 * sr, x1 * sr + y2 * cr, z2];
  };
  const proj = (v: V3): P2 => {
    const [X, Y] = rot(v);
    return [PROBE_AT.x + PROBE_AT.s * X, PROBE_AT.y - PROBE_AT.s * Y];
  };
  const line = (vs: V3[], close = false) => `M${vs.map((v) => fmt(proj(v))).join('L')}${close ? 'Z' : ''}`;
  const facing = (n: V3) => rot(n)[2] > 1e-6;
  return { proj, line, facing };
}
const ring = (f: (t: number) => V3, n = 48, from = 0, to = 360) =>
  Array.from({ length: n + 1 }, (_, i) => f((from + ((to - from) * i) / n) * DEG));

/* ---- model ---- */
const BW = 36, BH = 50, BD = 36;
type Edge = { a: V3; b: V3; faces: V3[] };
const EDGES: Edge[] = [];
for (const s1 of [-1, 1]) for (const s2 of [-1, 1]) {
  EDGES.push({ a: [-BW, s1 * BH, s2 * BD], b: [BW, s1 * BH, s2 * BD], faces: [[0, s1, 0], [0, 0, s2]] });
  EDGES.push({ a: [s1 * BW, -BH, s2 * BD], b: [s1 * BW, BH, s2 * BD], faces: [[s1, 0, 0], [0, 0, s2]] });
  EDGES.push({ a: [s1 * BW, s2 * BH, -BD], b: [s1 * BW, s2 * BH, BD], faces: [[s1, 0, 0], [0, s2, 0]] });
}
const V0 = BH + 16;
const DR = 46;
const DEPTH = 20;
const dishAt = (r: number, t: number): V3 => [r * Math.cos(t), V0 + DEPTH * (r / DR) ** 2, r * Math.sin(t)];
const FEED = V0 + DEPTH + 26;
const NZ0 = -BH, NZ1 = -BH - 20;
const nz = (r: number, y: number) => (t: number): V3 => [r * Math.cos(t), y, r * Math.sin(t)];
const WIN = 58, WOUT = 190, WH = 31, TILT = 30 * DEG;
const wingPt = (x: number, v: number): V3 => [x, v * Math.cos(TILT), v * Math.sin(TILT)];

/** Furthest point of a ring across the screen, either side of the probe's axis. */
function silhouette(proj: (v: V3) => P2, f: (t: number) => V3, sign: number) {
  const a = proj([0, 0, 0]);
  const b = proj([0, 10, 0]);
  const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy);
  const n: P2 = [-dy / l, dx / l];
  let best = 0, bv = -Infinity;
  for (let i = 0; i < 360; i += 2) {
    const p = proj(f(i * DEG));
    const v = sign * (p[0] * n[0] + p[1] * n[1]);
    if (v > bv) { bv = v; best = i * DEG; }
  }
  return best;
}

/*
 * Everything that is a solid of revolution about the probe's axis (dish, feed, mast, nozzle) looks the same at
 * any yaw, so it is drawn once. The rest is recomputed for each yaw.
 */
const FIXED = (() => {
  const { proj, line } = view(0);
  const rim = line(ring((t) => dishAt(DR, t), 64), true);
  const rimInner = line(ring((t) => dishAt(DR * 0.56, t), 48), true);
  const meridian = (t: number) => line(Array.from({ length: 13 }, (_, i) => dishAt((DR * i) / 12, t)));
  const dish = meridian(silhouette(proj, (t) => dishAt(DR, t), 1)) + meridian(silhouette(proj, (t) => dishAt(DR, t), -1));
  const mast = line([[0, BH, 0], [0, V0, 0]]);
  const feed = line(ring((t) => [3.2 * Math.cos(t), FEED + 3, 3.2 * Math.sin(t)], 20), true);
  const side = (s: number) => {
    const t0 = silhouette(proj, nz(8, NZ0), s);
    const t1 = silhouette(proj, nz(15, NZ1), s);
    return `M${fmt(proj(nz(8, NZ0)(t0)))}L${fmt(proj(nz(15, NZ1)(t1)))}`;
  };
  const nozzle = side(1) + side(-1) + line(ring(nz(15, NZ1), 40), true);
  const axis = line([[0, NZ1 - 34, 0], [0, FEED + 34, 0]]);
  /* the sense of turn: a dashed arc round the axis under the nozzle, arrow at its end */
  const spinR = 34, spinY = NZ1 - 14;
  const arcPts = ring(nz(spinR, spinY), 24, 160, 28);
  const end = proj(arcPts[arcPts.length - 1]);
  const prev = proj(arcPts[arcPts.length - 3]);
  const dx = end[0] - prev[0], dy = end[1] - prev[1], l = Math.hypot(dx, dy);
  const u: P2 = [dx / l, dy / l];
  const head = (k: number): P2 => [end[0] - u[0] * 7 + u[1] * 3.5 * k, end[1] - u[1] * 7 - u[0] * 3.5 * k];
  const spin = line(arcPts);
  const arrow = `M${fmt(head(1))}L${fmt(end)}L${fmt(head(-1))}`;
  return { rim, rimInner, dish, mast, feed, nozzle, axis, spin, arrow, omega: { x: r1(end[0] + 10), y: r1(end[1] + 12) } };
})();
export const PROBE_FIXED = FIXED;

export type ProbeFrame = Record<'hidden' | 'body' | 'marks' | 'grille' | 'struts' | 'booms' | 'wings' | 'cells' | 'strips' | 'axis', string>;

export function probeFrame(yawDeg: number): ProbeFrame {
  const { line, facing } = view(yawDeg);
  let body = '';
  let hidden = '';
  for (const e of EDGES) {
    const d = line([e.a, e.b]);
    if (e.faces.some(facing)) body += d; else hidden += d;
  }
  /* bands round the body; a hatch on the broad faces and a grille on the narrow ones, where they show */
  let marks = '';
  let grille = '';
  for (const y of [-34, 34]) {
    const segs: [V3, V3, V3][] = [
      [[-BW, y, BD], [BW, y, BD], [0, 0, 1]],
      [[BW, y, BD], [BW, y, -BD], [1, 0, 0]],
      [[BW, y, -BD], [-BW, y, -BD], [0, 0, -1]],
      [[-BW, y, -BD], [-BW, y, BD], [-1, 0, 0]],
    ];
    for (const [a, b, n] of segs) {
      if (facing(n)) marks += line([a, b]); else hidden += line([a, b]);
    }
  }
  for (const s of [-1, 1]) {
    if (facing([0, 0, s])) marks += line([[-20, -18, s * BD], [20, -18, s * BD], [20, 18, s * BD], [-20, 18, s * BD]], true);
    if (facing([s, 0, 0])) grille += [-18, -9, 0, 9, 18].map((y) => line([[s * BW, y, -22], [s * BW, y, 22]])).join('');
  }
  const struts = [90, 210, 330].map((a) => line([dishAt(DR, a * DEG), [0, FEED, 0]])).join('');
  let wings = '';
  let cells = '';
  let strips = '';
  let booms = '';
  for (const s of [-1, 1]) {
    const x0 = s * WIN, x1 = s * WOUT;
    wings += line([wingPt(x0, -WH), wingPt(x1, -WH), wingPt(x1, WH), wingPt(x0, WH)], true);
    for (let k = 1; k < 5; k++) {
      const x = s * (WIN + ((WOUT - WIN) * k) / 5);
      cells += line([wingPt(x, -WH), wingPt(x, WH)]);
    }
    cells += line([wingPt(x0, 0), wingPt(x1, 0)]);
    for (let k = 0; k < 10; k++) {
      const x = s * (WIN + ((WOUT - WIN) * (k + 0.5)) / 10);
      strips += line([wingPt(x, -WH + 3), wingPt(x, -2)]) + line([wingPt(x, 2), wingPt(x, WH - 3)]);
    }
    booms += line([[s * BW, 0, 0], [x0, 0, 0]]) + line(ring((t) => [s * 47, 5 * Math.cos(t), 5 * Math.sin(t)], 20), true);
  }
  const axis = line([[-WOUT - 34, 0, 0], [WOUT + 34, 0, 0]]);
  return { hidden, body, marks, grille, struts, booms, wings, cells, strips, axis };
}
