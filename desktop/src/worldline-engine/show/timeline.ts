// The director. Every frame is a pure function of (show time, context), so the same input always
// renders the same image (used by seek() for capture). Shot times follow the original recording:
// prototype t = source t - 3.90.
//
//  0.00 T0  起势        standby tears (slices), darkens with red edges, vertical streaks reveal
//  0.53 T1  线稿门廊    nested line-art copies of the instrument + tan door posts on dark marble
//  1.67 T2  暗门框走廊  lines gone, posts solid, forward
//  1.93 T3  苔藓/裂纹   moss from the right (1.95), pale cracked plates (2.10), dissolve 2.35-2.47
//  2.20 T4  烟凝底座    (overlaps T3) black ink threads round the tubes condense into the case by 2.98
//  2.60 T5  门廊读数    frontal instrument, white door corridor; digits start to roll at 3.05
//  3.70 T6  穿门        through the doors (barrel), side lattice; ghost look only at the cut (4.00-4.05)
//  4.03 T7  黑碎块/行星 centre slit opens on a sepia nebula: fragments, planet, moon; ghost tubes
//  4.57 T8  齿轮群      hard cut into a gear cloud, rolling push; ghost tubes behind
//  5.10 T9  暗场光弧    line-art instrument, ring -> white swoosh round an arch, diagonal lower limb,
//                       white flood 5.36-5.53; outer tubes thrown out by an edge-only pincushion
//  5.50 T9→T10 筒形内部 dark ribbed vault, white floor, readout bent round the wall
//  5.575 T10 螺旋/光带  line art close, DNA strands, ribbons, radial speed
//  6.10 T11 闪白        one white frame, then the lock framing relaxes from a wide lens
//  6.20 GATE            pending result: wait here, never lock
//  6.13 T12 逐位锁定    strictly left to right (6.13 6.23 6.63 6.88 7.13 7.38 7.63)
//  7.63 T13 静持        slow push, cables sway
//  9.10 T14 黑墨吞纸    black lobes eat the paper from the edges (and in patches), shards fly in
//  9.36 T15 胶囊放射    the remaining pieces are pulled into radial capsules, white streak burst
//  9.43 T16 橙色散景    orange bokeh fan out sideways/down; the glass turns see-through on black
//  9.50 T17 色偏推近    push 1.0->1.31 (measured tube pitch), edge pincushion 9.78-9.90,
//                       radial burst + RGB split + exposure 9.90-10.05
// 10.07 T18 白          white; 10.15..10.55 white burns off onto the locked end frame
import { clamp, lerp, smooth, hash1, easeOutCubic, pchip } from './math';
import type { V2 } from './math';
import { DOT, STAGE_ASPECT, pivotOff, roScreen } from './geom';
import type { FrameState, ParchState, PostState, ReadoutState, CorridorState, CableState, PieceState, DarkState } from './frame';
import { poseCloud, poseLone, MAX_GEARS, GEAR_STRIDE } from './gears';
import { cableFrame } from './cables';
import { pieceFrame, strobeFrame } from './pieces';

export const T = {
  TRIG_END: 0.53,
  TUN_END: 1.67,
  COR_END: 1.93,
  MOSS_END: 2.47,
  SMOKE_END: 2.6,
  DOORS_END: 3.7,
  SPACE0: 4.03,
  GEAR0: 4.57,
  // R6-D: the cut into the gears comes one 60 fps frame earlier than GEAR0 (src 8.451 -> 8.468, the
  // last nebula frame and the first gear frame; R5 / R6 cut 4.567 -> 4.583); the shot holds its
  // first pose (u = 0) until GEAR0
  GEARCUT: 4.56,
  DARK0: 5.1,
  DARK2: 5.55,
  TUBE9: 5.517,
  HELIX0: 5.575,
  FLASH0: 6.1,
  LOCK0: 6.13,
  GATE: 6.2,
  HOLD0: 7.63,
  INK0: 9.1,
  CAPS0: 9.4,
  BOKEH0: 9.52,
  PUSH0: 9.73,
  WHITE0: 10.07,
  WHITE_END: 10.15,
  S_END: 10.55,
  F_SLIP: 6.3,
  F_ROLL: 7.2,
  F_CUT: 8.3,
  F_END: 8.7,
} as const;

/** When each digit starts rolling (doors shot) and locks (lock shot), from the original. */
const ROLL_AT = [3.05, 0, 3.2, 3.4, 3.45, 3.55, 3.75, 3.85];
const LOCK_AT = [6.13, 0, 6.23, 6.63, 6.88, 7.13, 7.38, 7.63];
const RATE = 20;

export type Scenario = 'success' | 'slow' | 'fail';
export type Outcome = 'success' | 'fail';

/** Wall-clock delay (s) after "start" at which the local mock answers. */
export const MOCK_DELAY: Record<Scenario, number> = { success: 2.6, slow: 9.2, fail: 3.4 };
export const MOCK_OUTCOME: Record<Scenario, Outcome> = { success: 'success', slow: 'success', fail: 'fail' };

export interface ShowContext {
  origin: number[];
  target: number[];
  outcome: Outcome | null;
  waitT: number;
  /** show time at which the result became known (locks never happen before it) */
  resultT?: number;
}

// ---------------------------------------------------------------- helpers

export function parseReading(s: string): number[] | null {
  const m = /^(\d)\.(\d{6})$/.exec(s.trim());
  if (!m) return null;
  return [Number(m[1]), DOT, ...m[2].split('').map(Number)];
}

export function formatCells(cells: ArrayLike<number>): string {
  let s = '';
  for (let i = 0; i < cells.length; i++) s += cells[i] === DOT ? '.' : String(Math.round(cells[i]));
  return s;
}

function hashDigit(i: number, k: number): number {
  const d = Math.floor(hash1(i * 7919 + k * 104729 + 17) * 10);
  const p = Math.floor(hash1(i * 7919 + (k - 1) * 104729 + 17) * 10);
  return d === p ? (d + 3) % 10 : d;
}

function scramble(i: number, t: number, rate: number): number {
  return hashDigit(i, Math.floor(t * rate + i * 2.37));
}

const gearBuf = new Float32Array(MAX_GEARS * GEAR_STRIDE);

function newReadout(): ReadoutState {
  return {
    digA: new Float32Array(8),
    digB: new Float32Array(8),
    mix: new Float32Array(8),
    lit: new Float32Array(8).fill(1),
    flash: new Float32Array(8),
    glow: 1,
    halo: 0,
    plinth: 1,
    glass: 1,
    envDark: 0,
    clear: 0,
    mode: 0,
    blur: 0,
    lineK: 1,
    ro: [1, 0, 0, 0],
    view: [0, 0, 0],
    warp: [0, 0, 0, 4],
    warpY: 0,
    boardOnly: 0,
    shown: '',
    locked: new Array(8).fill(false),
  };
}

function parch(o: Partial<ParchState> = {}): ParchState {
  return { zoom: 1, off: [0, 0], time: 0, bright: 0.8, dark: 0, cool: 0, doors: 0, doorZ: 0, lattice: 0, plain: 0, plainDark: 0, specks: 1, stain: 0, lockInk: 0, warp: 0, vpY: 0, fz: [0, 0, 0, 0], sky: 0, ink: [], ...o };
}

function post(o: Partial<PostState> = {}): PostState {
  return {
    flash: 0,
    flashColor: [1, 1, 1],
    ca: 0,
    grain: 0.03,
    fade: 0,
    vig: 0.3,
    exposure: 1,
    bloom: 0.6,
    tear: 0,
    tearSeed: 0,
    slipA: [0, 0, 0],
    slipB: [0, 0, 0],
    zoom: 0,
    center: [0.5, 0.5],
    lens: 0,
    flare: 0,
    red: 0,
    streak: 0,
    pin: 0,
    caMode: 0,
    caCyan: 0,
    floorDark: 0,
    flareStreak: 1,
    flareRays: 1,
    veil: 0,
    ...o,
  };
}

type FrameParts = Partial<FrameState> & { readout: ReadoutState };

