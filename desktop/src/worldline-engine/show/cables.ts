// Cable bundles of the lock shot: a left bundle leaving the left end of the board, and a front
// bundle that starts at the transistor beside tube 3, runs across the lead combs of tubes 3-5, rises
// in front of tubes 6-8 and leaves at the right edge.
// R2-02: the bundles move all through T12/T13 as in the original (src 10.10-12.95): the whip after
// the cut travels outwards, the left peak drifts out and leaves the frame before 12.95, the right
// end turns from a hanging loop into a rise to the upper right edge. Their centre lines are
// keyframed from the 60 fps source (cable-keys.ts); the static shape (measured at 11.90) is kept
// for still frames (reduced motion, failure / end frames).
import type { CableState } from './frame';
import { CABLE_KEY_T, CABLE_L, CABLE_F, CABLE_F_PREFIX } from './cable-keys';

type P = [number, number];
interface Bundle {
  pts: P[];
  n: number;
  hw: number[];
  spread: number[];
  front: boolean;
  amp: number;
  ph: number;
}

const BUNDLES: Bundle[] = [
  {
    pts: [
      [-0.5047, -0.0522], [-0.569, -0.0087], [-0.6452, 0.0646], [-0.7035, 0.1378], [-0.7402, 0.1932],
      [-0.757, 0.1972], [-0.763, 0.1853], [-0.7926, 0.0754], [-0.8262, -0.0167], [-0.8708, -0.0899], [-0.92, -0.1987],
    ],
    // R1-02 (R3, measured on src 11.60 / 12.50 at 1080p): 4 black wires with narrow gaps - the
    // bundle is ~21 px wide (R2: 27 px of thin wires with wide gaps, read as grey at 640)
    n: 4,
    hw: [0.0021, 0.0021],
    spread: [0.0052, 0.0052, 0.0052, 0.0052, 0.0052, 0.0052],
    front: false,
    amp: 1,
    ph: 0,
  },
  {
    pts: [
      [-0.2566, -0.0432], [-0.2016, -0.0039], [-0.1465, 0.0164], [-0.055, 0.0145], [0.0275, -0.0039], [0.0916, -0.0208],
      [0.165, -0.0143], [0.2291, -0.0039], [0.2749, 0.0189], [0.3207, 0.0445], [0.3665, 0.0783], [0.4123, 0.104],
      [0.4581, 0.1196], [0.5131, 0.1287], [0.5681, 0.1323], [0.623, 0.126], [0.6963, 0.1076], [0.7696, 0.0462],
      [0.843, -0.0285], [0.8796, -0.0836], [0.9162, -0.1643],
    ],
    // (src: 5 wires - the extra lines at the free end are colour fringes; thin wires with gaps
    // across the tube bases (~2.5 px), ~5 px wires and ~33 px overall at the free end)
    n: 5,
    hw: [0.0012, 0.0012, 0.0027, 0.0027, 0.0027, 0.0027],
    spread: [0.0056, 0.0056, 0.0064, 0.0066, 0.0066, 0.0066],
    front: true,
    amp: 1.15,
    ph: 2.1,
  },
];

/** Bundle centre lines at show time t (proto seconds), linear between the source keys. */
function keyed(t: number, keys: number[][]): P[] {
  const T = CABLE_KEY_T;
  let i = 0;
  if (t <= T[0]) i = 0;
  else if (t >= T[T.length - 1]) i = T.length - 2;
  else while (i < T.length - 2 && T[i + 1] < t) i++;
  const k = Math.min(Math.max((t - T[i]) / (T[i + 1] - T[i]), 0), 1);
  const a = keys[i];
  const b = keys[i + 1];
  const out: P[] = [];
  for (let j = 0; j < a.length; j += 2) out.push([a[j] + (b[j] - a[j]) * k, a[j + 1] + (b[j + 1] - a[j + 1]) * k]);
  return out;
}
const F_PREFIX: P[] = [];
for (let j = 0; j < CABLE_F_PREFIX.length; j += 2) F_PREFIX.push([CABLE_F_PREFIX[j], CABLE_F_PREFIX[j + 1]]);

const SEG = 90;
let wires = 0;
for (const b of BUNDLES) wires += b.n;
const VERTS = wires * SEG * 6;
const buf = new Float32Array(VERTS * 4);
export const MAX_CABLE_VERTS = VERTS;

