/**
 * The probe on the drafting plate, drawn as an axonometric view: the model is a few boxes, rings and a dish in
 * its own 3-D space, turned and tilted, then projected orthographically onto the plate (1920 × 1080 user space).
 * Edges on faces turned away from the viewer are kept as hidden (dashed) lines, as on a real drawing.
 * Original geometry — not traced from any image.
 */
type V3 = [number, number, number];
type P2 = [number, number];

const DEG = Math.PI / 180;
const YAW = -30 * DEG;
const PITCH = 20 * DEG;
const ROLL = 30 * DEG;
/** Where the body's centre lands on the plate, and the drawing's scale. */
export const PROBE_AT = { x: 440, y: 205, s: 1.12 };

const cy = Math.cos(YAW), sy = Math.sin(YAW);
const cp = Math.cos(PITCH), sp = Math.sin(PITCH);
const cr = Math.cos(ROLL), sr = Math.sin(ROLL);

function rot([x, y, z]: V3): V3 {
  const x1 = x * cy + z * sy;
  const z1 = -x * sy + z * cy;
  const y2 = y * cp - z1 * sp;
  const z2 = y * sp + z1 * cp;
  return [x1 * cr - y2 * sr, x1 * sr + y2 * cr, z2];
}
function proj(v: V3): P2 {
  const [X, Y] = rot(v);
  return [PROBE_AT.x + PROBE_AT.s * X, PROBE_AT.y - PROBE_AT.s * Y];
}
const r1 = (n: number) => Math.round(n * 10) / 10;
const fmt = ([a, b]: P2) => `${r1(a)} ${r1(b)}`;
const line = (vs: V3[], close = false) => `M${vs.map((v) => fmt(proj(v))).join('L')}${close ? 'Z' : ''}`;
const facing = (n: V3) => rot(n)[2] > 1e-6;
const ring = (f: (t: number) => V3, n = 48, from = 0, to = 360) =>
  Array.from({ length: n + 1 }, (_, i) => f((from + ((to - from) * i) / n) * DEG));

/* ---- body: a box ---- */
const BW = 36, BH = 50, BD = 36;
type Edge = { a: V3; b: V3; faces: V3[] };
const edges: Edge[] = [];
for (const s1 of [-1, 1]) for (const s2 of [-1, 1]) {
  edges.push({ a: [-BW, s1 * BH, s2 * BD], b: [BW, s1 * BH, s2 * BD], faces: [[0, s1, 0], [0, 0, s2]] });
  edges.push({ a: [s1 * BW, -BH, s2 * BD], b: [s1 * BW, BH, s2 * BD], faces: [[s1, 0, 0], [0, 0, s2]] });
  edges.push({ a: [s1 * BW, s2 * BH, -BD], b: [s1 * BW, s2 * BH, BD], faces: [[s1, 0, 0], [0, s2, 0]] });
}
let bodySeen = '';
let bodyHidden = '';
for (const e of edges) {
  const d = line([e.a, e.b]);
  if (e.faces.some(facing)) bodySeen += d; else bodyHidden += d;
}
/* two bands round the body, and a hatch and a grille on the faces that show */
let bands = '';
let bandsHidden = '';
for (const y of [-34, 34]) {
  const segs: [V3, V3, V3][] = [
    [[-BW, y, BD], [BW, y, BD], [0, 0, 1]],
    [[BW, y, BD], [BW, y, -BD], [1, 0, 0]],
    [[BW, y, -BD], [-BW, y, -BD], [0, 0, -1]],
    [[-BW, y, -BD], [-BW, y, BD], [-1, 0, 0]],
  ];
  for (const [a, b, n] of segs) {
    if (facing(n)) bands += line([a, b]); else bandsHidden += line([a, b]);
  }
}
const hatch = facing([0, 0, 1]) ? line([[-20, -18, BD], [20, -18, BD], [20, 18, BD], [-20, 18, BD]], true) : '';
const grille = facing([1, 0, 0])
  ? [-18, -9, 0, 9, 18].map((y) => line([[BW, y, -22], [BW, y, 22]])).join('')
  : '';

