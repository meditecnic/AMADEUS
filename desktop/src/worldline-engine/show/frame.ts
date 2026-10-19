// Everything the renderer needs for one frame. Built by the timeline as a pure function of
// (show time, context), so seek() renders the same image every time.
import type { V2 } from './math';

/** Which background world fills the scene pass. */
export type Bg = 'parch' | 'corridor' | 'space' | 'dark' | 'black';

/** Mottled parchment (doors shot, lock, hold, standby, end frames) and the plain gear field. */
export interface ParchState {
  zoom: number;
  off: V2;
  time: number;
  bright: number; // white glow behind the instrument
  dark: number; // 0..1 darken (standby / fail)
  cool: number; // 0..1 desaturate toward a cold grey (fail)
  doors: number; // 0..1 the white door corridor
  doorZ: number; // frames passed in the door corridor (floor scroll)
  lattice: number; // mirrored mechanical lattice at the sides (door pass)
  plain: number; // 0..1 replace the parchment by the bare beige gear field
  plainDark: number; // gear field darkening (late gear shot)
  specks: number;
  stain: number; // denser smoke stains (doors shot)
  /** R2-03: the lock paper's big soft ink clouds, pink-brown tone and heavy bottom corners */
  lockInk: number;
  warp: number; // pincushion lens strength K of the door flight, s = p (1 + K |p|^4) (background only)
  fz: [number, number, number, number]; // depth of the T5 near door F0, then of the reveal backs of F1-F3
  vpY: number; // corridor vanishing point height
  sky: number; // 0..1 clean light sky with dark corners behind the door frames (T5/T6)
  ink: number[]; // up to 6 black pieces: centre x, y, half w, h (screen H units), flattened
}

/** Line-art doorway tunnel, dark door corridor, moss and cracks (shots T1-T3). */
export interface CorridorState {
  t: number;
  vp: V2;
  travel: number;
  ptravel: number;
  lines: number;
  spread: number; // copies present from the vanishing point out to this scale (log 0..1)
  fadeIn: number; // copies removed from the vanishing point outwards (0..1)
  pillar: number;
  solid: number;
  moss: number;
  crack: number;
  soft: number; // R1-08: the cracked lumps blur into a grainy mass
  diss: number; // dissolve into parchment
  reveal: number; // standby -> corridor through vertical streaks
  /** line-art copies: draw only the copy nearest this scale (0 = all) */
  only: number;
  /** line-art copies: draw every second copy (sparser nesting) */
  sparse: number;
  row: number; // single row of wire digits at the start of the tunnel
  /** R6-A, T1 only: row scale, emptied-centre radius and edge (r / half height), T1 look (0..1);
   *  [.71, 0, 0, 0] draws as R5 */
  t1: [number, number, number, number];
  /** R6-A, T1 only: radius and soft edge of the emptied centre; [0, 0, 0, 0] draws as R5 */
  t1b: [number, number, number, number];
  cells: Float32Array;
  frontPosts: [number, number]; // alpha, scale of the dark front posts (R1-07)
}

export interface SpaceState {
  t: number;
  reveal: number; // centre strip opening (0 closed .. 1 fully open)
  vp: V2;
  /** R1-13: roll of the space picture about the vanishing point (radians) */
  roll: number;
  /** R1-13: the lateral sweep: the nebula drawn out into horizontal streaks (0..1) */
  sweep: number;
  /** R3-02: black ink blobs inside the slit on the cut frames, flattened x, y, r (stage units on
   * the cut frame), k (1/s: each blob flies out from the burst centre as exp(k * time)) */
  burst: number[];
}

export interface DarkState {
  t: number;
  phase: number; // 0 light arc over the planet, 1 helix warp
  rot: number;
  sweep: number; // 0..1 head of the light band travelling round the arch
  width: number; // 0..1 band width growth
  archC: V2;
  archR: V2;
  white: number; // white floods outside the arch
  white2: number; // ... and inside it
  ground: number;
  helix: number;
  rib: number;
  wisp: number;
  /** T9 eye: comet heads (screen x) of the lower and upper limb */
  headL: number;
  headU: number;
  /** share of the band light drawn again in front of the tubes */
  front: number;
  /** the eye's hole opening up into the tunnel */
  hole: number;
  /** inside the tunnel (dark ribbed vault, white floor) */
  tunnel: number;
  floor: number;
}

