// The gear shot (T8): a 3D cloud of gears the camera flies into while rolling, plus the single
// gear that grows out of the instrument at the end of the fragment shot (T7).
import { rng, clamp, smooth } from './math';
import type { V2 } from './math';

/** instances (each gear: its face and a darker side copy). R6-D: room for the denser cloud */
export const MAX_GEARS = 600;
/** R6-B: camera travel through the cloud over the shot, CAM_A u + CAM_B u^2 (R5 .3 u + .55 u^2):
 *  fastest at the start (src zoom per frame .085 over 4.57-4.72, .084 over 4.83-5.00) */
const CAM_A = 2.2;
const CAM_B = -0.4;
export const GEAR_STRIDE = 20;
/** focal length (stage units: the frame is 1 high, 16/9 wide) */
const F = 0.55;
/** the vanishing point shotGears passes in */
const VP0: V2 = [0, 0.03];
const camAt = (u: number): number => CAM_A * u + CAM_B * u * u;
const rollAt = (u: number): number => -0.27 * smooth(0, 1, u) - 0.05 * u;
/** proto time -> u of the gear shot (T.GEAR0 4.57 .. T.DARK0 5.10 in timeline.ts) */
const uAt = (t: number): number => (t - 4.57) / 0.53;

interface G3 {
  x: number;
  y: number;
  z: number;
  R: number;
  teeth: number;
  depth: number;
  style: number;
  spokes: number;
  hub: number;
  a0: number;
  spin: number;
  color: [number, number, number];
  alpha: number;
  metal: number;
  thin: number;
  squash: number;
  sqA: number;
  seed: number;
  /** placed from the source (the discs the camera passes): not run through violates(), and culled
   *  only right at the camera */
  fixed?: boolean;
}

const DARK: [number, number, number] = [0.15, 0.17, 0.21];
const PALE: [number, number, number] = [0.79, 0.72, 0.63];

/** R6-D (src 8.63-8.80 at 60 fps): the plain saw-toothed discs and the dark wheel
 *  the camera passes close by, never in front of the readout's centre (the left disc only over the
 *  first digits, as in the source). Each is fixed by two sightings
 *  in the source - [t, x, y, radius] at t1 and t2 (stage units, the frame 1 high): the growth between
 *  them gives the distance, the first one the place. In the source a dark wheel passes over the top
 *  (8.63-8.67), discs pass at the right (8.70-8.75) and at the bottom (8.75-8.78), and one comes
 *  close behind the left digits (8.77-8.80) and fills the left third until ~8.85. */
const GIANTS: ReadonlyArray<readonly [number, number, number, number, number, number, number, number, number]> = [
  // t1, x1, y1, r1, t2, x2, y2, r2, colour (0 beige, 1 dark, 2 beige in shade)
  [4.8, 0.53, 0.0, 0.16, 4.85, 0.83, 0.05, 0.25, 0],
  [4.85, -0.14, -0.32, 0.2, 4.884, -0.09, -0.5, 0.3, 0],
  // (the left one's face is in shade, src 8.78-8.83; sighted at 4.884 / 4.900, which also puts its
  // edge where it is at 4.917 and 4.934)
  [4.884, -0.41, 0.07, 0.34, 4.9, -0.53, 0.1, 0.45, 2],
  [4.734, -0.1, 0.4, 0.22, 4.767, -0.13, 0.5, 0.3, 1],
];

/** R6-D (the speed has to come from flying through the cloud, not from one wheel
 *  growing in front of the camera; src 8.47-9.00): a cloud of small and middle-sized gears that fills
 *  the whole flight. Each gear is placed by a sighting: seen from camera position c at distance d,
 *  at a place on screen and with a screen radius - so wherever the camera is, the gears just ahead of
 *  it are where the screen is, and they grow and stream outward as it passes. Uniform in depth
 *  (z = c + d), PASS_D0..PASS_D1 ahead at the sighting. */
const N_CLOUD = 270;
const PASS_Z0 = 0.3;
const PASS_Z1 = 4.1;
const PASS_D0 = 0.22;
const PASS_D1 = 1.0;
/** big, mostly dark gears that pass close by at the edges of the frame (src 8.62-8.70 top and
 *  bottom, 8.83-9.00 all round) */
const N_EDGE = 36;