function frame(t: number, shot: string, o: FrameParts): FrameState {
  return {
    time: t,
    shot,
    bg: 'parch',
    parch: parch({ time: t }),
    corridor: null,
    space: null,
    dark: null,
    gears: null,
    gearsBehind: false,
    smoke: null,
    cables: null,
    pieces: null,
    showReadout: true,
    post: post(),
    grainFrame: Math.floor(t * 24),
    ...o,
  };
}

function finish(ro: ReadoutState): ReadoutState {
  ro.shown = formatCells(ro.digA.map((d, i) => (ro.mix[i] > 0.5 ? ro.digB[i] : d)));
  return ro;
}

/** Digits: origin until each one's roll start, then rolling; optional locks at given times. */
function setDigits(ro: ReadoutState, t: number, ctx: ShowContext, rollAt: number[] | null, lockAt: number[] | null, rate = RATE): void {
  for (let i = 0; i < 8; i++) {
    if (i === 1) {
      ro.digA[i] = DOT;
      ro.locked[i] = lockAt !== null && t >= lockAt[0];
      continue;
    }
    if (lockAt && t >= lockAt[i]) {
      ro.digA[i] = ctx.target[i];
      ro.locked[i] = true;
      // R3 (R1-01): no flash when a digit locks - the 60 fps source frames show none
      ro.flash[i] = 0;
    } else if (rollAt && t < rollAt[i]) {
      ro.digA[i] = ctx.origin[i];
    } else {
      ro.digA[i] = scramble(i, t, rate);
    }
  }
}

function lockTimes(ctx: ShowContext): number[] {
  const rt = ctx.resultT ?? 0;
  let k = 0;
  return LOCK_AT.map((L, i) => (i === 1 ? 0 : Math.max(L, rt + 0.06 + 0.1 * k++)));
}

/** Placement of the instrument: scale about the case top, plus a shift and roll. */
function place(ro: ReadoutState, s: number, dx = 0, dy = 0, rot = 0): void {
  const [ox, oy] = pivotOff(s, dx, dy);
  ro.ro = [s, ox, oy, rot];
}

const lockScale = (t: number): number => 0.985 + 0.0157 * (t - 6.37);
const doorScale = (t: number): number => 0.928 + 0.0217 * clamp(t - 2.6, 0, 1.45);

function uvOf(p: V2): V2 {
  return [p[0] / STAGE_ASPECT + 0.5, p[1] + 0.5];
}

// ---------------------------------------------------------------- standby / end frames

export function standbyFrame(cells: number[], failed: boolean, t = 0): FrameState {
  const ro = newReadout();
  for (let i = 0; i < 8; i++) ro.digA[i] = cells[i];
  place(ro, failed ? 1 : 0.928);
  ro.envDark = 0.85;
  ro.glow = failed ? 0.55 : 0.95;
  return frame(t, failed ? '失败结局（暗场，原读数）' : '待机（原读数）', {
    parch: parch({ time: t, dark: failed ? 0.8 : 0.88, cool: failed ? 1 : 0, bright: 0.4 }),
    readout: finish(ro),
    cables: failed ? cableFrame(0, ro.ro, 0.8, true) : null,
    post: post({ grain: 0.03, vig: 0.5 }),
  });
}

export function successEndFrame(target: number[], t = 0, flash = 0): FrameState {
  const ro = newReadout();
  for (let i = 0; i < 8; i++) {
    ro.digA[i] = target[i];
    ro.locked[i] = true;
  }
  place(ro, 1);
  return frame(t, '成功结局（新读数已锁定）', {
    parch: parch({ time: 9.0 + t, bright: 0.85 }),
    readout: finish(ro),
    cables: cableFrame(9.0 + t, ro.ro, 1, true),
    post: post({ flash, grain: 0.03, vig: 0.3 }),
  });
}

// ---------------------------------------------------------------- shots

function corridorState(o: Partial<CorridorState>, ctx: ShowContext, t: number): CorridorState {
  const cells = new Float32Array(8);
  for (let i = 0; i < 8; i++) cells[i] = ctx.origin[i];
  return {
    t,
    vp: [0, 0],
    travel: 0,
    ptravel: 0,
    lines: 0,
    spread: 0,
    fadeIn: 0,
    pillar: 0,
    solid: 0,
    moss: 0,
    crack: 0,
    soft: 0,
    diss: 0,
    frontPosts: [0, 1],
    reveal: 1,
    row: 0,
    only: 0,
    sparse: 0,
    t1: [0.71, 0, 0, 0],
    t1b: [0, 0, 0, 0],
    cells,
    ...o,
  };
}

function shotTrigger(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  for (let i = 0; i < 8; i++) ro.digA[i] = ctx.origin[i];
  place(ro, 0.928);
  ro.envDark = 0.85;
  const out = smooth(0.26, 0.42, t);
  ro.glass = 1 - out;
  ro.glow = 0.95 * (1 - out);
  ro.plinth = 1 - out;
  const tear = t < 0.16 ? smooth(0.02, 0.04, t) * (1 - smooth(0.13, 0.17, t)) : 0;
  const pst = post({
    tear,
    tearSeed: Math.floor(t * 30) + 1,
    exposure: 1 - 0.55 * smooth(0.1, 0.24, t) + 0.25 * smooth(0.36, 0.53, t),
    red: smooth(0.12, 0.2, t) * (1 - smooth(0.3, 0.42, t)),
    streak: smooth(0.24, 0.32, t) * (1 - smooth(0.46, 0.53, t)),
    vig: 0.5,
    ca: 0.008 * tear,
  });
  if (t < 0.27) {
    return frame(t, 'T0 起势', {
      parch: parch({ time: t, dark: 0.88, bright: 0.4 }),
      readout: finish(ro),
      post: pst,
    });
  }
  return frame(t, 'T0 起势', {
    bg: 'corridor',
    corridor: corridorState(
      {
        reveal: smooth(0.27, 0.53, t),
        pillar: 0.55,
        ptravel: t < 0.5 ? 0.25 * t : ptravelT1(t),
        row: smooth(0.46, 0.53, t),
        ...(t >= 0.46 ? { t1: t1Look(t) } : {}),
      },
      ctx,
      t,
    ),
    readout: finish(ro),
    post: pst,
  });
}

// R6-A (U 17/18; src 4.36-5.57 measured per frame at 60 fps, proto = src - 3.90).
// The rush: from 0.50 the corridor comes at the camera - the zoom per frame starts at ~.06-.08
// and has decayed to ~0 by 0.90 (fitted (1 - u)^1.32), then the picture holds nearly still
// until 1.40 (~.0014 / frame). rushE = share of the rush done by t.
function rushE(t: number): number {
  const u = clamp((t - 0.5) / 0.4);
  return 1 - Math.pow(1 - u, 2.32);
}
/** door frames passed during the rush; creep during the hold (frames / s). The posts carry most
 *  of the picture's zoom: 1.105 gives a mean .052 / frame over 0.53-0.70 (src .056; 2.105 gave
 *  .077) and .0125 over 0.70-0.90 (src .015) - r617z, 480 */
const RUSH_P = 1.105;
const HOLD_P = 0.1;
/** copy layers passed during the rush; creep during the hold (layers / s). RUSH_C lands the
 *  copies on R5's travel + 6 at 1.659 (same picture: whole, even layer count) */
const HOLD_C = 0.7;
const RUSH_C = 6.9108 - 0.759 * HOLD_C;
/** posts before 1.6: only fract(ptravel) is drawn there (the machinery that reads the whole
 *  value starts at 1.6), and the value lands on R5's + 1 at 1.6 */