export interface SmokeState {
  k: number; // 0 cloud .. 1 condensed into the case
  a: number;
  time: number;
  ro: [number, number, number, number];
}

export interface GearState {
  data: Float32Array;
  count: number;
}

export interface CableState {
  data: Float32Array; // x, y, across, shade per vertex (triangles)
  back: number; // vertex count drawn behind the instrument
  front: number; // vertex count drawn in front
  alpha: number;
}

export interface PieceState {
  data: Float32Array; // 12 floats per piece
  back: number;
  front: number; // pieces [back, back + front) are drawn in front of the instrument
  snap: ParchState; // the paper the pieces are cut from
}

export interface ReadoutState {
  digA: Float32Array;
  digB: Float32Array;
  mix: Float32Array;
  lit: Float32Array;
  flash: Float32Array;
  glow: number;
  /** R1-14: wide orange glow round the lit digits (ghost mode, T8) */
  halo: number;
  plinth: number;
  glass: number;
  envDark: number;
  /** clear glass in the dark exit (mesh / stack mostly see-through, warm haze) */
  clear: number;
  /** R2-04: the near tubes' glass glows warm white inside the T10 tunnel (src 9.42-9.45) */
  tglow?: number;
  /** R1-25 (R4): the bright glass rims / speculars fade out (exit burst, src 13.83-13.87) */
  rimFade?: number;
  mode: number; // 0 real, 1 ghost (overexposed, translucent), 2 line art
  blur: number; // digit softness (atlas lod bias)
  lineK: number; // line art intensity
  ro: [number, number, number, number]; // scale, offX, offY, roll
  view: [number, number, number]; // yaw, pitch, 1/distance (0 = flat)
  /** instrument-only pincushion: centre x, y (screen), strength k, power n: R = r (1 + k r^n) */
  warp: [number, number, number, number];
  /** extra height growth toward the sides (inside the tube) */
  warpY: number;
  /** R3-06 (R4): screen-space lift of the end tubes on the tube wall (t6, t7 up; t0 down), fitted to src 9.42-9.53 */
  warpB?: [number, number, number];
  /** 1: of the case only the circuit board is drawn (T9: a thin plate seen from low) */
  boardOnly: number;
  shown: string;
  locked: boolean[];
}

export interface PostState {
  flash: number;
  flashColor: [number, number, number];
  ca: number;
  grain: number;
  fade: number;
  vig: number;
  exposure: number;
  bloom: number;
  tear: number;
  tearSeed: number;
  slipA: [number, number, number]; // band y0, y1 (frame units, y up), sideways offset
  slipB: [number, number, number];
  zoom: number;
  center: V2; // uv centre of the radial effects
  lens: number; // >0 barrel (edges squeezed), <0 pincushion (edges stretched outward)
  flare: number;
  red: number; // red edge glow (trigger)
  streak: number; // vertical smear (trigger)
  pin: number; // edge-only pincushion (centre unchanged), the exit push
  /** 0: red out / blue in fringes; 1: magenta / green fringes (the exit burst, src 13.83-13.97) */
  caMode: number;
  /** R1-25: 0..1 towards red-out / cyan-in fringes (the burst capsules, src 13.83-13.90) */
  caCyan: number;
  /** R1-25: 0..1 keeps the floor under the tubes dark (only its dim parts) */
  floorDark: number;
  /** weight of the long horizontal streak of the light core (R1-23: none in T16) */
  flareStreak: number;
  /** weight of the soft rays of the light core */
  flareRays: number;
  /** warm cream wash over the upper picture (the end of the push, src 13.77-13.87) */
  veil: number;
}

export interface FrameState {
  time: number;
  shot: string;
  bg: Bg;
  parch: ParchState;
  corridor: CorridorState | null;
  space: SpaceState | null;
  dark: DarkState | null;
  gears: GearState | null;
  gearsBehind: boolean;
  smoke: SmokeState | null;
  cables: CableState | null;
  pieces: PieceState | null;
  readout: ReadoutState;
  showReadout: boolean;
  post: PostState;
  grainFrame: number;
}