function catmull(pts: P[], u: number): P {
  const n = pts.length - 1;
  const f = Math.min(Math.max(u, 0), 1) * n;
  const i = Math.min(Math.floor(f), n - 1);
  const t = f - i;
  const p0 = pts[Math.max(i - 1, 0)];
  const p1 = pts[i];
  const p2 = pts[i + 1];
  const p3 = pts[Math.min(i + 2, n)];
  const t2 = t * t;
  const t3 = t2 * t;
  const c = (a: number, b: number, cc: number, d: number): number =>
    0.5 * (2 * b + (-a + cc) * t + (2 * a - 5 * b + 4 * cc - d) * t2 + (-a + 3 * b - 3 * cc + d) * t3);
  return [c(p0[0], p1[0], p2[0], p3[0]), c(p0[1], p1[1], p2[1], p3[1])];
}

function spreadAt(s: number[], u: number): number {
  const f = u * (s.length - 1);
  const i = Math.min(Math.floor(f), s.length - 2);
  const k = f - i;
  return s[i] * (1 - k) + s[i + 1] * k;
}


/**
 * t: show time (seconds); since: when the lock framing cut in (whip starts there);
 * ro: instrument placement; still: no motion at all (reduced motion / end frames).
 */
export function cableFrame(t: number, ro: [number, number, number, number], alpha: number, still = false, since = 6.13): CableState {
  const s = ro[0];
  const c = Math.cos(ro[3]);
  const sn = Math.sin(ro[3]);
  const place = (q: P): P => [ro[1] + (c * q[0] - sn * q[1]) * s, ro[2] + (sn * q[0] + c * q[1]) * s];
  void since;
  const live: P[][] = still ? [BUNDLES[0].pts, BUNDLES[1].pts] : [keyed(t, CABLE_L), F_PREFIX.concat(keyed(t, CABLE_F))];
  const center: P[] = new Array(SEG + 1);
  const nrm: P[] = new Array(SEG + 1);
  let o = 0;
  let back = 0;
  let front = 0;
  for (const pass of [false, true]) {
    for (let bi = 0; bi < BUNDLES.length; bi++) {
      const b = BUNDLES[bi];
      if (b.front !== pass) continue;
      const pts = live[bi];
      for (let i = 0; i <= SEG; i++) center[i] = catmull(pts, i / SEG);
      for (let i = 0; i <= SEG; i++) {
        const a = center[Math.max(i - 1, 0)];
        const d = center[Math.min(i + 1, SEG)];
        const tx = d[0] - a[0];
        const ty = d[1] - a[1];
        const l = Math.hypot(tx, ty) || 1;
        nrm[i] = [-ty / l, tx / l];
      }
      const disp: P[] = center;
      for (let j = 0; j < b.n; j++) {
        const jo = j - (b.n - 1) / 2;
        const line: P[] = disp.map((p, i) => {
          const off = jo * spreadAt(b.spread, i / SEG);
          return [p[0] + nrm[i][0] * off, p[1] + nrm[i][1] * off];
        });
        const shade = 0.35 + 0.65 * (((j * 7 + 3) % 5) / 4);
        for (let i = 0; i < SEG; i++) {
          const A = line[i];
          const B = line[i + 1];
          const nA = nrm[i];
          const nB = nrm[i + 1];
          const hA = spreadAt(b.hw, i / SEG);
          const hB = spreadAt(b.hw, (i + 1) / SEG);
          const A0 = place([A[0] + nA[0] * hA, A[1] + nA[1] * hA]);
          const A1 = place([A[0] - nA[0] * hA, A[1] - nA[1] * hA]);
          const B0 = place([B[0] + nB[0] * hB, B[1] + nB[1] * hB]);
          const B1 = place([B[0] - nB[0] * hB, B[1] - nB[1] * hB]);
          buf.set([A0[0], A0[1], -1, shade, A1[0], A1[1], 1, shade, B0[0], B0[1], -1, shade,
                   B0[0], B0[1], -1, shade, A1[0], A1[1], 1, shade, B1[0], B1[1], 1, shade], o);
          o += 24;
        }
        if (pass) front += SEG * 6;
        else back += SEG * 6;
      }
    }
  }
  return { data: buf, back, front, alpha };
}