function ptravelT1(t: number): number {
  if (t < 0.9) return 0.25 * t + RUSH_P * rushE(t);
  return 0.225 + RUSH_P + HOLD_P * (t - 0.9);
}
function travelT1(t: number): number {
  if (t < 0.9) return 1.2 * Math.max(t - 0.53, 0) + RUSH_C * rushE(t);
  return 0.444 + RUSH_C + HOLD_C * (t - 0.9);
}
// the old reading as a row of bold wire digits, coming at the camera (src: outer digit at
// +.17 W at 4.417, +.19 at 4.483, +.225 at 4.55, +.27 at 4.60, +.34 at 4.667): row scale per time
const ROW_KEYS: ReadonlyArray<readonly [number, number]> = [
  [0.46, 0.62], [0.517, 0.67], [0.583, 0.75], [0.65, 0.89], [0.7, 1.06], [0.734, 1.19], [0.767, 1.34],
];
// the digits trail back from the row first (src 4.53-4.65): log2 of the largest copy showing them
const TRAIL_KEYS: ReadonlyArray<readonly [number, number]> = [
  [0.58, -4], [0.62, -1.4], [0.66, -0.4], [0.72, 0.15], [0.8, 0.3], [1.0, 0.6],
];
function t1Look(t: number): [number, number, number, number] {
  // z: the digits on the copies go first (src: gone from 5.38 but on alternate frames - not copied)
  return [pchip(ROW_KEYS, t), t < 0.58 ? -9 : pchip(TRAIL_KEYS, t), smooth(1.42, 1.485, t), t < 1.659 ? 1 : 0];
}
// 18 (src 5.32-5.57, 60 fps; rings round the vanishing point, upper half, share of the 5.30
// lines left: inner r/R .05-.20 ~.27 from 5.383, mid .20-.45 ~.75 at 5.383-5.45 and ~.45 from
// 5.467, outer .45-.70 ~.9 to 5.42, ~.6 from 5.467). Back to front: first the lines next to the
// vanishing point (by 5.383 the centre is empty under the arches but for the bright column), the
// fans of the middle copies hold to ~5.45, then the copies go by size - two or three layers of
// domes left at 5.467-5.55 at full strength, one at 5.567 (R5's single copy from 1.659). The
// digits on the copies go first (src: off on alternate frames 5.35-5.43 - not copied; gone from
// 5.45). CENTRE_KEYS: radius of the emptied centre (r / half height).
const CENTRE_KEYS: ReadonlyArray<readonly [number, number]> = [
  [1.44, 0], [1.467, 0.13], [1.483, 0.22], [1.517, 0.26], [1.55, 0.28], [1.659, 0.3],
];
function t1Centre(t: number): [number, number, number, number] {
  if (t < 1.44 || t >= 1.659) return [0, 0, 0, 0];
  return [pchip(CENTRE_KEYS, t), 0.06, smooth(1.44, 1.55, t), 0];
}
// minS (fadeIn = (log2 minS + 6) / 9.5). #1 (src 4.85-5.30 at 1080p): the fans of copies stop
// short of the vanishing point, the centre is a dark gap round a bright column, not a glare - the
// smallest copies are left out (minS ~.034); then 18: .1 at 1.483, .25 1.517, .35 1.533, .6 1.55,
// 1.3 1.567, 1.45 1.60, 1.55 1.633, 1.6 1.659
const FADE_KEYS: ReadonlyArray<readonly [number, number]> = [
  [0.53, 0], [0.75, 0.12], [1.44, 0.12], [1.483, 0.282], [1.517, 0.421], [1.533, 0.472], [1.55, 0.554], [1.567, 0.672],
  [1.6, 0.688], [1.633, 0.698], [1.659, 0.703],
];

/** log2 of the largest line copy on screen. R6-A (src 4.56-4.87): the copies trail back from
 *  the row of digits first (~.2 of the frame at 4.60), the domes come out at 4.65 (+-.3 W),
 *  +-.35 W at 4.70, +-.45 W by 4.85 - no sudden fan */
const SPREAD_KEYS: ReadonlyArray<readonly [number, number]> = [
  [0.53, -4.5], [0.66, -3.5], [0.7, -2.3], [0.75, -0.8], [0.8, -0.5], [0.88, -0.2], [0.97, 0.4], [1.05, 1],
];
function spreadL2(t: number): number {
  return pchip(SPREAD_KEYS, t);
}

// R1-04, measured frame by frame on the 60 fps source (src 5.517-5.70): the nested line art
// empties from the centre (only copies >~1/3 of the frame are left by 5.53), then drops to the
// single near copy (5.567), vanishes (5.584-5.60), flashes back as the single copy
// (5.617-5.65, fainter at 5.65) and is gone from 5.667
function tunnelLines(t: number): number {
  if (t < 1.675) return 1;
  if (t < 1.709) return 0;
  if (t < 1.742) return 1;
  if (t < 1.758) return 0.6;
  return 0;
}
function tunnelFadeIn(t: number): number {
  // minS = 2^(mix(-6, 3.5, fadeIn)): copies smaller than ~0.8 minS are dropped
  // (src 5.517-5.55: the innermost copy left is ~0.45 of the frame)
  if (t < 1.659) return pchip(FADE_KEYS, t);
  return 0.53 * smooth(1.3, 1.6, t);
}

function shotCorridor(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  for (let i = 0; i < 8; i++) ro.digA[i] = ctx.origin[i];
  const tun = t < T.TUN_END;
  const shot = tun ? 'T1 线稿门廊' : t < T.COR_END ? 'T2 暗门框走廊' : 'T3 苔藓/裂纹';
  const dc = Math.max(t - 1.62, 0);
  const fwd = 0.25 * t + 1.1 * dc + 1.6 * dc * dc;
  const c = corridorState(
    {
      travel: t < 1.659 ? travelT1(t) : 1.2 * Math.max(t - 0.53, 0),
      ptravel: t < 1.6 ? ptravelT1(t) : fwd,
      lines: tunnelLines(t),
      spread: (spreadL2(t) + 4.5) / 7.9,
      fadeIn: tunnelFadeIn(t),
      // (src 5.567 / 5.617: the single copy left spans the frame, outer tubes at x ~ +-.75)
      only: t >= 1.659 ? 1.75 : 0,
      // (R6-A: no sparse step at 1.40 - the copies leave back to front instead; as R5 from 1.659)
      sparse: t >= 1.659 ? 1 : 0,
      pillar: 0.6 + 0.3 * smooth(1.6, 1.75, t),
      solid: smooth(1.6, 1.75, t) * (1 - smooth(2.2, 2.36, t)),
      moss: smooth(1.95, 2.14, t),
      crack: smooth(2.1, 2.24, t),
      // (src 6.20-6.30: the lumps blur into grain)
      soft: smooth(2.23, 2.36, t),
      diss: smooth(2.43, 2.47, t),
      frontPosts: [smooth(2.0, 2.12, t), 1.0 + 0.35 * smooth(2.05, 2.6, t)],
      row: 1 - smooth(0.66, 0.74, t),
      ...(t < 1.659 ? { t1: t1Look(t), t1b: t1Centre(t) } : {}),
    },
    ctx,
    t,
  );
  place(ro, doorScale(t));
  // (src 6.20: the lumps still fill the frame; the dark mass gathers in the middle by 6.33)
  const smoke = t > 2.24 ? { k: smooth(2.33, 2.82, t), a: smooth(2.25, 2.40, t) * (0.55 + 0.45 * smooth(2.38, 2.47, t)), time: t, ro: ro.ro } : null;
  return frame(t, shot, {
    bg: 'corridor',
    corridor: c,
    smoke,
    readout: finish(ro),
    showReadout: false,
    post: post({ vig: 0.45, bloom: 0.75, grain: 0.035, ca: 0.0035, streak: 0.28 * smooth(1.95, 2.1, t) * (1 - smooth(2.12, 2.2, t)) }),
  });
}

