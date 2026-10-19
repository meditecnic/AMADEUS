// Small deterministic math helpers shared by the show timeline.

export const clamp = (x: number, a = 0, b = 1): number => (x < a ? a : x > b ? b : x);
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
export const smooth = (a: number, b: number, x: number): number => {
  const t = clamp((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};
export const easeOutCubic = (t: number): number => 1 - Math.pow(1 - clamp(t), 3);
export const easeInCubic = (t: number): number => Math.pow(clamp(t), 3);
export const easeOutExpo = (t: number): number => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * clamp(t)));
export const easeInQuad = (t: number): number => clamp(t) * clamp(t);
export const easeOutQuad = (t: number): number => 1 - (1 - clamp(t)) * (1 - clamp(t));

/** Integer hash -> [0,1). Pure function, stable across runs. */
export function hash1(n: number): number {
  let x = Math.imul((n | 0) ^ 0x9e3779b9, 0x85ebca6b);
  x ^= x >>> 13;
  x = Math.imul(x, 0xc2b2ae35);
  x ^= x >>> 16;
  return (x >>> 0) / 4294967296;
}

/** Seeded PRNG (mulberry32). */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type V2 = [number, number];

/** Monotone cubic (Fritsch-Carlson) through keyframes [t, v] sorted by t; clamped at the ends. */
export function pchip(keys: ReadonlyArray<readonly [number, number]>, t: number): number {
  const n = keys.length;
  if (t <= keys[0][0]) return keys[0][1];
  if (t >= keys[n - 1][0]) return keys[n - 1][1];
  let i = 0;
  while (i < n - 2 && t > keys[i + 1][0]) i++;
  const slope = (j: number): number => (keys[j + 1][1] - keys[j][1]) / (keys[j + 1][0] - keys[j][0]);
  const tangent = (j: number): number => {
    if (j === 0) return slope(0);
    if (j === n - 1) return slope(n - 2);
    const a = slope(j - 1), b = slope(j);
    if (a * b <= 0) return 0;
    const ha = keys[j][0] - keys[j - 1][0], hb = keys[j + 1][0] - keys[j][0];
    const w1 = 2 * hb + ha, w2 = hb + 2 * ha;
    return (w1 + w2) / (w1 / a + w2 / b);
  };
  const [t0, v0] = keys[i];
  const [t1, v1] = keys[i + 1];
  const h = t1 - t0;
  const u = (t - t0) / h;
  const m0 = tangent(i) * h, m1 = tangent(i + 1) * h;
  const u2 = u * u, u3 = u2 * u;
  return (2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * m1;
}
