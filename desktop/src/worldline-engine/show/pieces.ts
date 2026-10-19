// The exit (T14-T16): the paper is cut into overlapping round pieces whose union is exactly the
// paper (so nothing changes until they move). Pieces near the edges shrink first, so black eats
// in with scalloped, concave-arc edges; one-frame cusped ink pieces flash (T13 end / T14, fitted
// on the 60 fps source); then every piece is pulled out radially into a capsule, a burst of thin
// white streaks follows, and orange bokeh drift out.
import { clamp, smooth, rng } from './math';
import type { V2 } from './math';

export const PIECE_STRIDE = 12;

interface Blob {
  h: V2;
  R: number;
  ts: number;
  /** R1-22: how far this piece is drawn out (the capsules vary in length and width) */
  q: number;
  w: number;
  dot: boolean;
  /** small per-piece delay of the general break-up */
  dl: number;
}
interface Shard {
  from: V2;
  to: V2;
  t0: number;
  size: number;
  a0: number;
  spin: number;
  seed: number;
}
interface Bok {
  ang: number;
  /** 3D radial offset and size; the screen radius and size go as 1/z */
  R3: number;
  S3: number;
  z0: number;
  t0: number;
  elong: number;
  bright: number;
  front: boolean;
  gain: number;
  /** R1-25: cream, crisp burst capsule (tint added, sharper edge) */
  cream?: number;
}