// R2-01: door frames F0-F3 and the lens of the flight, fitted to the 60 fps source frames (show
// time = src - 3.90). Values: screen offset of the back edge of each frame's reveal on the horizon
// before the lens (H units). Rows were tracked on both jambs and fitted with s = p (1 + K |p|^4)
// (rms .2-1.1 px); F1 6.90-7.30 from the jamb foot below the case.
// F0 (the T5 near frame) reveal back edge, screen offset before the lens (src 6.451-7.25, every
// 3rd frame; reveal bands at 1080p y=300)
const F0_KEYS: ReadonlyArray<readonly [number, number]> = [
  [2.45, 0.56], [2.551, 0.573], [2.65, 0.59], [2.75, 0.61], [2.8, 0.62], [2.9, 0.64], [2.95, 0.648], [3.0, 0.659],
  [3.05, 0.67], [3.1, 0.68], [3.15, 0.684], [3.2, 0.693], [3.25, 0.70], [3.3, 0.712], [3.35, 0.726], [3.4, 0.78],
  [3.45, 1.0], [3.6, 2.0],
];
const F1_KEYS: ReadonlyArray<readonly [number, number]> = [
  [2.6, 0.345], [3.0, 0.37], [3.2, 0.386], [3.3, 0.4], [3.4, 0.4175], [3.5, 0.4475], [3.583, 0.4925], [3.6, 0.504],
  [3.633, 0.531], [3.668, 0.584], [3.684, 0.6065], [3.7, 0.632], [3.717, 0.6595], [3.733, 0.6925], [3.8, 0.79],
  [3.85, 0.95], [3.9, 1.25], [4.0, 2.2],
];
const F2_KEYS: ReadonlyArray<readonly [number, number]> = [
  [2.6, 0.19], [3.4, 0.29], [3.7, 0.328], [3.75, 0.33], [3.767, 0.3425], [3.784, 0.351], [3.8, 0.368], [3.817, 0.3765],
  [3.834, 0.394], [3.85, 0.411], [3.868, 0.436], [3.884, 0.461], [3.901, 0.494], [3.918, 0.531], [3.95, 0.62],
  [3.984, 0.72], [4.018, 0.85], [4.05, 1.0], [4.2, 2.0],
];
const F3_KEYS: ReadonlyArray<readonly [number, number]> = [
  [2.6, 0.1], [3.7, 0.175], [3.8, 0.2], [3.9, 0.27], [3.95, 0.325], [4.001, 0.42], [4.034, 0.48], [4.2, 0.9],
];
// lens strength K (r^4 pincushion): .23 at src 7.30, .45 at 7.60, 1.1-1.3 while F2 passes, 1.6+ at 7.85
const WARP_KEYS: ReadonlyArray<readonly [number, number]> = [
  [3.05, 0], [3.4, 0.23], [3.5, 0.31], [3.6, 0.37], [3.633, 0.4], [3.668, 0.43], [3.7, 0.45], [3.733, 0.5],
  [3.767, 1.12], [3.8, 1.15], [3.85, 1.25], [3.918, 1.3], [3.95, 1.6], [4.034, 1.7],
];
const WO = 0.52;

/** door-corridor part of the parchment state for show time t */
function corridorDoors(t: number): Pick<ParchState, 'fz' | 'warp' | 'vpY' | 'doorZ'> {
  const c1 = pchip(F1_KEYS, t);
  return {
    fz: [WO / pchip(F0_KEYS, t), WO / c1, WO / pchip(F2_KEYS, t), WO / pchip(F3_KEYS, t)],
    warp: pchip(WARP_KEYS, t),
    vpY: 0,
    doorZ: Math.log(c1 / 0.345) / Math.log(1.85),
  };
}

// black ink pieces at the end of the flight (60 fps src 7.834 / 7.850 (= 7.868) / 7.884 / 7.901 /
// 7.918; dark components L < 45 at 1080p, boxes x0, y0, x1, y1), one set per source frame
const INK_FRAMES: ReadonlyArray<ReadonlyArray<readonly [number, number, number, number]>> = [
  [[920, 266, 1018, 276]],
  [[906, 250, 1016, 260], [896, 264, 929, 306], [880, 308, 936, 347]],
  [[906, 250, 1016, 260], [896, 264, 929, 306], [880, 308, 936, 347]],
  [[913, 230, 1025, 241], [932, 253, 963, 293], [880, 245, 938, 347]],
  [[867, 204, 980, 215], [838, 220, 915, 292], [738, 294, 805, 384]],
  [[899, 170, 1032, 181], [879, 186, 983, 344], [917, 352, 978, 394], [1056, 880, 1212, 898], [1076, 901, 1214, 915]],
];
function inkPieces(t: number): number[] {
  const f = Math.floor((t - 3.926) * 60 + 1e-6);
  if (f < 0 || f >= INK_FRAMES.length || t >= T.SPACE0) return [];
  const out: number[] = [];
  for (const [x0, y0, x1, y1] of INK_FRAMES[f]) {
    out.push(((x0 + x1) / 2 - 960) / 1080, (540 - (y0 + y1) / 2) / 1080, (x1 - x0) / 2160, (y1 - y0) / 2160);
  }
  return out;
}

function shotSmokeDoors(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  setDigits(ro, t, ctx, ROLL_AT, null);
  // T6: the instrument grows a little as the camera pushes through (case width measured
  // 1.111 / 1.131 / 1.142 at src 7.60 / 7.85 / 7.90)
  const s = doorScale(t) * (1 + 0.016 * smooth(3.7, 3.95, t) + 0.01 * smooth(3.95, 4.0, t));
  place(ro, s);
  // R1-08: inside the smoke (src 6.37-6.50) the tube bodies barely show, the digits glow through
  ro.glass = 0.3 * smooth(2.4, 2.52, t) + 0.7 * smooth(2.56, 2.68, t);
  ro.plinth = smooth(2.72, 2.88, t);
  let shot = t < T.SMOKE_END ? 'T4 烟凝底座' : t < T.DOORS_END ? 'T5 门廊读数' : 'T6 穿门';
  // overexposure into the ghost look as the doors part
  // (src 7.90 still shows the real instrument, slightly rolled; the ghost look comes at the cut)
  // (60 fps src 7.918 still shows the solid instrument; the ghost look is the T7 cut at 7.934)
  const over = smooth(4.016, 4.034, t);
  ro.glow = 1 + 1.1 * over;
  ro.blur = 2 * over;
  if (t > 4.026) ro.mode = 1;
  // three one-frame slips of the band holding the instrument right before the cut (row-block
  // shifts measured against src 7.868: 7.884 rows +.03..-.26 moved -.07; 7.901 rows +.27..+.04
  // moved -.13 and +.03..-.26 moved -.23; 7.918 rows +.23..+.01 moved +.04 and 0..-.26 moved +.14)
  let slipA: [number, number, number] = [0, 0, 0];
  let slipB: [number, number, number] = [0, 0, 0];
  if (t >= 3.976 && t < 3.993) {
    slipB = [-0.264, 0.033, 0.072];
  } else if (t >= 3.993 && t < 4.01) {
    slipA = [0.042, 0.267, 0.128];
    slipB = [-0.264, 0.033, 0.233];
  } else if (t >= 4.01 && t < 4.026) {
    slipA = [0.014, 0.233, -0.04];
    slipB = [-0.264, 0.0, -0.14];
  }
  // (src 6.70 the crusted block still covers the case, 6.80 the case is clean)
  const smoke = t < 2.98 ? { k: smooth(2.33, 2.82, t), a: 1 - smooth(2.81, 2.88, t), time: t, ro: ro.ro } : null;
  return frame(t, shot, {
    // (src 6.45-7.90: a clean light sky behind the frames, top centre (239,225,214) sd 6)
    parch: parch({ time: t, bright: 0.85, doors: smooth(2.45, 2.6, t), lattice: smooth(2.45, 2.6, t), stain: 0.7, sky: smooth(2.45, 2.65, t), ink: inkPieces(t), ...corridorDoors(t) }),
    readout: finish(ro),
    smoke,
    post: post({
      vig: 0.28,
      lens: 0,
      // radial colour fringes on the frame edges (src 6.80-7.85: blue / orange, ~3 px each side)
      ca: 0.0035 * smooth(2.45, 2.6, t),
      flash: 0.35 * smooth(4.018, 4.034, t) * (1 - smooth(4.04, 4.09, t)),
      bloom: 0.6 + 0.6 * over,
      slipA,
      slipB,
    }),
  });
}

// R3-02 (R4): the centre slit's half-width / 0.95 (stage units), read off the 60 fps original
// (7.934 / 7.95 / 7.967 / 7.984 / 8.00 / 8.017 / 8.034 / 8.067: slit x .445-.555, .445-.555,
// .43-.57, .40-.60, .35-.66, .30-.72, .07-.93, open). It is already open on the cut frame, holds
// narrow for three frames, then the doors part fast
const SLIT_KEYS: ReadonlyArray<readonly [number, number]> = [
  [4.03, 0.103], [4.05, 0.103], [4.067, 0.131], [4.084, 0.187], [4.1, 0.29], [4.117, 0.39], [4.134, 0.8], [4.15, 0.95], [4.167, 1],
];