/* ---- dish on a short mast ---- */
const V0 = BH + 16;
const DR = 46;
const DEPTH = 20;
const dishAt = (r: number, t: number): V3 => [r * Math.cos(t), V0 + DEPTH * (r / DR) ** 2, r * Math.sin(t)];
const rim = line(ring((t) => dishAt(DR, t), 64), true);
const rimInner = line(ring((t) => dishAt(DR * 0.56, t), 48), true);
/* silhouette meridians: where the rim is widest across the screen */
const axisDir = (() => {
  const a = proj([0, V0, 0]);
  const b = proj([0, V0 + 10, 0]);
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l = Math.hypot(dx, dy);
  return [-dy / l, dx / l] as P2;
})();
const across = (p: P2) => p[0] * axisDir[0] + p[1] * axisDir[1];
const extremeT = (r: number, sign: number) => {
  let best = 0, bv = -Infinity;
  for (let i = 0; i < 360; i++) {
    const v = sign * across(proj(dishAt(r, i * DEG)));
    if (v > bv) { bv = v; best = i * DEG; }
  }
  return best;
};
const meridian = (t: number) => line(Array.from({ length: 13 }, (_, i) => dishAt((DR * i) / 12, t)));
const dish = meridian(extremeT(DR, 1)) + meridian(extremeT(DR, -1));
const FEED = V0 + DEPTH + 26;
const struts = [90, 210, 330].map((a) => line([dishAt(DR, a * DEG), [0, FEED, 0]])).join('');
const mast = line([[0, BH, 0], [0, V0, 0]]);
const feed = line(ring((t) => [3.2 * Math.cos(t), FEED + 3, 3.2 * Math.sin(t)], 20), true);

/* ---- nozzle under the body ---- */
const NZ0 = -BH, NZ1 = -BH - 20;
const nz = (r: number, y: number) => (t: number): V3 => [r * Math.cos(t), y, r * Math.sin(t)];
const nozzleRim = line(ring(nz(15, NZ1), 40), true);
const nozzle = (() => {
  const a = proj([0, NZ0, 0]), b = proj([0, NZ1, 0]);
  const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy);
  const n: P2 = [-dy / l, dx / l];
  const pick = (r: number, y: number, s: number) => {
    let best: P2 = [0, 0], bv = -Infinity;
    for (let i = 0; i < 360; i += 2) {
      const p = proj(nz(r, y)(i * DEG));
      const v = s * (p[0] * n[0] + p[1] * n[1]);
      if (v > bv) { bv = v; best = p; }
    }
    return best;
  };
  return `M${fmt(pick(8, NZ0, 1))}L${fmt(pick(15, NZ1, 1))}M${fmt(pick(8, NZ0, -1))}L${fmt(pick(15, NZ1, -1))}`;
})();

/* ---- solar wings, each tilted about the boom ---- */
const WIN = 58, WOUT = 232, WH = 31, TILT = 30 * DEG;
const wingPt = (x: number, v: number): V3 => [x, v * Math.cos(TILT), v * Math.sin(TILT)];
let wings = '';
let cells = '';
let strips = '';
let booms = '';
let hinges = '';
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
  booms += line([[s * BW, 0, 0], [x0, 0, 0]]);
  hinges += line(ring((t) => [s * 47, 5 * Math.cos(t), 5 * Math.sin(t)], 20), true);
}

/* ---- drafting: centre lines and the length of one wing ---- */
const axes = line([[-WOUT - 34, 0, 0], [WOUT + 34, 0, 0]]) + line([[0, NZ1 - 34, 0], [0, FEED + 34, 0]]);
const span = (() => {
  const L = proj([-WOUT, 0, 0]);
  const R = proj([-WIN, 0, 0]);
  const dx = R[0] - L[0], dy = R[1] - L[1], len = Math.hypot(dx, dy);
  const u: P2 = [dx / len, dy / len];
  const n: P2 = [-u[1], u[0]];
  const off = 66;
  const at = (p: P2, k: number, j = 0): P2 => [p[0] + n[0] * k + u[0] * j, p[1] + n[1] * k + u[1] * j];
  const mid: P2 = [(L[0] + R[0]) / 2, (L[1] + R[1]) / 2];
  const gap = 22;
  const d =
    `M${fmt(at(L, 40))}L${fmt(at(L, off + 7))}M${fmt(at(R, 40))}L${fmt(at(R, off + 7))}` +
    `M${fmt(at(L, off))}L${fmt(at(mid, off, -gap))}M${fmt(at(mid, off, gap))}L${fmt(at(R, off))}` +
    `M${fmt(at(L, off - 4, -4))}L${fmt(at(L, off + 4, 4))}M${fmt(at(R, off - 4, -4))}L${fmt(at(R, off + 4, 4))}`;
  const t = at(mid, off);
  return { d, x: r1(t[0]), y: r1(t[1]), angle: r1(Math.atan2(u[1], u[0]) / DEG), value: String(WOUT - WIN) };
})();

export const PROBE = {
  bodySeen, bodyHidden, bands, bandsHidden, hatch, grille,
  rim, rimInner, dish, struts, mast, feed,
  nozzle, nozzleRim,
  wings, cells, strips, booms, hinges,
  axes, span,
};