const r = rng(9127);
// smooth value noise (coherent over ~1/f stage units) for the ink lobes
function vn(x: number, y: number): number {
  const h = (i: number, j: number): number => {
    const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453;
    return s - Math.floor(s);
  };
  const i = Math.floor(x), j = Math.floor(y);
  const fx = x - i, fy = y - j;
  const ux = fx * fx * (3 - 2 * fx), uy = fy * fy * (3 - 2 * fy);
  const a = h(i, j) + (h(i + 1, j) - h(i, j)) * ux;
  const b = h(i, j + 1) + (h(i + 1, j + 1) - h(i, j + 1)) * ux;
  return a + (b - a) * uy;
}
// (R2: the flying torn pieces are no longer drawn - at 60 fps src 13.083-13.268 has one-frame
// pieces, see STROBE. The list is still generated so the random sequence of everything after it
// (paper pieces, capsules, bokeh) stays the same.)
const SHARDS: Shard[] = [];
for (let i = 0; i < 22; i++) {
  let x = 0;
  let y = 0;
  for (let k = 0; k < 24; k++) {
    const a = r() * Math.PI * 2;
    const rr = 0.6 + 0.48 * r();
    x = Math.cos(a) * rr * 0.95;
    y = Math.sin(a) * rr * 0.56;
    if (!(Math.abs(x) < 0.56 && y > -0.3 && y < 0.26)) break;
  }
  const to: V2 = [x, y];
  const from: V2 = [x * 1.3, y * 1.3];
  SHARDS.push({ from, to, t0: 9.1 + 0.17 * r(), size: 0.03 + 0.12 * Math.pow(r(), 1.3), a0: r() * 6.28, spin: (r() - 0.5) * 4, seed: r() * 100 });
}
// R1-20 / R1-21, re-measured on the 1080p 60 fps source: T13 has no gear and T14 has no flying
// shards. From src 12.617 to 13.284 cusped ink pieces (smooth round bites, sharp points, no
// teeth) flash one frame each, every frame a new set: in T13 at the frame edges and round the case
// ends (none in 12.668-12.717, 12.767, 12.883-12.901), in T14 more and bigger (13.234: 10% of the
// paper, 13.284: 15%), until the paper breaks up. Every piece of every frame is fitted with this
// shader's cusped shape (seed, angle, centre, radius) by IoU on the source frame
// (/tmp/r2/pkg/inkfit.py: T13 43 pieces, IoU 0.62-0.93; T14 109 pieces in 12 frames, IoU
// 0.43-0.95); proto t = src t - 3.90, half a frame early so the piece is up for exactly that source
// frame. They sit behind the instrument (the case edge shows over them, src 13.033 / 13.050).
const STROBE = [
  // src 12.618
  { t0: 8.7091, t1: 8.7250, x: 0.668, y: 0.218, R: 0.0225, ang: 1.571, seed: 17.56 },
  // src 12.633
  { t0: 8.7250, t1: 8.7420, x: 0.769, y: 0.255, R: 0.0544, ang: 0.725, seed: 88.02 },
  // src 12.651
  { t0: 8.7420, t1: 8.7595, x: 0.831, y: 0.285, R: 0.0768, ang: 0.634, seed: 88.02 },
  // src 12.734
  { t0: 8.8251, t1: 8.8417, x: 0.729, y: -0.416, R: 0.0447, ang: 3.333, seed: 39.24 },
  { t0: 8.8251, t1: 8.8417, x: 0.571, y: 0.136, R: 0.0215, ang: 6.101, seed: 115.12 },
  // src 12.750
  { t0: 8.8417, t1: 8.8593, x: 0.100, y: -0.456, R: 0.0361, ang: 2.880, seed: 88.02 },
  // src 12.783
  { t0: 8.8748, t1: 8.8919, x: 0.882, y: 0.268, R: 0.0744, ang: 2.406, seed: 1.3 },
  // src 12.800
  { t0: 8.8919, t1: 8.9090, x: -0.632, y: -0.069, R: 0.0909, ang: 6.233, seed: 47.37 },
  // src 12.817
  { t0: 8.9090, t1: 8.9255, x: -0.831, y: -0.095, R: 0.1438, ang: 4.592, seed: 90.73 },
  { t0: 8.9090, t1: 8.9255, x: 0.727, y: 0.471, R: 0.0211, ang: 0.604, seed: 6.72 },
  // src 12.834
  { t0: 8.9255, t1: 8.9426, x: -0.853, y: -0.044, R: 0.0932, ang: 0.705, seed: 12.14 },
  { t0: 8.9255, t1: 8.9426, x: 0.411, y: 0.473, R: 0.0476, ang: 0.000, seed: 12.14 },
  // src 12.851
  { t0: 8.9426, t1: 8.9590, x: -0.773, y: -0.251, R: 0.1146, ang: 1.087, seed: 1.3 },
  // src 12.868
  { t0: 8.9590, t1: 8.9748, x: -0.856, y: -0.265, R: 0.0933, ang: 2.214, seed: 55.5 },
  // src 12.918
  { t0: 9.0090, t1: 9.0247, x: -0.681, y: 0.354, R: 0.1297, ang: 1.601, seed: 115.12 },
  // src 12.933
  { t0: 9.0247, t1: 9.0426, x: -0.804, y: 0.406, R: 0.0936, ang: 1.661, seed: 41.95 },
  { t0: 9.0247, t1: 9.0426, x: 0.630, y: -0.233, R: 0.0339, ang: 1.833, seed: 66.34 },
  // src 12.951
  { t0: 9.0426, t1: 9.0591, x: 0.820, y: -0.298, R: 0.0492, ang: 2.880, seed: 98.86 },
  // src 12.968
  { t0: 9.0591, t1: 9.0755, x: 0.449, y: 0.313, R: 0.0757, ang: 3.503, seed: 71.76 },
  { t0: 9.0591, t1: 9.0755, x: 0.467, y: -0.280, R: 0.0302, ang: 1.833, seed: 71.76 },
  // src 12.984
  { t0: 9.0755, t1: 9.0921, x: 0.695, y: -0.366, R: 0.1622, ang: 3.867, seed: 112.41 },
  { t0: 9.0755, t1: 9.0921, x: -0.477, y: -0.287, R: 0.0236, ang: 4.451, seed: 115.12 },
  { t0: 9.0755, t1: 9.0921, x: 0.120, y: -0.485, R: 0.0239, ang: 0.222, seed: 47.37 },
  // src 13.001
  { t0: 9.0921, t1: 9.1093, x: -0.860, y: -0.321, R: 0.0647, ang: 4.411, seed: 88.02 },
  { t0: 9.0921, t1: 9.1093, x: -0.612, y: -0.394, R: 0.0273, ang: 2.578, seed: 66.34 },
  { t0: 9.0921, t1: 9.1093, x: 0.206, y: -0.490, R: 0.0180, ang: 3.363, seed: 17.56 },
  // src 13.018
  { t0: 9.1093, t1: 9.1250, x: 0.677, y: -0.426, R: 0.0910, ang: 3.333, seed: 117.83 },
  { t0: 9.1093, t1: 9.1250, x: -0.777, y: -0.020, R: 0.0285, ang: 4.109, seed: 20.27 },
  { t0: 9.1093, t1: 9.1250, x: -0.712, y: -0.352, R: 0.0429, ang: 5.104, seed: 12.14 },
  { t0: 9.1093, t1: 9.1250, x: 0.507, y: 0.001, R: 0.0340, ang: 2.024, seed: 60.92 },
  { t0: 9.1093, t1: 9.1250, x: 0.619, y: -0.125, R: 0.0371, ang: 0.424, seed: 85.31 },
  { t0: 9.1093, t1: 9.1250, x: -0.553, y: 0.399, R: 0.0270, ang: 2.458, seed: 6.72 },
  // src 13.033
  { t0: 9.1250, t1: 9.1418, x: 0.749, y: -0.167, R: 0.2009, ang: 4.984, seed: 22.98 },
  { t0: 9.1250, t1: 9.1418, x: 0.120, y: -0.381, R: 0.0350, ang: 0.785, seed: 47.37 },
  { t0: 9.1250, t1: 9.1418, x: -0.641, y: 0.489, R: 0.0414, ang: 6.061, seed: 6.72 },
  { t0: 9.1250, t1: 9.1418, x: -0.435, y: -0.408, R: 0.0255, ang: 0.927, seed: 6.72 },
  // src 13.050
  { t0: 9.1418, t1: 9.1594, x: 0.803, y: -0.219, R: 0.1702, ang: 5.598, seed: 79.89 },
  { t0: 9.1418, t1: 9.1594, x: 0.164, y: -0.474, R: 0.0611, ang: 2.678, seed: 12.14 },
  { t0: 9.1418, t1: 9.1594, x: -0.611, y: -0.478, R: 0.0258, ang: 1.651, seed: 12.14 },
  { t0: 9.1418, t1: 9.1594, x: 0.618, y: 0.100, R: 0.0235, ang: 5.236, seed: 109.7 },
  // src 13.068
  { t0: 9.1594, t1: 9.1746, x: -0.718, y: -0.095, R: 0.0651, ang: 2.426, seed: 112.41 },
  { t0: 9.1594, t1: 9.1746, x: -0.421, y: 0.274, R: 0.0393, ang: 3.927, seed: 17.56 },
  { t0: 9.1594, t1: 9.1746, x: 0.187, y: -0.490, R: 0.0180, ang: 4.712, seed: 101.57 },
  // T14 (src 13.083-13.284): the same one-frame pieces, more and bigger, until the paper breaks up
  // src 13.083
  { t0: 9.1746, t1: 9.1922, x: -0.642, y: 0.415, R: 0.0953, ang: 5.488, seed: 31.11 },
  { t0: 9.1746, t1: 9.1922, x: 0.737, y: 0.024, R: 0.0325, ang: 0.544, seed: 1.3 },
  { t0: 9.1746, t1: 9.1922, x: -0.124, y: 0.421, R: 0.0464, ang: 2.094, seed: 9.43 },
  { t0: 9.1746, t1: 9.1922, x: -0.573, y: -0.051, R: 0.0384, ang: 4.531, seed: 28.4 },
  { t0: 9.1746, t1: 9.1922, x: 0.618, y: 0.387, R: 0.0185, ang: 0.524, seed: 17.56 },
  { t0: 9.1746, t1: 9.1922, x: -0.142, y: -0.360, R: 0.0131, ang: 5.418, seed: 31.11 },
  // src 13.101
  { t0: 9.1922, t1: 9.2095, x: -0.848, y: -0.121, R: 0.1080, ang: 2.013, seed: 6.72 },
  { t0: 9.1922, t1: 9.2095, x: 0.328, y: 0.472, R: 0.0580, ang: 5.670, seed: 41.95 },
  { t0: 9.1922, t1: 9.2095, x: -0.193, y: -0.473, R: 0.0454, ang: 1.481, seed: 55.5 },
  { t0: 9.1922, t1: 9.2095, x: 0.159, y: -0.373, R: 0.0398, ang: 1.944, seed: 82.6 },
  { t0: 9.1922, t1: 9.2095, x: -0.224, y: 0.436, R: 0.0249, ang: 3.283, seed: 66.34 },
  // src 13.118
  { t0: 9.2095, t1: 9.2255, x: -0.834, y: -0.129, R: 0.1619, ang: 3.837, seed: 112.41 },
  { t0: 9.2095, t1: 9.2255, x: -0.854, y: 0.376, R: 0.0320, ang: 0.765, seed: 109.7 },
  // src 13.134
  { t0: 9.2255, t1: 9.2419, x: -0.883, y: 0.191, R: 0.1231, ang: 0.595, seed: 66.34 },
  { t0: 9.2255, t1: 9.2419, x: 0.413, y: -0.426, R: 0.0901, ang: 1.581, seed: 39.24 },
  { t0: 9.2255, t1: 9.2419, x: -0.625, y: -0.160, R: 0.0496, ang: 3.897, seed: 98.86 },
  { t0: 9.2255, t1: 9.2419, x: 0.517, y: -0.304, R: 0.0486, ang: 6.061, seed: 93.44 },
  { t0: 9.2255, t1: 9.2419, x: -0.436, y: -0.480, R: 0.0488, ang: 3.262, seed: 60.92 },
  { t0: 9.2255, t1: 9.2419, x: 0.609, y: -0.129, R: 0.0153, ang: 5.538, seed: 101.57 },
  // src 13.168
  { t0: 9.2591, t1: 9.2751, x: -0.684, y: 0.147, R: 0.0924, ang: 2.750, seed: 63.63 },
  { t0: 9.2591, t1: 9.2751, x: -0.853, y: -0.144, R: 0.0743, ang: 5.196, seed: 96.15 },
  { t0: 9.2591, t1: 9.2751, x: 0.292, y: -0.479, R: 0.0523, ang: 1.389, seed: 9.43 },
  { t0: 9.2591, t1: 9.2751, x: -0.856, y: -0.371, R: 0.0205, ang: 2.578, seed: 6.72 },
  { t0: 9.2591, t1: 9.2751, x: -0.522, y: -0.438, R: 0.0176, ang: 0.665, seed: 6.72 },
  // src 13.184
  { t0: 9.2751, t1: 9.2918, x: 0.580, y: 0.429, R: 0.1097, ang: 6.143, seed: 74.47 },
  { t0: 9.2751, t1: 9.2918, x: -0.452, y: -0.359, R: 0.0844, ang: 3.070, seed: 66.34 },
  { t0: 9.2751, t1: 9.2918, x: 0.716, y: -0.366, R: 0.0636, ang: 0.875, seed: 14.85 },
  { t0: 9.2751, t1: 9.2918, x: 0.016, y: -0.399, R: 0.0669, ang: 2.234, seed: 6.72 },
  { t0: 9.2751, t1: 9.2918, x: 0.206, y: -0.476, R: 0.0494, ang: 0.604, seed: 104.28 },
  { t0: 9.2751, t1: 9.2918, x: -0.690, y: 0.287, R: 0.0192, ang: 4.974, seed: 66.34 },
  { t0: 9.2751, t1: 9.2918, x: 0.886, y: 0.305, R: 0.0171, ang: 3.807, seed: 66.34 },
  // src 13.200
  { t0: 9.2918, t1: 9.3093, x: 0.554, y: 0.027, R: 0.1036, ang: 1.217, seed: 117.83 },
  { t0: 9.2918, t1: 9.3093, x: 0.869, y: -0.178, R: 0.1210, ang: 2.274, seed: 12.14 },
  { t0: 9.2918, t1: 9.3093, x: 0.528, y: -0.308, R: 0.0758, ang: 4.712, seed: 33.82 },
  { t0: 9.2918, t1: 9.3093, x: 0.684, y: 0.481, R: 0.0532, ang: 0.080, seed: 20.27 },
  { t0: 9.2918, t1: 9.3093, x: 0.253, y: 0.396, R: 0.0564, ang: 1.037, seed: 125.96 },
  { t0: 9.2918, t1: 9.3093, x: -0.500, y: 0.329, R: 0.0467, ang: 0.644, seed: 36.53 },
  { t0: 9.2918, t1: 9.3093, x: -0.525, y: 0.427, R: 0.0350, ang: 2.880, seed: 9.43 },
  { t0: 9.2918, t1: 9.3093, x: 0.026, y: -0.277, R: 0.0164, ang: 5.236, seed: 20.27 },
  { t0: 9.2918, t1: 9.3093, x: 0.481, y: -0.019, R: 0.0143, ang: 5.156, seed: 101.57 },
  // src 13.218
  { t0: 9.3093, t1: 9.3260, x: 0.651, y: 0.388, R: 0.1267, ang: 3.745, seed: 98.86 },
  { t0: 9.3093, t1: 9.3260, x: -0.158, y: 0.384, R: 0.0838, ang: 0.384, seed: 66.34 },
  { t0: 9.3093, t1: 9.3260, x: 0.029, y: -0.436, R: 0.0698, ang: 3.545, seed: 12.14 },
  { t0: 9.3093, t1: 9.3260, x: -0.465, y: 0.314, R: 0.0600, ang: 5.316, seed: 6.72 },
  { t0: 9.3093, t1: 9.3260, x: -0.311, y: 0.475, R: 0.0392, ang: 5.810, seed: 22.98 },
  { t0: 9.3093, t1: 9.3260, x: -0.614, y: -0.203, R: 0.0245, ang: 5.246, seed: 14.85 },
  { t0: 9.3093, t1: 9.3260, x: -0.315, y: 0.213, R: 0.0208, ang: 2.254, seed: 31.11 },
  { t0: 9.3093, t1: 9.3260, x: -0.125, y: -0.343, R: 0.0237, ang: 0.332, seed: 98.86 },
  // src 13.234
  { t0: 9.3260, t1: 9.3424, x: 0.744, y: -0.162, R: 0.2656, ang: 4.129, seed: 22.98 },
  { t0: 9.3260, t1: 9.3424, x: -0.763, y: 0.009, R: 0.2072, ang: 5.881, seed: 66.34 },
  { t0: 9.3260, t1: 9.3424, x: 0.585, y: -0.465, R: 0.0910, ang: 1.863, seed: 41.95 },
  { t0: 9.3260, t1: 9.3424, x: -0.659, y: -0.475, R: 0.0699, ang: 1.489, seed: 115.12 },
  { t0: 9.3260, t1: 9.3424, x: 0.040, y: 0.423, R: 0.0325, ang: 2.276, seed: 4.01 },
  { t0: 9.3260, t1: 9.3424, x: -0.223, y: -0.292, R: 0.0223, ang: 3.705, seed: 44.66 },
  { t0: 9.3260, t1: 9.3424, x: 0.187, y: -0.371, R: 0.0242, ang: 0.865, seed: 47.37 },
  { t0: 9.3260, t1: 9.3424, x: -0.277, y: -0.279, R: 0.0130, ang: 3.665, seed: 31.11 },
  // src 13.251
  { t0: 9.3424, t1: 9.3595, x: -0.614, y: -0.205, R: 0.2206, ang: 0.664, seed: 31.11 },
  { t0: 9.3424, t1: 9.3595, x: 0.708, y: 0.093, R: 0.1174, ang: 0.202, seed: 31.11 },
  { t0: 9.3424, t1: 9.3595, x: 0.020, y: -0.337, R: 0.0674, ang: 5.891, seed: 66.34 },
  { t0: 9.3424, t1: 9.3595, x: 0.156, y: -0.309, R: 0.0264, ang: 1.591, seed: 20.27 },
  { t0: 9.3424, t1: 9.3595, x: 0.189, y: 0.485, R: 0.0433, ang: 1.671, seed: 88.02 },
  { t0: 9.3424, t1: 9.3595, x: 0.387, y: 0.490, R: 0.0316, ang: 2.134, seed: 17.56 },
  { t0: 9.3424, t1: 9.3595, x: -0.204, y: 0.290, R: 0.0258, ang: 2.760, seed: 82.6 },
  { t0: 9.3424, t1: 9.3595, x: 0.777, y: 0.456, R: 0.0190, ang: 4.229, seed: 55.5 },
  { t0: 9.3424, t1: 9.3595, x: -0.339, y: 0.079, R: 0.0176, ang: 3.585, seed: 14.85 },
  { t0: 9.3424, t1: 9.3595, x: -0.144, y: 0.311, R: 0.0172, ang: 2.014, seed: 93.44 },
  { t0: 9.3424, t1: 9.3595, x: -0.375, y: -0.051, R: 0.0259, ang: 5.518, seed: 88.02 },
  { t0: 9.3424, t1: 9.3595, x: 0.386, y: -0.035, R: 0.0191, ang: 3.937, seed: 66.34 },
  { t0: 9.3424, t1: 9.3595, x: -0.607, y: 0.366, R: 0.0166, ang: 4.712, seed: 66.34 },
  { t0: 9.3424, t1: 9.3595, x: 0.039, y: 0.493, R: 0.0145, ang: 5.316, seed: 74.47 },
  // src 13.268
  { t0: 9.3595, t1: 9.3756, x: 0.013, y: -0.392, R: 0.1275, ang: 3.977, seed: 50.08 },
  { t0: 9.3595, t1: 9.3756, x: 0.752, y: 0.047, R: 0.1374, ang: 3.020, seed: 66.34 },
  { t0: 9.3595, t1: 9.3756, x: -0.038, y: 0.379, R: 0.0843, ang: 0.302, seed: 33.82 },
  { t0: 9.3595, t1: 9.3756, x: 0.756, y: -0.347, R: 0.0785, ang: 0.292, seed: 125.96 },
  { t0: 9.3595, t1: 9.3756, x: 0.133, y: 0.465, R: 0.0665, ang: 4.722, seed: 106.99 },
  { t0: 9.3595, t1: 9.3756, x: -0.420, y: 0.267, R: 0.0620, ang: 0.030, seed: 85.31 },
  { t0: 9.3595, t1: 9.3756, x: 0.652, y: 0.454, R: 0.0611, ang: 3.543, seed: 6.72 },
  { t0: 9.3595, t1: 9.3756, x: 0.105, y: 0.288, R: 0.0381, ang: 0.745, seed: 1.3 },
  { t0: 9.3595, t1: 9.3756, x: 0.552, y: 0.226, R: 0.0368, ang: 1.611, seed: 28.4 },
  { t0: 9.3595, t1: 9.3756, x: -0.532, y: -0.449, R: 0.0313, ang: 1.611, seed: 41.95 },
  { t0: 9.3595, t1: 9.3756, x: -0.273, y: -0.484, R: 0.0287, ang: 3.917, seed: 85.31 },
  { t0: 9.3595, t1: 9.3756, x: 0.709, y: 0.062, R: 0.0238, ang: 0.444, seed: 6.72 },
  { t0: 9.3595, t1: 9.3756, x: -0.427, y: 0.220, R: 0.0183, ang: 0.422, seed: 101.57 },
  { t0: 9.3595, t1: 9.3756, x: -0.170, y: -0.373, R: 0.0255, ang: 3.665, seed: 88.02 },
  { t0: 9.3595, t1: 9.3756, x: -0.251, y: 0.180, R: 0.0188, ang: 2.356, seed: 90.73 },
  { t0: 9.3595, t1: 9.3756, x: -0.378, y: -0.025, R: 0.0152, ang: 5.156, seed: 101.57 },
  // src 13.284
  { t0: 9.3756, t1: 9.3920, x: 0.646, y: 0.149, R: 0.2550, ang: 2.680, seed: 82.6 },
  { t0: 9.3756, t1: 9.3920, x: -0.777, y: -0.055, R: 0.2297, ang: 2.738, seed: 79.89 },
  { t0: 9.3756, t1: 9.3920, x: 0.567, y: -0.326, R: 0.1861, ang: 1.611, seed: 71.76 },
  { t0: 9.3756, t1: 9.3920, x: -0.893, y: 0.255, R: 0.1670, ang: 2.516, seed: 112.41 },
  { t0: 9.3756, t1: 9.3920, x: -0.079, y: 0.411, R: 0.1192, ang: 2.034, seed: 9.43 },
  { t0: 9.3756, t1: 9.3920, x: 0.219, y: 0.459, R: 0.0859, ang: 4.772, seed: 106.99 },
  { t0: 9.3756, t1: 9.3920, x: 0.425, y: -0.404, R: 0.0451, ang: 4.169, seed: 123.25 },
  { t0: 9.3756, t1: 9.3920, x: 0.255, y: -0.276, R: 0.0491, ang: 1.873, seed: 115.12 },
  { t0: 9.3756, t1: 9.3920, x: -0.250, y: 0.471, R: 0.0552, ang: 1.893, seed: 104.28 },
  { t0: 9.3756, t1: 9.3920, x: -0.622, y: -0.269, R: 0.0434, ang: 2.880, seed: 93.44 },
  { t0: 9.3756, t1: 9.3920, x: -0.701, y: -0.396, R: 0.0416, ang: 3.685, seed: 98.86 },
  { t0: 9.3756, t1: 9.3920, x: 0.148, y: 0.281, R: 0.0534, ang: 3.060, seed: 66.34 },
  { t0: 9.3756, t1: 9.3920, x: 0.364, y: 0.406, R: 0.0441, ang: 4.854, seed: 36.53 },
  { t0: 9.3756, t1: 9.3920, x: -0.465, y: 0.210, R: 0.0266, ang: 2.256, seed: 55.5 },
  { t0: 9.3756, t1: 9.3920, x: 0.024, y: 0.248, R: 0.0364, ang: 0.040, seed: 71.76 },
  { t0: 9.3756, t1: 9.3920, x: 0.035, y: 0.332, R: 0.0251, ang: 3.122, seed: 33.82 },
  { t0: 9.3756, t1: 9.3920, x: 0.698, y: 0.491, R: 0.0258, ang: 3.967, seed: 82.6 },
  { t0: 9.3756, t1: 9.3920, x: 0.143, y: -0.399, R: 0.0294, ang: 4.612, seed: 88.02 },
  { t0: 9.3756, t1: 9.3920, x: -0.766, y: -0.185, R: 0.0227, ang: 3.022, seed: 6.72 },
  { t0: 9.3756, t1: 9.3920, x: -0.470, y: 0.245, R: 0.0203, ang: 5.718, seed: 66.34 },
  { t0: 9.3756, t1: 9.3920, x: 0.458, y: 0.343, R: 0.0178, ang: 2.084, seed: 74.47 },
  { t0: 9.3756, t1: 9.3920, x: -0.506, y: 0.170, R: 0.0223, ang: 2.518, seed: 82.6 },
  { t0: 9.3756, t1: 9.3920, x: -0.743, y: -0.168, R: 0.0199, ang: 2.174, seed: 41.95 },
];
function putStrobe(t: number, n: number): number {
  for (const s of STROBE) {
    if (t >= s.t0 && t < s.t1) put(n++, s.x, s.y, s.R, 1, s.ang, 1, 1, 0.0025, s.seed, 1, 0, 0);
  }
  return n;
}
/** when the black reaches (x, y): from the edges and the ink pieces, in irregular lobes */
function inkTs(x: number, y: number, jitter: number): number {
  // the ink front: from the frame edges inward, irregular (smooth in space, so the black
  // comes in as scalloped lobes rather than a rectangle); src 13.00-13.30
  const wob = 0.26 * Math.sin(2.3 * x + 1.3) * Math.cos(3.1 * y + 0.5) + 0.12 * Math.sin(5.3 * x - 2.1 * y + 0.7);
  const e = Math.max(Math.abs(x) / 0.9, Math.abs(y + 0.03) / 0.52) + wob;
  // black lobes come in at random places too (more at the edges), paper islands remain
  const lobe = vn(x * 3.2 + 11.3, y * 3.2 + 4.1) * 0.65 + vn(x * 7.1 + 2.2, y * 7.1 + 9.7) * 0.35;
  // r2 (60 fps src 13.217-13.284): the black spreads from the edges and the ink pieces with
  // scalloped fronts (~30% black by 13.267); everything else breaks up at once after that
  // (src 13.13-13.20: the black already bites in from the frame edges with cusped fronts)
  return 9.12 + (1 - clamp(e)) * 0.6 + lobe * 0.22 + jitter;
}
// R1-22: under the big round pieces lie small ones (same paper, so invisible while covered);
// when the big ones go they are left as the many short dashes of src 13.35-13.40
const SMALL: Blob[] = [];
for (let i = 0; i < 420; i++) {
  const x = (r() - 0.5) * 1.84;
  const y = (r() - 0.5) * 1.06;
  // (they go a little before the big pieces over them, so no paper dots are left in the black)
  SMALL.push({ h: [x, y], R: 0.022 + 0.026 * r(), ts: inkTs(x, y, 0.02 * r()) - 0.045, q: 0.3 + 1.4 * r(), w: 0.6 + 0.6 * r(), dot: r() < 0.15, dl: 0.04 * r() });
}
const BLOBS: Blob[] = [];
{
  // (src 13.28-13.33: the round pieces are ~.12-.2 of the frame height across, ~.15-.2 apart)
  const cell = 0.13;
  const NX = 15;
  const NY = 9;
  for (let gy = 0; gy < NY; gy++) {
    for (let gx = 0; gx < NX; gx++) {
      const x = -0.94 + (gx + 0.5) * cell + (r() - 0.5) * 0.05;
      const y = -0.53 + (gy + 0.5) * cell + (r() - 0.5) * 0.05;
      const ts = inkTs(x, y, r() * 0.02);
      const dot = r() < 0.2;
      BLOBS.push({ h: [x, y], R: 0.092 + 0.04 * r(), ts, q: 0.25 + 1.55 * r(), w: 0.6 + 0.65 * r(), dot, dl: 0.03 * vn(x * 2.6 + 3.3, y * 2.6 + 8.1) + 0.008 * r() });
    }
  }
  // the union of the pieces must still be the whole paper: grow any piece next to a gap
  for (let sy = -0.51; sy <= 0.51; sy += 0.012) {
    for (let sx = -0.9; sx <= 0.9; sx += 0.012) {
      let best = -1e9;
      let bi = 0;
      let bd = 0;
      const gx0 = Math.floor((sx + 0.94) / cell);
      const gy0 = Math.floor((sy + 0.53) / cell);
      for (let gy = Math.max(0, gy0 - 1); gy <= Math.min(NY - 1, gy0 + 1); gy++) {
        for (let gx = Math.max(0, gx0 - 1); gx <= Math.min(NX - 1, gx0 + 1); gx++) {
          const i = gy * NX + gx;
          const d = Math.hypot(sx - BLOBS[i].h[0], sy - BLOBS[i].h[1]);
          if (BLOBS[i].R - d > best) {
            best = BLOBS[i].R - d;
            bi = i;
            bd = d;
          }
        }
      }
      if (best < 0.006) BLOBS[bi].R = bd + 0.008;
    }
  }
}
// R1-23 (src 13.43-13.80): a continuous stream of out-of-focus orange lights coming at the
// camera from around the instrument (above it too); small far ones near the middle, large near
// ones at the edges (~1/80 to ~1/8 of the frame height)
const BOKEH: Bok[] = [];
for (let i = 0; i < 280; i++) {
  const ang = r() * Math.PI * 2;
  const batch = i < 110;
  // screen radius and size at birth; z0 sets how fast they grow and spread (rr = R3 / z)
  const z0 = batch ? 1.1 + 1.1 * r() : 2.0 + 0.3 * r();
  // (src 13.47-13.60: the lights crowd round and behind the tubes and in front of the case)
  const rr0 = batch ? 0.1 + 0.45 * Math.pow(r(), 0.8) : 0.05 + 0.25 * Math.pow(r(), 1.8);
  // (R2 re-check vs src 13.47-13.60 at 540p: the lights are larger - median radius ~.027 H)
  const s0 = 1.2 * (batch ? 0.006 + 0.022 * Math.pow(r(), 1.6) : 0.005 + 0.017 * Math.pow(r(), 1.6));
  BOKEH.push({
    ang,
    R3: rr0 * z0,
    S3: s0 * z0,
    z0,
    t0: batch ? 9.375 + 0.04 * r() : 9.4 + 0.58 * ((i - 110) / 170) + 0.01 * r(),
    elong: 1.25 + 1.3 * r(),
    bright: 0.3 + 0.7 * r(),
    front: r() < 0.26,
    gain: 1,
  });
}
// (src 13.70-13.83) during the push large peach lights crowd in front of the instrument until
// they fill the picture with a cream haze
for (let i = 0; i < 60; i++) {
  const z0 = 1.5 + 0.8 * r();
  BOKEH.push({
    ang: r() * Math.PI * 2,
    R3: (0.04 + 0.4 * Math.pow(r(), 0.8)) * z0,
    S3: (0.022 + 0.04 * r()) * z0,
    z0,
    t0: 9.66 + 0.24 * (i / 60) + 0.01 * r(),
    elong: 1.2 + 0.8 * r(),
    bright: 0.6 + 0.4 * r(),
    front: true,
    gain: 0.32,
  });
}
// r2 (src 13.35-13.42): a warp of short cream dashes flying out from around the instrument,
// at every radius, thinning as they go
const STREAKS: { ang: number; R3: number; z0: number; t0: number; w: number; L: number }[] = [];
for (let i = 0; i < 240; i++) {
  STREAKS.push({ ang: r() * Math.PI * 2, R3: 0.12 + 0.45 * Math.pow(r(), 0.7), z0: 1.2 + 2.2 * r(), t0: 9.42 + 0.07 * r(), w: 0.003 + 0.006 * r(), L: 3 + 6 * r() });
}