// R3-02 (R4): the ink in the slit on the cut frame, 60 fps src 7.934 (dark L < 70 inside the slit,
// split into inscribed discs at 1080p): x, y, r in stage units, k: 21/s for the near blobs (r >= 5
// px), 5/s for the far dots (fitted on 7.934-7.984)
const SLIT_BURST: ReadonlyArray<number> = [
  -0.0556, 0.2519, 0.0284, 21,
  -0.0648, 0.3037, 0.0210, 21,
  -0.0130, 0.2833, 0.0186, 21,
  -0.0704, 0.3611, 0.0160, 21,
  -0.0417, 0.2028, 0.0114, 21,
  0.0491, 0.3657, 0.0110, 21,
  -0.0759, 0.3352, 0.0100, 21,
  -0.0620, 0.2157, 0.0070, 21,
  -0.0593, 0.1981, 0.0056, 21,
  -0.0787, 0.2796, 0.0050, 21,
  -0.0778, 0.2250, 0.0050, 21,
  -0.0315, 0.1676, 0.0049, 21,
  -0.0056, 0.3065, 0.0044, 5,
  -0.0259, 0.1972, 0.0042, 5,
  -0.0620, 0.3417, 0.0040, 5,
  -0.0620, 0.3324, 0.0040, 5,
  0.0003, 0.3487, 0.0030, 5,
  -0.0122, 0.1966, 0.0030, 5,
  0.0072, 0.2454, 0.0027, 5,
  0.0197, 0.3584, 0.0027, 5,
  0.0206, 0.3255, 0.0026, 5,
  0.0723, 0.3096, 0.0025, 5,
  0.0363, 0.3128, 0.0022, 5,
  0.0773, 0.1676, 0.0020, 5,
  0.0014, 0.3212, 0.0019, 5,
  0.0343, 0.2749, 0.0019, 5,
];

function shotSpace(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  setDigits(ro, t, ctx, null, null);
  place(ro, 0.97, 0, 0.01);
  ro.mode = 1;
  ro.glow = 1.35 + 0.4 * (1 - smooth(4.03, 4.12, t));
  ro.blur = 1.2 + 1.0 * (1 - smooth(4.03, 4.12, t));
  ro.glass = 0.6;
  ro.plinth = 0.45;
  ro.envDark = 0.2;
  const tau = t - T.SPACE0;
  let gears = null;
  if (t > 4.38) {
    const n = poseLone(t - 4.38, gearBuf, [0.0, -0.035]);
    gears = { data: gearBuf, count: n };
  }
  return frame(t, 'T7 黑碎块/行星', {
    bg: 'space',
    // R1-13 (60 fps src 8.334-8.45): the picture rolls, then for ~2 frames the nebula is drawn out
    // sideways (a lateral planar sweep) while the bronze gear cluster grows in the middle
    space: {
      t: tau,
      reveal: pchip(SLIT_KEYS, t),
      vp: [0, 0.03],
      roll: -0.11 * smooth(4.40, 4.53, t),
      sweep: smooth(4.522, 4.532, t),
      burst: t < 4.16 ? (SLIT_BURST as number[]) : [],
    },
    parch: parch({ time: t, bright: 0.85, doors: 1, lattice: 1, stain: 0.7, sky: 1, ...corridorDoors(t) }),
    gears,
    readout: finish(ro),
    post: post({
      vig: 0.4,
      bloom: 0.7,
      flash: 0.06 * (1 - smooth(4.03, 4.09, t)),
      // R3-02 (R4): the cut frame (src 7.934) has two clean row slips and no colour split - the
      // band through the moon (rows .62-.79 H) sits .027 W left, the strip under it (.79-.87 H)
      // .075 W left; 7.95-8.00 are clean (R3's colour-split tearing to 4.10 is gone)
      ...(t < 4.042 ? { slipA: [-0.29, -0.12, 0.048] as [number, number, number], slipB: [-0.37, -0.29, 0.133] as [number, number, number] } : {}),
    }),
  });
}

function shotGears(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  setDigits(ro, t, ctx, null, null);
  const u = clamp((t - T.GEAR0) / (T.DARK0 - T.GEAR0));
  const roll = -0.27 * smooth(0, 1, u) - 0.05 * u;
  place(ro, 1.0 + 0.14 * u * u, 0, 0.02, roll * 0.45);
  ro.mode = 1;
  // (src 8.50-8.80: bright glowing digits, the tube shells only faint transparent outlines)
  ro.glow = 2.1;
  // R1-14 (src 8.50-8.90): each lit digit sits in a wide orange glow (radius ~ the digit height)
  ro.halo = 1;
  // (src 8.70: the wires are sharp inside their glow and the tube bodies are not filled)
  ro.blur = 0.35;
  ro.glass = 0.16;
  ro.plinth = 0.25;
  ro.envDark = 0.1 + 0.2 * u;
  const n = poseCloud(u, t - T.GEAR0, gearBuf, [0, 0.03]);
  // the gears turn behind the glowing ghost digits (the digits stay on top, as in the original)
  return frame(t, 'T8 齿轮群', {
    parch: parch({ time: t, plain: 1, plainDark: smooth(0.5, 0.9, u) }),
    gears: { data: gearBuf, count: n },
    gearsBehind: true,
    readout: finish(ro),
    post: post({ vig: 0.55, bloom: 0.9, ...gearGlitch(t) }),
  });
}

/** r2 (60 fps src 8.718 / 8.733 / 8.751): for three frames the band with the readout slips sideways */
function gearGlitch(t: number): Partial<PostState> {
  if (t >= 4.815 && t < 4.832) return { slipA: [-0.02, 0.12, 0.03], tear: 0.18, tearSeed: 3 };
  if (t >= 4.832 && t < 4.849) return { slipA: [-0.04, 0.15, -0.07], slipB: [0.15, 0.2, 0.03], tear: 0.3, tearSeed: 4 };
  if (t >= 4.849 && t < 4.866) return { slipA: [-0.01, 0.12, 0.045], tear: 0.15, tearSeed: 5 };
  return {};
}

/** R2-04: the readout's clockwise roll inside the T10 tunnel (rad; negative = clockwise) */
const TUNNEL_ROLL = -0.085;
/** R4-01 (R5): T10 row from 5.70 - growth of the row (z) and the wall bend (k, exponent 4) */
const R5_Z: ReadonlyArray<readonly [number, number]> = [[5.7, 1.08], [5.9, 1.15], [6.1, 1.17]];
const R5_K: ReadonlyArray<readonly [number, number]> = [[5.7, 7], [5.78, 8], [5.9, 9.5], [6.1, 9.5]];
const R5_WY = 22;
const R5_GLASS = 0.7;
const R5_LINE = 0.4;
const R5_WX: ReadonlyArray<readonly [number, number]> = [[5.7, 0.04], [5.9, 0.065], [6.1, 0.065]];
const R5_OUT = [5.9, 6.033] as const;
// R3-06 (R4): the end tubes on the tube wall, fitted to the 60 fps source 9.417-9.55 (lit-glyph
// centroids at 1080p): the right end rides up instead of sinking with the roll, the left end stays
// down, and in the helix the warp eases so both ends stay in frame, as in the source.
// columns: t, warp scale (helix only), lift round t6, extra lift round t7, drop round t0
const R306: number[][] = [
  [5.517, 1, -0.0027, 0.0314, 0],
  [5.533, 1, 0.0092, 0.0092, 0],
  [5.55, 1, 0.0265, 0.0043, 0.045],
  [5.567, 1, 0.0261, -0.0045, 0.0368],
  [5.583, 0.45, 0.0358, -0.0154, 0.05],
  [5.6, 0.45, 0.02, 0.012, 0],
  [5.617, 0.45, 0.01, 0.0193, 0.0093],
  [5.633, 0.45, -0.005, 0, 0.0068],
  [5.65, 0.45, 0, 0, 0],
  [5.7, 1, 0, 0, 0],
];
function r306(t: number): number[] {
  const K = R306;
  if (t <= K[0][0]) return K[0].slice(1);
  for (let i = 1; i < K.length; i++) {
    if (t <= K[i][0]) {
      const a = K[i - 1], b = K[i];
      const w = (t - a[0]) / (b[0] - a[0]);
      return a.slice(1).map((v, j) => v + (b[j + 1] - v) * w);
    }
  }
  return K[K.length - 1].slice(1);
}

