/**
 * HOT WIRE palette for code that paints pixels (canvas). The CSS side lives in hotwire.css; keep the two in step.
 * design/references.md §2.1 (amadeus-ops) is the source of these values.
 */
export type Tone = 'sg' | 'beta';
type RGB = readonly [number, number, number];

export interface ToneColors {
  /** Her signal: the dot sphere and the scope. */
  her: RGB;
  /** Hot wire glow. */
  hot: RGB;
  /** White-hot core of a wire or stroke. */
  core: RGB;
  /** Secondary text, ticks. */
  ash: RGB;
  /** Binary columns. */
  bin: RGB;
}

export const TONE: Record<Tone, ToneColors> = {
  sg: { her: [255, 156, 195], hot: [143, 220, 255], core: [234, 249, 255], ash: [147, 163, 194], bin: [124, 168, 226] },
  beta: { her: [255, 162, 74], hot: [255, 107, 31], core: [255, 215, 161], ash: [179, 162, 145], bin: [255, 132, 60] },
};

export const toneOf = (worldline: 'steins_gate' | 'beta'): Tone => (worldline === 'beta' ? 'beta' : 'sg');

export const rgba = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`;
