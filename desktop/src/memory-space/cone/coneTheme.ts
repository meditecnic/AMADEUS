// Two world lines, same geometry (DIRECTION 15.6). The colours match the sheet's --m-* roles. The chart is drawn on the
// workstation's sheet, so the paper (the matte band, hollow stars) is that sheet's tone: the shaders take a hex's
// working-space channels, so these hexes are the ones whose linear values come out a shade under the sheet
// (SG about #222729 on screen, β about #141b19).
import { Color } from 'three';

import type { RGB } from './coneFrame';

export type ConeWorldline = 'steins_gate' | 'beta';

export type ConeTheme = {
  paper: string;
  ink: string;
  acc: string;
  her: string;
  /** Structure lines: paper-white ink on SG, rust copper on β. */
  struct: string;
  structA: number;
  /** β draws its rings dotted. */
  dots: boolean;
  /** Springs: frequency in Hz and damping ratio. */
  spring: { f: number; z: number };
  /** β moves 1.3 times slower. */
  k: number;
};

export const CONE_THEMES: Record<ConeWorldline, ConeTheme> = {
  steins_gate: {
    paper: '#5e6467', ink: '#ecebe7', acc: '#8fdcff', her: '#ff9cc3', struct: '#ecebe7',
    structA: 1, dots: false, spring: { f: 1.55, z: 1 }, k: 1,
  },
  beta: {
    paper: '#495551', ink: '#ede6dd', acc: '#e98a4f', her: '#c9a063', struct: '#e98a4f',
    structA: 1.25, dots: true, spring: { f: 1.2, z: 0.74 }, k: 1.3,
  },
};

/** Shader colours are the working-space channels of the hex, as in the approved mock-up. */
export function rgb(hex: string): RGB {
  const c = new Color(hex);
  return [c.r, c.g, c.b];
}
