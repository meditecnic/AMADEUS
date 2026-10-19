// Original nixie cathode glyphs, drawn procedurally with Canvas2D paths.
// Design space per glyph: 72 x 150 units (y down). Each atlas cell adds a margin
// so the pre-blurred halo fits inside the cell.
//   R = the yellow core   G = wide neon halo   B = the thin metal wire   A = the orange sheath
// Cells 0..9 digits, 10 decimal point, 11 = all cathode wires overlaid (the unlit stack, in B).

export const GLYPH_W = 72;
export const GLYPH_H = 150;
export const GLYPH_MARGIN = 22;
export const GLYPH_PX = 2.0; // pixels per design unit
export const CELL_W = Math.round((GLYPH_W + GLYPH_MARGIN * 2) * GLYPH_PX);
export const CELL_H = Math.round((GLYPH_H + GLYPH_MARGIN * 2) * GLYPH_PX);
export const CELLS = 12;

type Pen = CanvasRenderingContext2D;
type Op = (string | number)[];

// R3 (R1-01): every digit re-fitted to the lit strokes of the 60 fps source frames (src 11.60 /
// 11.30 / 10.30 at the lock framing, 6.80 for the 3 and the 6) with a chamfer fit (mean distance
// 0.5-1.1 px at 1080p). Each cathode is a body (the glowing digit) plus a thinner support wire:
// a centre wire and a crossbar through the loop it belongs to (upper loop for 0 1 2 7 8 9, the bowl
// for 3 5 6 8). The 4 has its stem right of the centre wire.
const GLYPHS: { body: Op[]; wire: Op[] }[] = [
  // 0
  { body: [['E', 35.89, 77.19, 34.01, 68.06]],
    wire: [['M', 35.97, 5.05], ['L', 35.97, 140.29], ['M', 6.67, 39.57], ['L', 63.11, 39.57]] },
  // 1
  { body: [['M', 18.7, 26], ['L', 36, 7.1], ['L', 36, 142.91]],
    wire: [['M', 5.85, 39.69], ['L', 64.95, 39.69]] },
  // 2
  { body: [['A', 36.74, 38.31, 28.22, 3.28, 6.88], ['Q', 41.35, 79.58, 2.15, 145.18], ['L', 66.53, 145.18]],
    wire: [['M', 36, 9.24], ['L', 36, 142.4], ['M', 10.35, 40.61], ['L', 63.18, 40.61]] },
  // 3
  { body: [['M', 9.15, 12.69], ['L', 66.04, 12.69], ['L', 41.59, 66.4], ['A', 35.71, 109.8, 34.04, -1.56, 2.62]],
    wire: [['M', 36, 9.93], ['L', 36, 144.99], ['M', 1.92, 111.1], ['L', 67.69, 111.1]] },
  // 4
  { body: [['M', 53.02, 146.57], ['L', 53.02, 12.38], ['L', 9.22, 109.52], ['L', 72.92, 109.52]],
    wire: [['M', 37.53, 10.26], ['L', 37.53, 142.45]] },
  // 5
  { body: [['M', 63.68, 14.48], ['L', 20.34, 14.48], ['L', 20.34, 74], ['Q', 20.39, 68.57, 31.26, 78], ['A', 35.72, 112.26, 34.03, -2.13, 2.82]],
    wire: [['M', 36, 15], ['L', 36, 145.01], ['M', 2.99, 114.18], ['L', 71.62, 114.18]] },
  // 6
  { body: [['M', 40.73, 14.46], ['Q', 12.96, 53.27, 7.21, 117], ['E', 36.34, 113.32, 31.61, 31.61]],
    wire: [['M', 36, 15.26], ['L', 36, 149.16], ['M', 4.88, 112.55], ['L', 69.14, 112.55]] },
  // 7
  { body: [['M', 5.15, 13.44], ['L', 64.15, 13.44], ['L', 22.44, 144.27]],
    wire: [['M', 36, 9.78], ['L', 36, 144.5], ['M', 6.69, 43.51], ['L', 71.47, 43.51]] },
  // 8
  { body: [['E', 37.46, 43.79, 26.35, 28.27], ['E', 37.46, 107.88, 33.92, 38.82]],
    wire: [['M', 37.46, 9.23], ['L', 37.46, 146.58], ['M', 10.72, 42.67], ['L', 62.43, 42.67], ['M', 4.45, 108.45], ['L', 68.71, 108.45]] },
  // 9
  { body: [['E', 34.65, 46.58, 32.37, 31.91], ['M', 65.61, 43.18], ['Q', 56.16, 108.09, 29.28, 141.08]],
    wire: [['M', 34.65, 11.83], ['L', 34.65, 146.59], ['M', 4.94, 43.63], ['L', 66.07, 43.63]] },
];