function shotDark(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  setDigits(ro, t, ctx, null, null);
  ro.mode = 2;
  ro.envDark = 1;
  ro.glow = 1.25;
  ro.plinth = 1;
  const dk = (o: Partial<DarkState>): DarkState => ({
    t: t - T.DARK0, phase: 0, rot: 0, sweep: 0, width: 0, archC: [0, 0], archR: [1, 1], white: 0, white2: 0,
    ground: 0, helix: 0, rib: 0, wisp: 0, headL: 2, headU: 2, front: 0, hole: 0, tunnel: 0, floor: 0, ...o,
  });
  if (t < T.TUBE9) {
    const u = clamp((t - T.DARK0) / (T.DARK2 - T.DARK0));
    // frontal, close and low; digits measured at y ~ .09, spanning +-.50 (src 9.00) to +-.85 (9.40):
    // the middle tubes keep their size, the outer ones are thrown out by a pincushion that now
    // bends the instrument only (the light band is drawn unbent, in screen space)
    // (60 fps src 9.367 / 9.384 / 9.401: the hole grows only a little; 9.418 it opens into the vault)
    const hole = smooth(5.44, 5.525, t);
    const s = 1.17 + 0.06 * Math.min(u, 1) - 0.17 * hole;
    place(ro, s, 0, -0.01);
    ro.view = [-0.1, -0.1, 1 / 1.8];
    // R1-16: the board under the tubes stays, a flat plate seen from low in perspective
    ro.plinth = 0.9 * (1 - hole);
    ro.boardOnly = 1;
    // R1-16: thin grey translucent outlines (src 9.03), not bright cream lines
    ro.lineK = 0.72;
    const wc = roScreen([0, 0.06], ro.ro);
    const k9 = 2.7 * smooth(0.15, 0.85, u);
    ro.warp = [wc[0], wc[1], k9 + (16.5 - k9) * hole, 3 + hole];
    // src 9.03: the band is already in, a comet from the lower left wrapping the middle tubes;
    // its two limbs reach the right edge by 9.20 and swell into a flood by 9.29
    const head = 1.9 * smooth(5.1, 5.135, t) + 0.5 * smooth(5.135, 5.2, t);
    // R3-01 (R4, 60 fps src 9.17-9.37 at 1080p): the flood swells later than R3 had it - frame mean
    // 75 / 85 / 114 / 135 / 167 at src 9.20 / 9.233 / 9.267 / 9.30 / 9.333, the upper half still
    // dark at 9.267 (98), the eye's lid opening mostly after 9.30. Band width, arch growth, flood
    // and ground all reach their R3 values by 5.466 (the picture from 5.467 on is unchanged)
    const tw = t < 5.27 ? t : t < 5.37 ? 5.27 + (t - 5.27) * 0.6 : 5.33 + (t - 5.37) * (0.09 / 0.095);
    const S = 1.15 * (1 + 0.15 * smooth(5.13, 5.3, t) + 0.45 * smooth(5.36, 5.462, t)) * (1 + 3 * hole * hole);
    return frame(t, 'T9 暗场光弧', {
      bg: 'dark',
      dark: dk({
        headL: head,
        headU: head,
        archC: [-0.03 + 0.03 * smooth(5.13, 5.3, t), -0.055 - 0.02 * smooth(5.15, 5.3, t)],
        archR: [S * 0.86, S * 1.12],
        width: smooth(5.22, 5.42, tw),
        white: smooth(5.43, 5.466, t),
        white2: smooth(5.40, 5.47, t),
        hole,
        ground: 1 - smooth(5.42, 5.466, t),
        front: 0.55 * (1 - hole) * (1 - 0.6 * smooth(5.25, 5.4, t)),
      }),
      readout: finish(ro),
      post: post({ vig: 0.35 * (1 - smooth(5.36, 5.48, t)), bloom: 0.85, grain: 0.04 }),
    });
  }
  if (t < T.HELIX0) {
    // src 9.40-9.47: inside a ribbed tube; the readout bends round its wall, the end digits huge
    // measured: middle pitch ~.12-.13 (src 9.40-9.45), end tubes at ~+-.75-.8
    // R2-04 (R3): the row rolls clockwise (glyph-centroid fits, src 9.417 / 9.433 / 9.45 / 9.467:
    // +0.3 / +2-3 / +4 / +2-5.5 deg descending to the right) - ~5 deg by 5.567
    place(ro, 1.06, 0, -0.01, TUNNEL_ROLL * smooth(5.505, 5.56, t));
    ro.view = [-0.1, -0.1, 1 / 1.8];
    ro.plinth = 0;
    const wc = roScreen([0, 0.06], ro.ro);
    ro.warp = [wc[0], wc[1], 16.5, 4];
    ro.warpY = 22;
    const e6 = r306(t);
    ro.warpB = [e6[1], e6[2], e6[3]];
    // R2-04: lighter outlines, the near tubes glowing warm white until the wall has passed
    ro.lineK = 0.40;
    ro.tglow = 1 - smooth(5.545, 5.575, t);
    return frame(t, 'T9→T10 筒形内部', {
      bg: 'dark',
      // (60 fps src 9.401 -> 9.418: the vault opens in one frame, the floor stays white to 9.467)
      // (front: the floor is added over the instrument in the second pass)
      dark: dk({ t: t - T.TUBE9, hole: 1, tunnel: 1, floor: 1, front: 1, headL: 2, headU: 2 }),
      readout: finish(ro),
      post: post({ vig: 0.3, bloom: 0.85, grain: 0.04 }),
    });
  }
  const u = clamp((t - T.HELIX0) / (T.FLASH0 - T.HELIX0));
  // R4-01 (R5, lit-glyph clusters of the 60 fps src 9.55-9.97 at 1080p): the row is not relaxed
  // after the tube. The end digits leave the frame at src 9.59-9.61 (left at 9.583 still 0-.04 W,
  // right a sliver at 9.60) and never come back; the middle tubes keep a pitch of ~.075 W at 9.60
  // growing to ~.085 W by 9.75, the tube right of them big and pushed out to ~.75-.83 W. So from
  // 5.65 the row grows a little and the wall bend is held instead of easing out by 5.80
  // (and hands back to the R4 row before the X bands - 6.03-6.07 are R1-18's frames, kept as in R4)
  const g5 = smooth(5.65, 5.7, t) * (1 - smooth(R5_OUT[0], R5_OUT[1], t));
  const z5 = 1 + g5 * (pchip(R5_Z, t) - 1);
  // (the tunnel's roll carries into the helix and relaxes - src 9.48-9.55 still ~+2-9 deg)
  place(ro, (1.1 + 0.15 * u) * z5, 0, -0.02, 0.03 * Math.sin(u * 5) + TUNNEL_ROLL * (1 - smooth(5.6, 5.75, t)));
  const e6 = r306(t);
  {
    // the readout stays bent round the tube wall into the helix (src 9.48-9.60)
    const kw4 = 16.5 * (1 - smooth(5.6, 5.8, t)) * e6[0];
    const kw = kw4 + (pchip(R5_K, t) - kw4) * g5;
    if (kw > 0.01) {
      // (src 9.60-9.85: the left end leaves the frame while the tube right of the middle is only
      // moderately pushed, and from 9.80 the left tubes spread too - the bend is centred a little
      // right of the row's middle)
      const wc = roScreen([pchip(R5_WX, t) * g5, 0.06], ro.ro);
      ro.warp = [wc[0], wc[1], kw, 4];
      const wy4 = 22 * (1 - smooth(5.6, 5.8, t));
      ro.warpY = wy4 + (R5_WY - wy4) * g5;
      ro.warpB = [e6[1], e6[2], e6[3]];
    }
  }
  ro.view = [0.05 * Math.sin(u * 3), -0.15, 1 / 1.2];
  ro.plinth = 0.6;
  ro.lineK = 1.1 + R5_LINE * g5;
  ro.glow = 0.95;
  // R4-01 (R5, src 9.60-9.85): the tube bodies are dark see-through glass - the ladders behind them
  // and at the sides read through, only the outlines draw the big outer tubes
  ro.glass = 1 - R5_GLASS * g5;
  return frame(t, 'T10 螺旋/光带', {
    bg: 'dark',
    dark: dk({
      t: t - T.DARK2,
      phase: 1,
      helix: smooth(5.56, 5.6, t),
      rib: 0.4 * smooth(5.75, 6.06, t),
      // src 9.52-9.97: black behind the ladders, only a little warm haze (R1-18 R4: warmer over the
      // top once the X bands are in, src 9.90-9.97 upper half R-B ~75)
      wisp: 0.18 + 0.3 * smooth(5.88, 5.98, t),
      front: 1,
      // R2-04: the tunnel floor sinks out of the frame (src 9.475-9.50)
      floor: 1 - smooth(5.588, 5.6, t),
    }),
    readout: finish(ro),
    // R1-18: the end is carried by the converging ladders (src 9.97: magenta / green fringes).
    // R4: src 9.93-9.97 is two crisp white X bands on near-black (lower-left 1/6 mean 35-41); R3's
    // bloom + zoom washed the frame. Bloom eases off from 5.9, the zoom smear is a trace, the
    // fringes only on the last frame (src 9.933-9.967 have none, 9.984 strong)
    post: post({ vig: 0.3, bloom: 0.75 - 0.4 * smooth(5.9, 6.0, t), lens: -0.6, zoom: 0.025 * smooth(5.99, 6.1, t), ca: 0.024 * smooth(6.055, 6.084, t), center: [0.5, 0.52] }),
  });
}