function buildCloud(seed: number): G3[] {
  const r = rng(seed);
  const out: G3[] = [];
  // R1-14 (src 8.50-8.90): flat, nearly face-on silhouettes in two colours - dark blue-grey and
  // warm beige - from ~1/15 of the frame height to larger than the frame (R6-D: more of them dark,
  // src 8.60-8.95)
  for (let i = 0; i < N_CLOUD + N_EDGE; i++) {
    const edge = i >= N_CLOUD;
    const z = edge ? 1.2 + 1.7 * r() : PASS_Z0 + (PASS_Z1 - PASS_Z0) * r();
    const d = edge ? 0.3 + 0.5 * r() : PASS_D0 + (PASS_D1 - PASS_D0) * r();
    // the cloud darkens as the camera goes in (dark share src .19 / .22 / .32 / .42 over 4.57-4.72 /
    // -4.83 / -5.00 / -5.10): fewer beige gears further in
    const late = smooth(0.6, 2.6, z);
    const k0 = r();
    const kind = edge
      ? k0 < 0.3 ? k0 : 0.62 + 0.38 * r()
      : k0 < 0.22 + 0.12 * late ? 0.15
      : k0 < 0.56 ? 0.4
      : k0 < 0.68 - 0.06 * late ? 0.57
      : 0.8;
    let style = 2;
    let color: [number, number, number] = DARK;
    let metal = 0.1;
    let thin = 0;
    let teeth = 12 + Math.floor(r() * 16);
    let depth = 0.16;
    let hub = 0.13;
    let spokes = 5 + Math.floor(r() * 3);
    // (R6-D, src 8.70-9.05: many of them are dark see-through spoked wheels)
    if (kind < 0.3) {
      // dark blue-grey gear with round holes (default)
    } else if (kind < 0.52) {
      style = r() < 0.5 ? 2 : 0;
      color = PALE;
      metal = 0.35;
      teeth = 14 + Math.floor(r() * 20);
      hub = 0.1 + 0.06 * r();
    } else if (kind < 0.62) {
      style = 1;
      color = [0.76, 0.69, 0.6];
      metal = 0.4;
      thin = 0.3 + 0.3 * r();
      teeth = 30 + Math.floor(r() * 40);
      depth = 0.08;
      hub = 0.09;
      spokes = 5 + Math.floor(r() * 2);
    } else {
      // (R6-D: thin rims and spokes - the bright field shows through them, src 8.80-9.05)
      style = 1;
      color = [0.17, 0.18, 0.21];
      metal = 0.2;
      thin = 0.6 + 0.35 * r();
      teeth = 20 + Math.floor(r() * 22);
      depth = 0.11;
      hub = 0.12;
      spokes = 4 + Math.floor(r() * 3);
    }
    let sr = edge ? 0.25 + 0.3 * r() : 0.035 + 0.19 * Math.pow(r(), 1.5);
    const g: G3 = {
      x: 0, y: 0, z, R: 0, teeth, depth, style, spokes, hub,
      a0: r() * Math.PI * 2,
      spin: (r() - 0.5) * (style === 1 ? 1.6 : 3.2),
      color, alpha: 1, metal, thin,
      squash: 0.86 + 0.14 * r(),
      sqA: r() * Math.PI,
      seed: r(),
    };
    // the sighting's place on screen (a little beyond the frame); a gear that would grow in front of
    // the readout, fill the frame or meet the camera on screen is tried elsewhere, then smaller
    let ok = false;
    for (let k = 0; k < 40 && !ok; k++) {
      if (k > 0 && k % 10 === 0) sr *= 0.7;
      let xs = (r() * 2 - 1) * 0.95;
      let ys = (r() * 2 - 1) * 0.55;
      if (edge) {
        // on the rim: beyond .62 across or .36 up/down
        if (r() < 0.5) xs = Math.sign(xs || 1) * (0.62 + 0.38 * r());
        else ys = Math.sign(ys || 1) * (0.36 + 0.24 * r());
      }
      g.x = (xs * d) / F;
      g.y = (ys * d) / F;
      g.R = (sr * d) / F;
      ok = !violates(g);
    }
    if (ok) out.push(g);
  }
  // the plain beige saw-toothed discs with a small bore (src 8.77-8.80) and the dark wheel
  const rg = rng(seed + 31);
  for (let i = 0; i < GIANTS.length; i++) {
    const [t1, xp, yp, sp, t2, , , s2, dark] = GIANTS[i];
    const teeth = 100 + Math.floor(rg() * 40);
    const a0 = rg() * 6;
    const spin = (rg() - 0.5) * 0.8;
    const squash = 0.8 + 0.15 * rg();
    const sqA = rg() * Math.PI;
    const sd = rg();
    // distance at t1 from the growth to t2: s2 / s1 = d1 / (d1 - (cam(t2) - cam(t1)))
    const u = uAt(t1);
    const gr = s2 / sp;
    const dp = ((camAt(uAt(t2)) - camAt(u)) * gr) / (gr - 1);
    // undo the roll and the vanishing point at t1: (x, y) = vp + rot(roll) (X, Y) F / dp
    const ro = rollAt(u);
    const c = Math.cos(ro);
    const s = Math.sin(ro);
    const qx = xp - VP0[0];
    const qy = yp - VP0[1];
    out.push({
      x: ((qx * c + qy * s) * dp) / F,
      y: ((-qx * s + qy * c) * dp) / F,
      z: camAt(u) + dp,
      R: (sp * dp) / F,
      teeth,
      depth: 0.03,
      style: dark === 1 ? 1 : 0,
      spokes: 6,
      hub: 0.035,
      a0,
      spin,
      color: dark === 1 ? [0.14, 0.16, 0.2] : dark === 2 ? [0.46, 0.4, 0.34] : [0.78, 0.71, 0.62],
      alpha: 1,
      metal: 0.4,
      thin: 0.7,
      squash,
      sqA,
      seed: sd,
      fixed: true,
    });
  }
  return out;
}

