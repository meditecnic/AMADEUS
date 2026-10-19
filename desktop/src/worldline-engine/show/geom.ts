// Instrument geometry, measured on the original recording at the lock framing (source 11.50 s).
// Stage units: frame height = 1, centre origin, y up (x spans +-0.889). Readout units equal stage
// units at scale 1; the shot code moves/zooms the instrument with uRO (scale, offset, roll).
export const STAGE_ASPECT = 16 / 9;
export const ANCHOR: [number, number] = [0, 0];
export const RO_SCALE = 1;

export const TUBES = 8;
export const PITCH = 0.1224;
// R3 (R1-01, src 11.60 rims of all 8 tubes): outer glass width 108.5 px of a 132.7 px pitch
export const TUBE_HW = 0.05;
export const TUBE_Y0 = -0.072; // top of the case
export const PCB_TOP = -0.066;
export const SOCK_Y0 = -0.066;
// R3 (src 11.60: the barrel ends at -.0333, the socket's light top edge at -.0352)
export const SOCK_Y1 = -0.0355;
// R3: the sockets are narrower than the glass (src 11.60: paper shows from +49 / -51 px)
export const SOCK_HW = 0.0465;
export const GLASS_Y0 = -0.04;
export const TUBE_YD = 0.185;
// R4 (R3-07, src 11.60 at 1080p): the straight wall runs on to TUBE_YD + DOME_DY, then a half
// ellipse cap (half width TUBE_HW, height DOME_B) - fuller shoulders, flatter top than a half circle
export const DOME_DY = 0.0084;
export const DOME_B = 0.04;
export const DIGIT_CY = 0.088;
export const DIGIT_H = 0.133;
// R3 (R1-01): the cathode stacks do not sit on the tube axes. Offsets measured from the support
// wire of the lit digits at src 11.60 / 11.30 / 10.30 (px at 1080p): -10.2, (dot), +2.3, +0.1,
// +0.8, +3.7, +3.5, -4.0
export const DIGIT_DX = [-0.00944, 0, 0.00213, 0.00013, 0.00069, 0.00343, 0.00324, -0.0037];

export const PLINTH_C: [number, number] = [0, -0.1665];
export const PLINTH_H: [number, number] = [0.585, 0.0945];
export const PLINTH_R = 0.005;

export const DOT = 10; // atlas cell of the decimal point
export const STACK = 11; // atlas cell with every cathode overlaid

/** The original zooms the instrument about the centre of the case top. */
export const SCALE_PIVOT: [number, number] = [0, -0.072];

export function tubeCenterX(i: number): number {
  return (i - (TUBES - 1) / 2) * PITCH;
}

/** Offset that keeps SCALE_PIVOT fixed on screen at a given scale (plus an extra shift). */
export function pivotOff(scale: number, dx = 0, dy = 0): [number, number] {
  return [SCALE_PIVOT[0] * (1 - scale) + dx, SCALE_PIVOT[1] * (1 - scale) + dy];
}

/** Screen position of an instrument-local point for a flat (no perspective) placement. */
export function roScreen(p: [number, number], ro: [number, number, number, number]): [number, number] {
  const c = Math.cos(ro[3]);
  const s = Math.sin(ro[3]);
  const x = p[0] * ro[0];
  const y = p[1] * ro[0];
  return [ANCHOR[0] + ro[1] + c * x - s * y, ANCHOR[1] + ro[2] + s * x + c * y];
}

export function projectView(x: number, y: number, view: [number, number, number]): [number, number] {
  if (view[2] <= 0) return [x, y];
  const Dz = 1 / view[2];
  const cy = Math.cos(view[0]);
  const sy = Math.sin(view[0]);
  const cp = Math.cos(view[1]);
  const sp = Math.sin(view[1]);
  const X = cy * x + sy * sp * y;
  const Y = cp * y;
  const Z = Dz - sy * x + cy * sp * y;
  const k = Dz / Math.max(Z, 1e-3);
  return [X * k, Y * k];
}

export const GEOM_DEFINES = `
#define TUBES ${TUBES}
#define PITCH ${PITCH.toFixed(4)}
#define TUBE_HW ${TUBE_HW.toFixed(4)}
#define TUBE_Y0 ${TUBE_Y0.toFixed(4)}
#define PCB_TOP ${PCB_TOP.toFixed(4)}
#define SOCK_Y0 ${SOCK_Y0.toFixed(4)}
#define SOCK_Y1 ${SOCK_Y1.toFixed(4)}
#define SOCK_HW ${SOCK_HW.toFixed(4)}
#define GLASS_Y0 ${GLASS_Y0.toFixed(4)}
#define TUBE_YD ${TUBE_YD.toFixed(4)}
#define DIGIT_CY ${DIGIT_CY.toFixed(4)}
#define DIGIT_H ${DIGIT_H.toFixed(4)}
const float DIGIT_DX[8] = float[8](${DIGIT_DX.map((v) => v.toFixed(5)).join(', ')});
#define PLINTH_C vec2(${PLINTH_C[0].toFixed(4)}, ${PLINTH_C[1].toFixed(4)})
#define PLINTH_H vec2(${PLINTH_H[0].toFixed(4)}, ${PLINTH_H[1].toFixed(4)})
#define PLINTH_R ${PLINTH_R.toFixed(4)}
#define ANCHOR vec2(${ANCHOR[0].toFixed(4)}, ${ANCHOR[1].toFixed(4)})
// R4 (R3-07, src 11.60 at 1080p, outer glass edge measured the same way on both): fuller
// shoulders and a flatter top than a half circle - half width 49.6 / 47.1 / 44.5 px at 26 / 30 /
// 34 px above TUBE_YD and the top 49.6 / 43.6 / 35.1 px high at 20 / 32 / 44 px from the axis (a
// half circle: 46.9 / 44.5 / 41.6 and 49.8 / 42.9 / 30.1). Fit: the wall runs on to DOME_Y, then a
// superellipse cap of exponent 2.1 (half width TUBE_HW, height DOME_B)
#define DOME_DY ${DOME_DY.toFixed(4)}
#define DOME_B ${DOME_B.toFixed(4)}
#define DOME_N 2.1
#define DOME_Y (TUBE_YD + DOME_DY)
vec2 domeQ(vec2 p){ return p / vec2(TUBE_HW, DOME_B); }
float domeF(vec2 q){ vec2 a = max(abs(q), 1e-6); return pow(pow(a.x, DOME_N) + pow(a.y, DOME_N), 1. / DOME_N); }
// gradient of F in local units
vec2 domeG(vec2 q, float F){
  vec2 a = max(abs(q), 1e-6);
  return sign(q) * pow(a, vec2(DOME_N - 1.)) * pow(max(F, 1e-6), 1. - DOME_N) / vec2(TUBE_HW, DOME_B);
}
// the cap normal direction at q (unit) and the superelliptic radius F (1 on the outline)
vec2 domeN(vec2 q, out float F){
  F = domeF(q);
  vec2 g = domeG(q, F);
  return g / max(length(g), 1e-6);
}
float domeSD(vec2 p){
  vec2 q = domeQ(p);
  float F = domeF(q);
  return (F - 1.) / max(length(domeG(q, F)), 1.);
}
// glass envelope above the foot: the straight wall, then the cap
float glassSD(vec2 l){ return l.y > DOME_Y ? domeSD(l - vec2(0., DOME_Y)) : abs(l.x) - TUBE_HW; }
`;