// R1-25 (R3, src 13.80-13.90 at 1080p): the burst - near cream lights over the dark floor below
// the tubes (and out at the sides), stretched along the rays (the exit pincushion stretches them
// further) and split red / cyan; ~.04-.10 H wide. Own random stream, so nothing else moves.
{
  let sd = 0x5eed25;
  const r2 = (): number => {
    sd = (sd + 0x6d2b79f5) | 0;
    let q = Math.imul(sd ^ (sd >>> 15), 1 | sd);
    q = (q + Math.imul(q ^ (q >>> 7), 61 | q)) ^ q;
    return ((q ^ (q >>> 14)) >>> 0) / 4294967296;
  };
  for (let i = 0; i < 24; i++) {
    const side = r2() < 0.2;
    // (src 13.87: spread over the floor's width, few straight below the readout)
    const ang = side ? (r2() < 0.5 ? 0 : Math.PI) + (r2() - 0.5) * 0.7 : -Math.PI / 2 + (r2() < 0.5 ? -1 : 1) * (0.22 + 1.2 * r2());
    const z0 = 1.0 + 0.5 * r2();
    BOKEH.push({
      ang,
      R3: (0.2 + 0.3 * r2()) * z0,
      S3: (0.021 + 0.022 * r2()) * z0,
      z0,
      t0: 9.77 + 0.13 * (i / 24) + 0.01 * r2(),
      elong: 1.6 + 1.0 * r2(),
      bright: 0.75 + 0.25 * r2(),
      front: true,
      gain: 1,
      cream: 0.5,
    });
  }
}