function lockFrame(t: number, _ctx: ShowContext, shot: string, ro: ReadoutState, amb: number, extra: Partial<FrameState> = {}): FrameState {
  const relax = 1 - smooth(6.13, 6.19, t);
  const s = lockScale(t) * (1 + 0.22 * relax);
  place(ro, s, 0, -0.03 * relax);
  return frame(t, shot, {
    // R2-03 (src 11.60): pink-brown paper with big soft ink clouds and heavy (bottom) corners
    parch: parch({ time: amb, bright: 0.85, lockInk: 1 }),
    readout: finish(ro),
    cables: cableFrame(amb, ro.ro, 1),
    // R1-19 (60 fps src 10.001 / 10.017): not pure white - the wide-angle lock picture washed out
    // to a pale warm veil, the cables and tube tops still faintly drawn; full picture from 10.033
    post: post({
      vig: 0.3,
      // (src 10.033: a strong wide angle - outer tubes leaning out, the case front bowed)
      lens: -0.55 * relax,
      ca: 0.012 * relax,
      flash: t < 6.13 ? 0.8 : 0,
      flashColor: [1, 0.975, 0.9],
      exposure: t < 6.13 ? 1.35 : 1,
    }),
    ...extra,
  });
}

function shotLock(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  setDigits(ro, t, ctx, null, lockTimes(ctx));
  // R1-20 (re-measured at 1080p): the black shapes of src 12.62-13.07 are cusped ink pieces, not a
  // gear; behind the instrument (the case edge shows over them)
  const sp = strobeFrame(t);
  const extra: Partial<FrameState> = sp.count ? { pieces: { data: sp.data, back: sp.count, front: 0, snap: parch({ time: t, bright: 0.85, lockInk: 1 }) } } : {};
  return lockFrame(t, ctx, t < T.HOLD0 ? 'T12 逐位锁定' : 'T13 静持', ro, t, extra);
}

/** Pending result: the lock framing waits, digits keep rolling, nothing locks. */
export function shotWait(ctx: ShowContext): FrameState {
  const t = T.GATE;
  const amb = t + ctx.waitT;
  const ro = newReadout();
  setDigits(ro, amb, ctx, null, null, 12);
  for (let i = 0; i < 8; i++) ro.lit[i] = 0.88;
  const f = lockFrame(t, ctx, 'GATE 等待结果', ro, amb);
  // R2-02: the cables hold their GATE shape while waiting, so the keyed motion resumes seamlessly
  f.cables = cableFrame(T.GATE, ro.ro, 1);
  return f;
}

export function shotFail(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  const slips: [number, number, number][] = [
    [0, 6.3, 6.42],
    [3, 6.52, 6.62],
    [6, 6.74, 6.84],
  ];
  for (let i = 0; i < 8; i++) {
    if (i === 1) {
      ro.digA[i] = DOT;
      continue;
    }
    const settle = T.F_ROLL + (7 - i) * 0.07;
    if (t >= settle) ro.digA[i] = ctx.origin[i];
    else ro.digA[i] = scramble(i, t, RATE);
    for (const [k, a, b] of slips) if (k === i && t >= a && t < b) ro.digA[i] = ctx.target[i];
  }
  const cool = smooth(7.6, 8.25, t);
  ro.glow = 1 - 0.4 * cool;
  if (t >= T.F_CUT) {
    const f = standbyFrame(ctx.origin, true, t);
    f.post.fade = 1 - smooth(T.F_CUT, T.F_CUT + 0.18, t);
    f.time = t;
    f.shot = '失败：切到暗场';
    return f;
  }
  const f = lockFrame(t, ctx, t < T.F_ROLL ? '失败：滑脱' : '失败：回卷原读数', ro, Math.min(t, 7.6 + (t - 7.6) * 0.3));
  f.parch.cool = cool;
  f.parch.dark = 0.45 * cool;
  return f;
}

function shotExit(t: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  setDigits(ro, t, ctx, null, lockTimes(ctx));
  // the fast push-in: the row grows from ~2/3 of the width (src 13.62) to past the frame edges
  // (13.80), the pincushion stretching the outer tubes
  // r2 re-measure (middle-tube pitch vs src 13.30, 960 px rows): 1.04 at src 13.48, 1.12 at 13.62,
  // 1.22 at 13.70, 1.24 at 13.80 (the outer tubes go on outward through the pincushion)
  const push = Math.pow(clamp((t - 9.5) / 0.4), 1.15);
  const grow = 0.045 * smooth(9.45, 9.62, t) + 0.2 * smooth(9.64, 9.84, t);
  const s = lockScale(t) * (1 + grow);
  place(ro, s, 0, -0.01 * push);
  ro.envDark = smooth(9.28, 9.46, t);
  ro.clear = smooth(9.44, 9.56, t);
  ro.glow = 1 + 0.25 * smooth(9.4, 9.6, t) + 0.1 * push;
  ro.plinth = 1 - 0.18 * smooth(9.45, 9.6, t);
  // R1-25 (R4, src 13.833-13.867 at 1080p): the glass rims melt into the smeared picture before
  // the burst - only blurred fragments of the tube shapes are left, the digits stay
  ro.rimFade = smooth(9.89, 9.955, t);
  const C = roScreen([0, -0.036], ro.ro);
  const pf = pieceFrame(t, C, push);
  const snap = parch({ time: t, bright: 0.85, lockInk: 1 });
  const pieces: PieceState = { data: pf.data, back: pf.back, front: pf.front, snap };
  // (60 fps src: the cables still show over the paper pieces and capsules until 13.384, gone at 13.401)
  const cables: CableState | null = t < 9.5 ? cableFrame(t, ro.ro, 1 - smooth(9.47, 9.495, t)) : null;
  const shot = t < T.CAPS0 ? 'T14 黑墨吞纸' : t < T.BOKEH0 ? 'T15 胶囊放射' : t < T.PUSH0 ? 'T16 橙色散景' : t < T.WHITE0 ? 'T17 色偏推近' : 'T18 白';
  const f = frame(t, shot, {
    bg: 'black',
    parch: snap,
    pieces,
    cables,
    readout: finish(ro),
    post: post({
      vig: 0.35 + 0.2 * ro.envDark,
      bloom: 0.75 - 0.1 * push,
      center: uvOf(C),
      flare: 0.55 * smooth(9.44, 9.58, t) + 1.6 * smooth(9.93, 10.07, t),
      // R1-23: no long horizontal streak under the tubes in T16 (src 13.54: just the hot point);
      // the bright horizontal line belongs to the burst (src 13.83-13.97)
      flareStreak: 0.1 + 0.9 * smooth(9.84, 9.93, t),
      // (src 13.83-13.95: the radial streaks are the smeared picture, not a star filter)
      flareRays: 0.12,
      veil: 0.32 * smooth(9.84, 9.94, t),
      // R1-25 (R3, src 13.83-13.88 at 1080p): red out / cyan in on the capsules first; the
      // magenta / green ends come with the burst smear (13.90-13.95)
      caMode: Math.max(smooth(9.975, 10.03, t), caFrame(t).mode),
      caCyan: Math.max(smooth(9.86, 9.92, t) * (1 - smooth(9.975, 10.03, t)), caFrame(t).cyan),
      // (src 13.80-13.88: the floor below the tubes stays dark teal-grey between the capsules)
      floorDark: smooth(9.82, 9.88, t) * (1 - smooth(9.99, 10.04, t)),
      // R2-05 (R3): no RGB split up to src 13.60 (channels coincide on the tube outlines at
      // 1080p); it switches on at 13.617 and flickers frame by frame (caFrame), then the burst's
      // strong split from 13.76 (R1-25)
      ca: t < CA_ON ? 0 : t < CA_BURST ? caFrame(t).ca : 0.04 + 0.08 * smooth(9.91, 10.0, t),
      pin: smooth(9.78, 9.9, t),
      // r2 (60 fps src 13.767-13.983): the picture washes to cream first, the radial burst comes
      // at 13.85-13.90 with yellow / magenta / green streaks and a bright horizontal line, and
      // it is (almost) white only from 13.983
      // R1-25 (R3): the capsules stay distinct shapes - radially stretched, RGB split, over the
      // dark floor - until src 13.90; the radial smear takes the picture over at 13.90-13.95
      zoom: 0.02 * smooth(9.93, 9.99, t) + 0.48 * smooth(9.995, 10.06, t),
      exposure: 1 + 0.55 * smooth(9.88, 10.03, t) + 1.6 * smooth(10.03, 10.085, t),
      flash: smooth(10.04, 10.095, t),
    }),
  });
  return f;
}