/** R6-D: the camera passes through a gap in the cloud - no gear, while on screen, covers the
 *  readout at a radius over S_CORE, grows past S_EDGE, or meets the near plane */
const S_CORE = 0.22;
const S_EDGE = 0.85;
/** the readout's box in stage units (shotGears: centre (0, .02), 8 tubes) */
const CORE_X = 0.55;
const CORE_Y0 = -0.1;
const CORE_Y1 = 0.17;
function violates(g: G3): boolean {
  let prevOn = false;
  for (let i = 0; i <= 106; i++) {
    const u = i / 106;
    const d = g.z - camAt(u);
    if (d <= 0.1) return prevOn;
    const ro = rollAt(u);
    const c = Math.cos(ro);
    const s = Math.sin(ro);
    const px = (g.x * F) / d;
    const py = (g.y * F) / d;
    const x = VP0[0] + px * c - py * s;
    const y = VP0[1] + px * s + py * c;
    const R = (g.R * F) / d;
    const on = Math.abs(x) - R <= 0.889 && Math.abs(y) - R <= 0.5;
    if (on) {
      if (R > S_EDGE) return true;
      const dx = Math.max(0, Math.abs(x) - CORE_X);
      const dy = Math.max(0, CORE_Y0 - y, y - CORE_Y1);
      if (R > S_CORE && dx * dx + dy * dy < R * R) return true;
    }
    prevOn = on;
  }
  return false;
}
const CLOUD = buildCloud(4471);
const order: number[] = [];
const zs = new Float32Array(CLOUD.length);

function pack(out: Float32Array, n: number, x: number, y: number, R: number, ang: number, g: G3, blur: number): void {
  out.set(
    [x, y, R, ang, g.teeth, g.depth, g.style, g.seed, g.color[0], g.color[1], g.color[2], g.alpha, blur, g.hub, g.metal, g.spokes, g.squash, g.sqA, g.thin, 0],
    n * GEAR_STRIDE,
  );
}