/** integral over [t0, t] of push(t')^2 dt' with push = clamp((t'-9.5)/.4)^1.15 */
function pushInt(t: number, t0: number): number {
  const P = (x: number): number => {
    const u = clamp((x - 9.5) / 0.4);
    return (0.4 * Math.pow(u, 3.3)) / 3.3 + Math.max(0, x - 9.9);
  };
  return P(t) - P(t0);
}

export const MAX_PIECES = BLOBS.length + SMALL.length + SHARDS.length + STROBE.length + BOKEH.length + STREAKS.length;
const buf = new Float32Array(MAX_PIECES * PIECE_STRIDE);

function put(n: number, x: number, y: number, rad: number, elong: number, ang: number, kind: number, alpha: number, blur: number, hx: number, hy: number, tint: number, shade: number): void {
  buf.set([x, y, rad, elong, ang, kind, alpha, blur, hx, hy, tint, shade], n * PIECE_STRIDE);
}

/**
 * t: show time; C: screen point the pieces converge on / fly out from (the base of the middle
 * tubes); push: 0..1 the final camera push (bokeh swell and speed up).
 */
export function pieceFrame(t: number, C: V2, push: number): { data: Float32Array; back: number; front: number } {
  let n = 0;
  // paper pieces: the small ones first (under the big ones)
  if (t < 9.52) {
    const piece = (b: Blob, big: boolean): void => {
      // (R2: no creeping ink front before the break-up - at 60 fps the black of src 13.08-13.28
      // is all one-frame pieces, STROBE. b.ts is no longer used; the pieces are still generated
      // the same way so the random sequence stays the same)
      // R1-22 (R3, src 13.25-13.30): the paper does not fall apart into even dots - dark gaps
      // open between the round pieces along smooth corridors (first at the frame edges, so the
      // paper outline turns scalloped by 13.25), leaving a few large cloud-shaped islands of
      // merged circles (the largest ~1/3 of the frame height) that then draw out into radial
      // capsules of very different lengths and widths (13.317-13.40)
      const hx = b.h[0];
      const hy = b.h[1];
      const isl = vn(hx * 2.6 + 5.3, hy * 2.6 + 1.7) * 0.55 + vn(hx * 5.5 + 2.1, hy * 5.5 + 7.7) * 0.45;
      // (src 13.30: islands reach the side edges too)
      const islS = isl + 0.12 * smooth(0.45, 0.85, Math.abs(hx));
      const edge = Math.max(Math.abs(hx) / 0.9, Math.abs(hy) / 0.5);
      const th = -0.25 + 0.75 * smooth(9.32, 9.43, t) + 0.15 * smooth(0.62, 1.05, edge) * smooth(9.30, 9.36, t) + (big ? 0 : 0.05);
      const keep = Math.pow(clamp((islS - th) / 0.16), 0.7);
      const g = smooth(9.352 + b.dl, 9.385 + b.dl, t);
      let rad = b.R * keep * (1 - (big ? 0.1 : 0.5) * g);
      let x = hx;
      let y = hy;
      let elong = 1;
      let ang = 0;
      let tint = 0;
      let alpha = 1;
      const v = big ? clamp((t - 9.39) / 0.13) : clamp((t - 9.395) / 0.125);
      if (v > 0) {
        const dx = hx - C[0];
        const dy = hy - C[1];
        const d = Math.hypot(dx, dy) || 1;
        const k = big ? 1 + 0.25 * v + 1.6 * v * v : 1 + 0.3 * v + 1.3 * v * v;
        x = C[0] + dx * k;
        y = C[1] + dy * k;
        if (big) {
          // widths .3-1 of the piece, lengths from round dots to ~1/3 of the frame height
          const wn = (b.w - 0.6) / 0.65;
          const r0 = rad * (1 - (0.7 - 0.7 * wn * wn) * smooth(0.08, 0.5, v)) * (1 - 0.45 * v);
          const ext = b.dot ? 0 : (0.015 + 0.17 * Math.pow(b.q / 1.8, 1.6)) * smooth(0, 0.42, v);
          rad = r0;
          elong = 1 + ext / Math.max(r0, 1e-4);
          rad *= 1 - 0.6 * smooth(0.6, 0.95, v);
          alpha = 1 - smooth(0.72, 0.96, v);
        } else {
          elong = b.dot ? 1 : 1 + (1.5 * v + 6 * v * v) * b.q;
          rad *= 1 - 0.5 * v;
          alpha = 1 - smooth(0.75, 1, v);
        }
        ang = Math.atan2(dy, dx);
        tint = clamp(1 - d / 0.5) * v * 0.9;
      }
      if (alpha <= 0.002 || rad < 0.002) return;
      put(n++, x, y, rad, elong, ang, 0, alpha, 0, hx, hy, tint, 0.3 * g);
    };
    for (const b of SMALL) piece(b, false);
    for (const b of BLOBS) piece(b, true);
  }
  // the warp of short dashes right after the capsules
  if (t > 9.42 && t < 9.54) {
    for (const s of STREAKS) {
      const age = t - s.t0;
      if (age < 0) continue;
      const z = s.z0 - 9 * age;
      if (z < 0.25) continue;
      const rr = s.R3 / z;
      const x = C[0] + Math.cos(s.ang) * rr * 1.3;
      const y = C[1] + Math.sin(s.ang) * rr * 0.8;
      if (Math.abs(x) > 1.0 || Math.abs(y) > 0.58) continue;
      const w = s.w * (1.4 / (0.6 + z)) * (1 - 0.5 * smooth(9.47, 9.53, t));
      const a = smooth(0, 0.025, age) * (1 - smooth(9.49, 9.535, t));
      // near the middle they glow orange (lit by the tubes)
      const tint = clamp(1 - rr / 0.35) * 0.8;
      put(n++, x, y, w, 1 + s.L * clamp(rr / 0.25), Math.atan2(Math.sin(s.ang) * 0.8, Math.cos(s.ang) * 1.3), 0, a, 0, C[0] + 0.1, C[1] + 0.2, tint, 0);
    }
  }
  n = putStrobe(t, n);
  // orange bokeh (behind / in front of the instrument)
  let back = n;
  // R1-25 (R4, 60 fps src 13.833-13.90): the floor under the burst holds dark for three frames
  // (bottom fifth mean 82 / 82 / 79) before the smear floods it (107, 141) - the near cream
  // lights dim while they cross it
  const creamK = 1 - 0.5 * smooth(9.93, 9.962, t) * (1 - smooth(9.968, 9.998, t));
  const bok = (front: boolean): void => {
    for (const b of BOKEH) {
      if (b.front !== front || t < b.t0 || b.gain <= 0) continue;
      const age = t - b.t0;
      // flight towards the camera; the push speeds it up (integrated speed, so no jumps)
      const z = b.z0 - 1.1 * age - 3.4 * pushInt(t, b.t0);
      if (z < 0.12) continue;
      const rr = b.R3 / z;
      const x = C[0] + Math.cos(b.ang) * rr * 1.25;
      const y = C[1] + Math.sin(b.ang) * rr * 0.8;
      if (Math.abs(x) > 1.05 || Math.abs(y) > 0.62) continue;
      const size = Math.min(b.S3 / z, b.cream ? 0.085 : 0.16) * (front ? 1.2 : 1);
      const a = (0.7 + 0.1 * push) * smooth(0, 0.05, age) * (0.5 + 0.5 * b.bright) * b.gain * (b.cream ? creamK : 1);
      // the near ones turn peach during the push (src 13.70-13.80)
      // (src 13.43-13.57 saturated orange; the near ones turn peach during the push, 13.70-13.80)
      const tint = b.bright * 0.45 + (front ? 0.45 * push : 0.2 * push) + (b.cream ?? 0);
      put(n++, x, y, size, 1 + (b.elong - 1) * clamp(rr / 0.35), Math.atan2(Math.sin(b.ang) * 0.8, Math.cos(b.ang) * 1.25), 2, b.cream ? Math.min(a * 0.8, 0.6) * (1 - 0.5 * smooth(0.045, 0.09, size)) : a, size * (b.cream ? 0.1 : 0.32), 0, 0, tint, 0);
    }
  };
  bok(false);
  back = n;
  bok(true);
  return { data: buf, back, front: n - back };
}

/** the T13 tail (before the exit shot): only the measured ink strobe pieces */
export function strobeFrame(t: number): { data: Float32Array; count: number } {
  return { data: buf, count: putStrobe(t, 0) };
}