function trace(c: Pen, ops: Op[]): void {
  c.beginPath();
  for (const op of ops) {
    const v = op.slice(1) as number[];
    switch (op[0]) {
      case 'M': c.moveTo(v[0], v[1]); break;
      case 'L': c.lineTo(v[0], v[1]); break;
      case 'Q': c.quadraticCurveTo(v[0], v[1], v[2], v[3]); break;
      case 'A': c.arc(v[0], v[1], v[2], v[3], v[4], false); break;
      case 'E': c.moveTo(v[0] + v[2], v[1]); c.ellipse(v[0], v[1], v[2], v[3], 0, 0, Math.PI * 2); break;
    }
  }
  c.stroke();
}

function paintCell(ctx: Pen, cell: number, blurPx: number, fn: (c: Pen) => void): void {
  ctx.save();
  ctx.translate(cell * CELL_W + GLYPH_MARGIN * GLYPH_PX, GLYPH_MARGIN * GLYPH_PX);
  ctx.scale(GLYPH_PX, GLYPH_PX);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = '#fff';
  ctx.fillStyle = '#fff';
  ctx.filter = blurPx > 0 ? `blur(${blurPx}px)` : 'none';
  fn(ctx);
  ctx.restore();
}

function digitPen(d: number, wBody: number, wWire: number) {
  return (c: Pen): void => {
    c.lineWidth = wBody;
    trace(c, GLYPHS[d].body);
    c.lineWidth = wWire;
    trace(c, GLYPHS[d].wire);
  };
}

function dotPen(w: number) {
  return (c: Pen): void => {
    c.lineWidth = w;
    c.beginPath();
    c.arc(36, 136, 2.0, 0, Math.PI * 2);
    c.fill();
    c.stroke();
  };
}

function layer(wBody: number, wWire: number, blurPx: number, stackBody = 0, stackWire = 0): Uint8ClampedArray {
  const cv = document.createElement('canvas');
  cv.width = CELL_W * CELLS;
  cv.height = CELL_H;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  if (!ctx) throw new Error('2D canvas unavailable');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, cv.width, cv.height);
  if (wBody > 0) {
    for (let d = 0; d < 10; d++) paintCell(ctx, d, blurPx, digitPen(d, wBody, wWire));
    paintCell(ctx, 10, blurPx, dotPen(wBody));
  }
  if (stackBody > 0) {
    for (let d = 0; d < 10; d++) paintCell(ctx, 11, blurPx, digitPen(d, stackBody, stackWire));
  }
  return ctx.getImageData(0, 0, cv.width, cv.height).data;
}

/** Build the RGBA atlas as raw bytes (no canvas image is kept around).
 *  R = the yellow core of the glowing cathode (src 11.60: ~4.5-6 px at 1080p on a ~140 px digit,
 *  the support wires ~3 px)   G = wide neon halo   B = the thin metal wire (cell 11: the unlit stack)
 *  A = the soft orange sheath round the core (src: the orange shoulder reaches ~6 px out). */
export function buildGlyphAtlas(): { data: Uint8Array; width: number; height: number } {
  const k = GLYPH_PX / 1.5; // blur radii are in atlas pixels
  const core = layer(4.4, 2.6, 0.45 * k);
  const halo = layer(16.0, 9.0, 13.0 * k);
  // (the unlit stack: src 11.60 tube 2 dark runs ~5 px median - thicker than the R2 1.7-unit lines)
  const wire = layer(3.0, 2.0, 0.4 * k, 2.4, 1.6);
  const sheath = layer(8.5, 5.0, 2.0 * k);
  const w = CELL_W * CELLS;
  const h = CELL_H;
  const out = new Uint8Array(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    out[i * 4] = core[i * 4];
    out[i * 4 + 1] = halo[i * 4];
    out[i * 4 + 2] = wire[i * 4];
    out[i * 4 + 3] = sheath[i * 4];
  }
  return { data: out, width: w, height: h };
}
