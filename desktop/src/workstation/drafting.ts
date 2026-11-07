/** Small geometry helpers for the drafting figures (plate user space, y down). */

export const f1 = (v: number) => Math.round(v * 10) / 10;

export function polar(r: number, deg: number): [number, number] {
  const a = (deg * Math.PI) / 180;
  return [f1(r * Math.cos(a)), f1(r * Math.sin(a))];
}
const pt = (r: number, deg: number) => polar(r, deg).join(' ');

/** Spur gear outline: straight flanks between root and tip circles, one tooth centred on 0°. */
export function gearOutline(n: number, r: number, m = (2 * r) / n) {
  const ro = r + m * 0.92;
  const rr = r - m * 1.18;
  const s = 360 / n;
  let d = '';
  for (let k = 0; k < n; k++) {
    const a = k * s;
    d += `${k ? 'L' : 'M'}${pt(rr, a - s * 0.5)}L${pt(rr, a - s * 0.29)}L${pt(ro, a - s * 0.15)}L${pt(ro, a + s * 0.15)}L${pt(rr, a + s * 0.29)}`;
  }
  return `${d}Z`;
}

/** Escape wheel: n hooked, pointed teeth (a long leading face, a short back), as on a clock's going train. */
export function escapeWheel(n: number, r: number) {
  const root = r * 0.84;
  const s = 360 / n;
  let d = '';
  for (let k = 0; k < n; k++) {
    const a = k * s;
    d += `${k ? 'L' : 'M'}${pt(root, a)}L${pt(r, a + s * 0.18)}L${pt(root * 1.01, a + s * 0.62)}`;
  }
  return `${d}Z`;
}

export function arc(r: number, from: number, to: number) {
  const large = Math.abs(to - from) > 180 ? 1 : 0;
  return `M${pt(r, from)}A${r} ${r} 0 ${large} 1 ${pt(r, to)}`;
}

/** n radial ticks between r0 and r1 (every `major`-th from r0 to rMajor), starting at -90° (twelve o'clock). */
export function ticks(n: number, r0: number, r1: number, major = 0, rMajor = r1) {
  let d = '';
  for (let k = 0; k < n; k++) {
    const a = -90 + (360 * k) / n;
    d += `M${pt(r0, a)}L${pt(major && k % major === 0 ? rMajor : r1, a)}`;
  }
  return d;
}

/**
 * The Lorenz system (σ = 10, ρ = 28, β = 8/3), integrated with RK4 and projected on the x–z plane: the
 * butterfly. Returns an SVG path centred on the origin, z running up the page.
 */
export function lorenzPath(steps = 5200, dt = 0.006, k = 13) {
  const S = 10, R = 28, B = 8 / 3;
  const d = (x: number, y: number, z: number): [number, number, number] => [S * (y - x), x * (R - z) - y, x * y - B * z];
  let x = 0.1, y = 0, z = 0;
  let out = '';
  for (let i = 0; i < steps + 400; i++) {
    const [a1, b1, c1] = d(x, y, z);
    const [a2, b2, c2] = d(x + (a1 * dt) / 2, y + (b1 * dt) / 2, z + (c1 * dt) / 2);
    const [a3, b3, c3] = d(x + (a2 * dt) / 2, y + (b2 * dt) / 2, z + (c2 * dt) / 2);
    const [a4, b4, c4] = d(x + a3 * dt, y + b3 * dt, z + c3 * dt);
    x += (dt / 6) * (a1 + 2 * a2 + 2 * a3 + a4);
    y += (dt / 6) * (b1 + 2 * b2 + 2 * b3 + b4);
    z += (dt / 6) * (c1 + 2 * c2 + 2 * c3 + c4);
    if (i < 400) continue; // let it settle onto the attractor first
    out += `${i === 400 ? 'M' : 'L'}${f1(x * k)} ${f1(-(z - 25) * k)}`;
  }
  return out;
}