/** u: 0..1 through the gear shot. Returns the instance count. */
export function poseCloud(u: number, tau: number, out: Float32Array, vp: V2): number {
  // R6-B (U 17, src 8.47-9.00 at 60 fps): the camera flies into the cloud fast from the first frame
  // (zoom per frame src .085 over 4.57-4.72 and .084 over 4.83-5.00; R5 .0145 / .048)
  const camZ = camAt(u);
  const roll = rollAt(u);
  const cs = Math.cos(roll);
  const sn = Math.sin(roll);
  order.length = 0;
  for (let i = 0; i < CLOUD.length; i++) {
    zs[i] = CLOUD[i].z - camZ;
    // (R6-D: the placed discs slide out of the frame close by instead of vanishing on screen -
    // src 8.82-8.85, the left disc fills the left third until it is gone)
    if (zs[i] > (CLOUD[i].fixed ? 0.015 : 0.1)) order.push(i);
  }
  order.sort((a, b) => zs[b] - zs[a]);
  let n = 0;
  for (const i of order) {
    if (n >= MAX_GEARS) break;
    const g = CLOUD[i];
    const z = zs[i];
    const px = (g.x * F) / z;
    const py = (g.y * F) / z;
    const x = vp[0] + px * cs - py * sn;
    const y = vp[1] + px * sn + py * cs;
    const R = (g.R * F) / z;
    if ((R > 2.2 && !g.fixed) || R < 0.004) continue;
    if (Math.abs(x) - R > 1.0 || Math.abs(y) - R > 0.6) continue;
    const blur = R > 0.5 ? 0.004 : 0;
    const a = g.a0 + g.spin * tau + roll;
    // the gear's thickness: a darker copy just behind it, offset away from the light (R6-D: not
    // for the small ones, where the offset is under a pixel)
    if (n < MAX_GEARS - 1 && R > 0.025) {
      side.color = [g.color[0] * 0.45, g.color[1] * 0.42, g.color[2] * 0.4];
      side.teeth = g.teeth; side.depth = g.depth; side.style = g.style; side.seed = g.seed; side.alpha = g.alpha;
      side.hub = g.hub; side.metal = 0; side.spokes = g.spokes; side.squash = g.squash; side.sqA = g.sqA; side.thin = g.thin;
      pack(out, n, x + 0.05 * R * cs + 0.03 * R * sn, y - 0.05 * R * cs * 0.8 + 0.03 * R * sn, R, a, side, blur);
      n++;
    }
    pack(out, n, x, y, R, a, g, blur);
    n++;
  }
  return n;
}
const side: G3 = { x: 0, y: 0, z: 1, R: 1, teeth: 12, depth: 0.1, style: 0, spokes: 5, hub: 0.1, a0: 0, spin: 0, color: [0, 0, 0], alpha: 1, metal: 0, thin: 0, squash: 1, sqA: 0, seed: 0 };

const LONE: G3 = {
  x: 0,
  y: 0,
  z: 1,
  R: 1,
  teeth: 18,
  depth: 0.16,
  style: 2,
  spokes: 5,
  hub: 0.14,
  a0: 0.3,
  spin: 2.4,
  color: [0.05, 0.045, 0.04],
  alpha: 1,
  metal: 0,
  thin: 0,
  squash: 0.85,
  sqA: 0.4,
  seed: 0.3,
};

/** The small gear growing out of the instrument centre at the end of T7. */
// R1-13 (src 8.334-8.45): not one gear but a small cluster of bronze and dark gears that grows
// out of the middle under tubes 4-5 (~.04 of the frame at 8.33, ~.17 at 8.45)
const CLUSTER: Array<[number, number, number, number, number]> = [
  // dx, dy (in cluster units), R, spin, colour (0 bronze, 1 dark, 2 pale bronze)
  [0, 0, 0.45, 2.4, 0],
  [0.42, 0.18, 0.3, -3.1, 1],
  [-0.4, 0.2, 0.28, -2.7, 2],
  [0.14, -0.4, 0.32, 2.0, 0],
  [-0.26, -0.3, 0.22, 3.4, 1],
  [0.56, -0.16, 0.2, -2.2, 2],
  [-0.56, -0.06, 0.18, 2.9, 0],
];
const CL_COL: Array<[number, number, number]> = [[0.64, 0.43, 0.2], [0.12, 0.09, 0.07], [0.84, 0.64, 0.36]];
const clG: G3 = { ...LONE };
export function poseLone(tau: number, out: Float32Array, at: V2): number {
  const k = clamp(tau / 0.17);
  const S = 0.045 + 0.19 * k * k;
  let n = 0;
  for (let i = 0; i < CLUSTER.length; i++) {
    const [dx, dy, r, spin, ci] = CLUSTER[i];
    clG.color = CL_COL[ci];
    clG.style = ci === 1 ? 2 : 1;
    clG.thin = ci === 1 ? 0 : 0.25;
    clG.teeth = 12 + ((i * 5) % 9);
    clG.metal = ci === 1 ? 0 : 0.5;
    clG.squash = 0.75 + 0.2 * ((i * 3) % 4) / 3;
    clG.sqA = 0.4 + i;
    clG.seed = 0.1 * i;
    pack(out, n++, at[0] + dx * S, at[1] + dy * S, r * S, 0.3 * i + spin * tau, clG, 0);
  }
  return n;
}