/** R2-05: first frame of the RGB split (src 13.617) and the start of the burst's split (src 13.76) */
const CA_ON = 9.7085;
const CA_BURST = 9.858;
/**
 * R2-05 (R3, src 13.617-13.75 at 1080p, one entry per 60 fps frame): the split flickers - weak
 * on the right tube first, strong magenta / green at 13.633, a clean frame at 13.65, then 5-10 px
 * fringes with red / cyan at 13.70 and strong again at 13.733 (outline peak offsets on the right
 * tube's row 330: 13.633 ~5 px, 13.70 R -8 px, 13.733 ~10+ px)
 */
const CA_TABLE: Array<[number, number, number]> = [
  // ca, magenta/green mode, red/cyan
  [0.006, 0, 0], // 13.617
  [0.014, 1, 0], // 13.633
  [0.0, 0, 0], // 13.650
  [0.012, 0, 0], // 13.667
  [0.01, 0, 0], // 13.683
  [0.014, 0, 1], // 13.700
  [0.008, 0, 0], // 13.717
  [0.018, 0, 0], // 13.733
  [0.014, 0, 0], // 13.750
];
function caFrame(t: number): { ca: number; mode: number; cyan: number } {
  if (t < CA_ON || t >= CA_BURST) return { ca: 0, mode: 0, cyan: 0 };
  const e = CA_TABLE[Math.min(CA_TABLE.length - 1, Math.floor((t - CA_ON) * 60))];
  return { ca: e[0], mode: e[1], cyan: e[2] };
}

export function evaluateShow(t: number, ctx: ShowContext): FrameState {
  t = Math.max(0, t);
  if (t < T.TRIG_END) return shotTrigger(t, ctx);
  if (t < T.MOSS_END) return shotCorridor(t, ctx);
  if (t < T.SPACE0) return shotSmokeDoors(t, ctx);
  if (t < T.GEARCUT) return shotSpace(t, ctx);
  if (t < T.DARK0) return shotGears(t, ctx);
  if (t < T.FLASH0) return shotDark(t, ctx);
  if (t < T.LOCK0) {
    // R1-19: the flash frames already show the (washed out, wide-angle) lock picture
    const ro = newReadout();
    let f: FrameState;
    if (ctx.outcome === 'success') f = shotLock(t, ctx);
    else {
      setDigits(ro, t, ctx, null, null);
      f = lockFrame(t, ctx, 'T11 闪白', ro, t);
    }
    f.shot = 'T11 闪白';
    return f;
  }
  if (ctx.outcome === null && t >= T.GATE) return shotWait(ctx);
  if (ctx.outcome === 'fail' && t >= T.GATE) return shotFail(t, ctx);
  if (ctx.outcome !== 'success') {
    // between the cut and the gate without a (successful) result: roll, never lock
    const ro = newReadout();
    setDigits(ro, t, ctx, null, null);
    return lockFrame(t, ctx, 'T12 逐位锁定（待结果）', ro, t);
  }
  if (t < T.INK0) return shotLock(t, ctx);
  if (t < T.WHITE_END) return shotExit(t, ctx);
  return successEndFrame(ctx.target, t, 1 - smooth(T.WHITE_END, T.S_END, t));
}

export function showEnd(outcome: Outcome): number {
  return outcome === 'success' ? T.S_END : T.F_END;
}

// ---------------------------------------------------------------- reduced motion

export const RM = { STEP: 0.14, FADE: 0.32, FAIL_HOLD: 0.4 };

/** Reduced-motion path: no cuts, no flashes, no camera. Digits crossfade only after confirmation. */
export function evaluateReduced(rmT: number, outcome: Outcome | null, resultAt: number, ctx: ShowContext): FrameState {
  const ro = newReadout();
  for (let i = 0; i < 8; i++) ro.digA[i] = ctx.origin[i];
  place(ro, 1);
  let cool = 0;
  let shot = 'RM 等待确认';
  if (outcome === 'success') {
    shot = 'RM 更新读数';
    for (let i = 0; i < 8; i++) {
      const a = resultAt + i * RM.STEP;
      ro.digB[i] = ctx.target[i];
      ro.mix[i] = smooth(a, a + RM.FADE, rmT);
      ro.locked[i] = rmT >= a + RM.FADE;
    }
  } else if (outcome === 'fail') {
    shot = 'RM 未锁定';
    cool = 0.3 * smooth(resultAt, resultAt + 0.3, rmT);
  }
  return frame(rmT, shot, {
    parch: parch({ time: 0, bright: 0.85, cool }),
    readout: finish(ro),
    cables: cableFrame(0, ro.ro, 1, true),
    post: post({ grain: 0.025 }),
    grainFrame: 0,
  });
}

export function reducedEnd(outcome: Outcome, resultAt: number): number {
  return outcome === 'success' ? resultAt + 7 * RM.STEP + RM.FADE + 0.05 : resultAt + RM.FAIL_HOLD;
}

// ---------------------------------------------------------------- deterministic wall-clock mapping (test hook)

export interface Mapped {
  showT: number;
  waitT: number;
  outcome: Outcome | null;
  resultT: number;
  ended: boolean;
}

/** Map wall time since start to show time, assuming no pause, for a scenario. */
export function mapWallTime(wall: number, scenario: Scenario): Mapped {
  const at = MOCK_DELAY[scenario];
  const out = MOCK_OUTCOME[scenario];
  const known = wall >= at;
  if (at <= T.GATE) {
    const outcome = known ? out : null;
    return { showT: wall, waitT: 0, outcome, resultT: at, ended: outcome !== null && wall >= showEnd(outcome) };
  }
  if (wall < T.GATE) return { showT: wall, waitT: 0, outcome: null, resultT: T.GATE, ended: false };
  if (!known) return { showT: T.GATE, waitT: wall - T.GATE, outcome: null, resultT: T.GATE, ended: false };
  const showT = T.GATE + (wall - at);
  return { showT, waitT: at - T.GATE, outcome: out, resultT: T.GATE, ended: showT >= showEnd(out) };
}

export function scenarioDuration(scenario: Scenario, reduced = false): number {
  const at = MOCK_DELAY[scenario];
  const out = MOCK_OUTCOME[scenario];
  if (reduced) return reducedEnd(out, at);
  return Math.max(at, T.GATE) - T.GATE + showEnd(out);
}

// keep tree-shaken helpers referenced for future shots
void lerp;
void easeOutCubic;
