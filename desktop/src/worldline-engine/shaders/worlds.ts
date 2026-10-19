import { COMMON } from './common';
import { GEOM_DEFINES } from '../show/geom';
import { GLYPH_W, GLYPH_H, GLYPH_MARGIN, CELLS } from '../engine/glyphs';

// Background worlds, one full-screen pass each. All procedural.

// Mottled parchment of the original: near-white in the middle, brown smoke stains that thicken
// toward the edges, dark corners, dry-brush scratches low left, spatter above. No ruled lines.
const PARCHMENT = /* glsl */ `
uniform float uStain;
uniform float uLockInk;
vec3 parchment(vec2 p, float t, float bright, float specks){
  vec2 dp = p + vec2(t * .010, -t * .006);
  vec2 q = vec2(fbm(dp * 1.6 + vec2(0., t * .03)), fbm(dp * 1.6 + vec2(5.2, 1.3) - t * .025));
  float f1 = fbm(dp * 2.2 + q * 1.6);
  float f2 = fbm(dp * 5.5 + q * 2.4 + 3.7);
  float r = length(p * vec2(.62, 1.));
  float cen = exp(-dot(p * vec2(.9, 1.6), p * vec2(.9, 1.6)) * 2.2);
  vec3 base = vec3(.985, .945, .885);
  // watercolour-like smoke stains: billows everywhere, denser away from the centre,
  // with darker dried rims
  float dens = .15 + uStain * .55 + 1.0 * smoothstep(.10, .70, r);
  float m1 = smoothstep(.47 - .05 * uStain, .58, f1) * dens;
  float m2 = smoothstep(.56, .64, f2) * (.15 + .6 * smoothstep(.2, .7, r));
  float rim = exp(-pow((f1 - .475) / .012, 2.)) * dens * .55 + exp(-pow((f2 - .565) / .010, 2.)) * .30 * dens;
  vec3 c = mix(base, vec3(.72, .62, .53), clamp(m1 * .75 + m2 * .45, 0., 1.));
  c = mix(c, vec3(.53, .43, .35), clamp(rim, 0., 1.) * .5);
  // burnt edges and corners: cloudy, not a clean vignette (normalised so the side edges sit
  // at 1, the top/bottom edges at .8 and the corners at 1.28, as measured on the original)
  float d = length(vec2(abs(p.x) / .889, abs(p.y) / .5 * .66)) + (f1 - .5) * .45 + (f2 - .5) * .15;
  c = mix(c, vec3(.47, .36, .30), smoothstep(.70, 1.30, d) * .88);
  c = mix(c, vec3(.12, .085, .07), smoothstep(1.0, 1.36, d) * .85);
  if (uLockInk > 0.) {
    // R2-03 (R3, src 11.60 at 1080p): big soft pink-brown ink clouds round the instrument, denser
    // and darker towards the bottom corners (corner means ~115/109 top, ~86/88 bottom)
    float f3 = fbm(dp * 1.15 + q * 1.1 + 8.3);
    float ring = smoothstep(.22, .55, r) * (.75 + .25 * smoothstep(.1, -.35, p.y));
    float cl = smoothstep(.52, .64, f3) * ring;
    // the clean paper between the clouds is brighter, pink-white (src outer-paper L p90 ~90)
    c = mix(c, vec3(1., .955, .935), (1. - cl) * smoothstep(.12, .45, r) * (1. - smoothstep(.66, 1.04, d)) * .72 * uLockInk);
    c = mix(c, vec3(.60, .46, .40), cl * .90 * uLockInk);
    c = mix(c, vec3(.55, .42, .37), exp(-pow((f3 - .51) / .02, 2.)) * ring * .22 * uLockInk);
    vec2 bc = vec2(abs(p.x) / .889, (p.y + .5) / 1.0);
    float bot = smoothstep(.40, 1.0, bc.x) * smoothstep(.60, .0, bc.y);
    c = mix(c, vec3(.36, .27, .24), bot * (.78 + .12 * step(p.x, 0.)) * uLockInk);
    // a warmer, pinker paper overall
    c *= mix(vec3(1.), vec3(1.0, .965, .955), uLockInk);
  }
  c = mix(c, vec3(1., .975, .94), bright * cen * .75);
  // sparse dry-brush scratches at about 35 degrees, mostly low left, faint top right
  vec2 sp = rot2(-.62) * p;
  float lines = smoothstep(.87, .985, vnoise(vec2(sp.x * 1.3, sp.y * 240.))) * smoothstep(.50, .80, vnoise(sp * vec2(3.2, .8) + 2.));
  float zone = smoothstep(-.10, -.34, p.y) * smoothstep(.55, -.30, p.x) + smoothstep(.20, .45, p.y) * smoothstep(.10, .45, p.x) * .35;
  c = mix(c, vec3(.30, .22, .17), clamp(lines * zone * 1.5, 0., 1.) * .55);
  // spatter in clusters
  float cl = smoothstep(.58, .78, fbm(p * 3.1 + 7.3));
  float sp1 = step(.993, hash12(floor(p * 260.))) * cl;
  float sp2 = step(.9985, hash12(floor(p * 110.))) * cl;
  c = mix(c, vec3(.26, .20, .16), (sp1 * .65 + sp2 * .75) * specks);
  c *= .975 + .04 * vnoise(p * 380.);
  return c;
}
`;

export const PARCH_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform float uAspect;
uniform float uPx;
uniform float uZoom;
uniform vec2 uOff;
uniform float uTime;
uniform float uBright;
uniform float uDark;
uniform float uCool;
uniform float uDoors;
uniform float uDoorZ;
uniform float uLattice;
uniform float uWarp;
uniform float uVpY;
uniform vec4 uFz;
uniform float uSky;
uniform vec4 uInk[6];
uniform int uInkN;
uniform float uPlain;
uniform float uPlainDark;
uniform float uSpecks;
${COMMON}
${PARCHMENT}

float gline(float v){
  float fw = max(fwidth(v), 1e-4);
  float d = abs(fract(v + .5) - .5);
  return 1. - smoothstep(fw * .6, fw * 1.6, d);
}

// --- door corridor (T5 / T6) ------------------------------------------------------------------
// Free-standing door frames in the sky, fitted to the 60 fps source frames (src 6.90-7.93; the
// edges were tracked row by row and fitted with the lens below, rms < 1 px). uFz holds the depths
// of the back edges of the inner side faces (reveals) of F0-F3. Measured on every frame: the front edge of a
// reveal sits at 1.082x the screen offset of its back edge and the free edge of the leaf (swung
// away along the wall beyond the frame) at 1/1.28 of it, so each frame keeps its proportions while
// the timeline moves it. Each frame plane carries mirrored machinery at mid height outside its
// posts (a beam on F1, struts left and grid towers right on F2/F3). In the flight a pincushion lens
// bends the background: s = p (1 + K |p|^4), K = uWarp.
const float WO = .52, RV = 1.082, LR = 1.28, YB = -.44, YT = .43, BH = .063;

vec2 unwarp(vec2 s, float k){
  float rs = length(s);
  if (k <= 1e-4 || rs < 1e-6) return s;
  float r = rs / (1. + k * rs * rs * rs * rs);
  for (int i = 0; i < 4; i++) {
    float r4 = r * r * r * r;
    r -= (r + k * r4 * r - rs) / (1. + 5. * k * r4);
  }
  return s * (r / rs);
}

void over(inout vec4 acc, vec3 c, float a){
  acc.rgb += (1. - acc.a) * c * a;
  acc.a += (1. - acc.a) * a;
}

// antialiased periodic line, half width w (in periods), pixel footprint fw (in periods)
float gl2(float v, float w, float fw){
  float d = abs(fract(v + .5) - .5);
  return 1. - smoothstep(w - fw, w + fw, d);
}
float box2(vec2 q, vec2 lo, vec2 hi, vec2 fw){
  vec2 a = smoothstep(lo - fw, lo + fw, q) * (1. - smoothstep(hi - fw, hi + fw, q));
  return a.x * a.y;
}

// mirrored machinery in a frame plane outside the posts. xo: distance outward from the post,
// ay: |height| (world), pw: world size of a pixel there, kind: 1 beam, 2 struts, 3 grid towers.
vec4 machinery(float xo, float ay, float pw, float kind){
  vec4 r = vec4(0.);
  if (kind < 1.5) {
    // F1: the beam: grey-brown border rails, white mirrored struts with dark chevrons inside
    if (ay < BH) {
      float v = ay / BH;
      float fv = pw / BH;
      vec3 c = vec3(.95, .93, .90);
      float u = xo / BH;
      float chev = gl2(u * .55 - v * .55, .045, fv * .55);
      float tick = gl2(u * 1.1, .04, fv * 1.1) * step(v, .5);
      float blk = step(.6, vnoise(vec2(u * 1.6, floor(v * 6.))));
      float cen = 1. - smoothstep(.03 - fv, .03 + fv, v);
      c = mix(c, vec3(.42, .40, .41), max(max(chev * .8, tick * .55), max(blk * .22, cen * .7)));
      float bord = smoothstep(.80 - fv, .80 + fv, v);
      c = mix(c, vec3(.60, .50, .45), bord);
      float ln = max(1. - smoothstep(.0, fv * 1.5, abs(v - .80)), 1. - smoothstep(.0, fv * 1.5, abs(v - .99)));
      c = mix(c, vec3(.30, .26, .25), ln * .8);
      r = vec4(c, 1. - smoothstep(1. - fv, 1., v));
    }
    return r;
  }
  // F2 / F3 (src 7.70 / 7.77 at 1:1): grey-brown rails at |Y| .07-.11 with a light outer edge,
  // a light mirrored kaleidoscope band between them, white jagged struts on the left side and light
  // lattice towers on the right, all mirrored about the horizon
  float lw = pw * 1.2;
  if (ay < .07) {
    // light band: white horizontal bars with grey outlines at three heights, broken along the
    // beam, and a centre spine (mirrored)
    vec3 c = vec3(.90, .88, .85);
    for (int k = 0; k < 3; k++) {
      float yc = .014 + .02 * float(k);
      float seg = floor(xo / .045 + float(k) * .5);
      if (hash11(seg * 7.1 + float(k) * 13.) < .3) continue;
      float hh = .005 + .002 * float(k);
      float bar = 1. - smoothstep(hh - pw, hh + pw, abs(ay - yc));
      float ol = 1. - smoothstep(hh + pw * 1.2, hh + pw * 2.6, abs(ay - yc));
      float fx = fract(xo / .045 + float(k) * .5);
      float cut = smoothstep(.04, .08, fx) * (1. - smoothstep(.92, .96, fx));
      c = mix(c, vec3(.50, .47, .47), clamp(ol - bar, 0., 1.) * cut * .8);
      c = mix(c, vec3(.99, .97, .95), bar * cut);
    }
    float spine = 1. - smoothstep(.003 - pw, .003 + pw, ay);
    c = mix(c, vec3(.50, .47, .47), spine * .6);
    r = vec4(c, 1.);
  } else if (ay < .11) {
    vec3 c = vec3(.56, .48, .44) * (.94 + .06 * vnoise(vec2(xo * 40., ay * 90.)));
    float st = gl2(ay * 80., .08, pw * 80.);
    c = mix(c, vec3(.47, .40, .37), st * .5);
    c = mix(c, vec3(.88, .84, .80), 1. - smoothstep(.0, lw * 1.5, abs(ay - .109)));
    c = mix(c, vec3(.33, .28, .27), (1. - smoothstep(.0, lw * 1.5, abs(ay - .071))) * .8);
    r = vec4(c, 1.);
  }
  if (kind < 2.5) {
    // the white cluster: a jagged white silhouette with grey veins (fractal edge, tight envelope
    // around xo .09-.21, |Y| < .19), over the rails (src 7.70 / 7.77)
    if (ay < .21 && xo > .06 && xo < .25) {
      vec2 q = vec2((xo - .15) / .055, ay / .155);
      float env = 1. - pow(abs(q.x), 1.4) - abs(q.y) * .9;
      float n = fbm(vec2(xo * 46., ay * 30.) + 3.7);
      float fld = env + (n - .5) * 1.7;
      float fw = pw * 22.;
      float blob = smoothstep(.34 - fw, .34 + fw, fld);
      float ol = smoothstep(.26 - fw, .26 + fw, fld) * .85;
      float vein = 1. - smoothstep(.0, .045 + fw, abs(fbm(vec2(xo * 55., ay * 30.) + 9.1) - .5));
      vec3 c = mix(vec3(.98, .96, .94), vec3(.56, .53, .53), vein * .6);
      c = mix(vec3(.45, .42, .42), c, blob);
      float a2 = max(blob, ol);
      r = vec4(mix(r.rgb, c, a2), max(r.a, a2));
    }
  } else {
    // light lattice towers above / below the rails (src 7.63-7.82 right side)
    vec2 q = vec2(xo, ay);
    float t1 = box2(q, vec2(.07, .11), vec2(.14, .30 - (xo - .07) * .6), vec2(pw));
    float t2 = box2(q, vec2(.0, .11), vec2(.04, .20), vec2(pw));
    float m = max(t1, t2);
    if (m > 0.) {
      float g = max(gl2((xo + ay) * 40., .07, pw * 57.), gl2((xo - ay) * 40., .07, pw * 57.));
      vec3 c = mix(vec3(.92, .89, .86), vec3(.42, .39, .40), g);
      float a2 = m * (.45 + .55 * g);
      r = vec4(mix(r.rgb, c, a2), max(r.a, a2));
    }
  }
  return r;
}

// a leaf swung open along the wall beyond its frame: a (0 hinge .. 1 free edge), b (0 .. 1 up);
// fa / fb: pixel footprints
vec3 leafAway(float a, float b, float fa, float fb){
  // src 7.60 (1080p row 300 / crop): white leaf, a thin grey line near the hinge (a ~.2), and two
  // narrow slots in each half, mirrored about the horizon (a .35-.50 / .54-.83, b .75-.95 /
  // .06-.26), ~35 levels darker than the leaf with thin grey rims
  vec3 c = vec3(.92, .89, .85) * (.95 + .05 * b);
  float rim = 0.;
  for (int k = 0; k < 4; k++) {
    vec2 lo = vec2(k % 2 == 0 ? .35 : .54, k < 2 ? .75 : .06);
    vec2 hi = vec2(k % 2 == 0 ? .50 : .83, k < 2 ? .95 : .26);
    float inside = box2(vec2(a, b), lo, hi, vec2(fa, fb));
    vec2 wr = vec2(fa * 1.2 + .012, fb * 1.2 + .004);
    float edge = max(
      max(1. - smoothstep(.0, wr.x, abs(a - lo.x)), 1. - smoothstep(.0, wr.x, abs(a - hi.x))) * step(lo.y, b) * step(b, hi.y),
      max(1. - smoothstep(.0, wr.y, abs(b - lo.y)), 1. - smoothstep(.0, wr.y, abs(b - hi.y))) * step(lo.x, a) * step(a, hi.x));
    rim = max(rim, edge);
    c = mix(c, c * .85, inside);
  }
  rim = max(rim, (1. - smoothstep(.0, fa * 1.2 + .01, abs(a - .2))) * .7);
  c = mix(c, vec3(.44, .40, .40), rim * .75);
  // the free edge (leaf thickness) seen at a grazing angle: a darker strip
  c = mix(c, vec3(.45, .41, .40), (1. - smoothstep(.0, fa * 1.5 + .025, 1. - a)) * .8);
  return c;
}

// F0 leaf (T5 near frame, src 6.45-7.25): hinged at the front edge of the reveal and swung
// outward-away by ~41 deg, so its free edge sits at ~1.21x the reveal front edge (src 6.80: leaf
// from x 216 to ~60 px at 1080p; 7.00: to the screen edge). Panels as the R2 leaf.
vec4 leafF0(vec3 d, float zf, float side, float pxp){
  float Lw = .52;
  vec3 H = vec3(side * WO, 0., zf);
  vec3 u = vec3(side * .755, 0., .656);
  vec3 n = vec3(-u.z, 0., u.x);
  float den = dot(d, n);
  if (abs(den) < 1e-5) return vec4(0.);
  float t = dot(H, n) / den;
  if (t <= 0.) return vec4(0.);
  vec3 P = d * t;
  float a = dot(P - H, u) / Lw;
  float b = (P.y - YB) / (YT - YB);
  float aa = pxp * t * t * 2. / Lw;
  float m = smoothstep(-aa, aa, a) * smoothstep(-aa, aa, 1. - a) * step(0., b) * step(b, 1.);
  if (m <= 0.) return vec4(0.);
  // (pre-compensated for the post grade: src leaf L ~228 at 6.80)
  vec3 c = vec3(1., .97, .93) * (.95 + .05 * b);
  vec2 pc = vec2(a, b);
  float pan = 0.;
  for (int i = 0; i < 2; i++) for (int j = 0; j < 3; j++) {
    vec2 lo = vec2(.13 + .44 * float(i), j == 0 ? .06 : (j == 1 ? .33 : .67));
    vec2 hi = lo + vec2(.31, j == 1 ? .29 : .25);
    float sd = sdBox(pc - (lo + hi) * .5, (hi - lo) * .5);
    pan = max(pan, 1. - smoothstep(.0, .012 + aa, abs(sd)));
    c = mix(c, c * .93, step(sd, 0.) * .5);
  }
  c = mix(c, vec3(.42, .40, .41), pan * .65);
  // the free edge (leaf thickness): a dark strip (src 6.80: L ~94 at x 88)
  c = mix(c, vec3(.33, .30, .30), (1. - smoothstep(.0, aa * 1.5 + .025, 1. - a)) * .85);
  return vec4(c, m);
}

vec4 doors(vec2 p, float pxp){
  vec3 d = vec3(p - vec2(0., uVpY), 1.);
  float ax = abs(d.x);
  float zw = WO / max(ax, 1e-4);           // where the ray meets the wall line |X| = WO
  float Yw = d.y * zw;
  float zs = d.y > 1e-4 ? YT / d.y : 1e6;  // where it meets the soffit plane Y = YT
  vec4 acc = vec4(0.);
  // F0: its leaves (outside the opening, in front of everything behind them)
  if (uFz.x > .05) {
    float zf0 = uFz.x / RV;
    vec4 L = leafF0(d, zf0, -1., pxp);
    vec4 R = leafF0(d, zf0, 1., pxp);
    over(acc, L.rgb, L.a);
    over(acc, R.rgb, R.a);
  }
  float zbs[4] = float[4](uFz.x, uFz.y, uFz.z, uFz.w);
  // the inner surface the ray meets first (reveal / swung leaf / rail line of F0-F3)
  float wz = 1e6;
  vec4 wc = vec4(0.);
  for (int i = 0; i < 4; i++) {
    float zb = zbs[i];
    float zf = zb / RV;
    float ze = i == 0 ? zb : zb * LR;   // F0 has no leaf beyond the frame
    if (ze < .05) continue;
    float zd = zb - zf;
    if (Yw >= YB && Yw <= YT && zw < wz) {
      if (zw >= zf && zw <= zb) {
        float u = (zw - zf) / zd;
        float fu = pxp * zw * zw / (WO * zd);
        // tan (src 7.60 row 300: (145,110,95) -> (152,116,101); 6.45-7.25 F0: L 112-145); it reads
        // darker only where the corner vignette covers it
        vec3 c = i == 0 ? mix(vec3(.74, .62, .56), vec3(.80, .68, .61), smoothstep(.15, 1., u))
                        : mix(vec3(.56, .43, .37), vec3(.62, .48, .42), smoothstep(.15, 1., u));
        // F0 (src 6.80 row 520, front to back: dark line, a light stripe, tan)
        if (i == 0) c = mix(c, vec3(.96, .93, .89), smoothstep(.16, .22, u) * (1. - smoothstep(.38, .44, u)));
        c *= .93 + .07 * vnoise(vec2(Yw * 30., float(i) * 7.));
        float ed = 1. - smoothstep(.0, fu * 1.5 + .04, u);
        c = mix(c, vec3(.20, .17, .17), ed * .8);
        wz = zw; wc = vec4(c, 1.);
      } else if (i > 0 && zw > zb && zw <= ze) {
        float a = (zw - zb) / (ze - zb);
        float b = (Yw - YB) / (YT - YB);
        float fa = pxp * zw * zw / (WO * (ze - zb));
        float fb = pxp * zw / (YT - YB);
        wz = zw; wc = vec4(leafAway(a, b, fa, fb), 1.);
      }
    }
    // thin rails along the wall tops / bottoms from the leaf end to the next frame
    float zn = i < 3 ? zbs[i + 1] / RV : ze * 1.6;
    if (i > 0 && zw > ze && zw < zn && zw < wz) {
      float pw = pxp * zw;
      float rl = max(1. - smoothstep(pw * .6, pw * 1.6, abs(Yw - YT)), 1. - smoothstep(pw * .6, pw * 1.6, abs(Yw - YB)));
      if (rl > 0.) { wz = zw; wc = vec4(vec3(.30, .27, .27), rl * .8); }
    }
  }
  bool wallDone = false;
  for (int i = 1; i < 4; i++) {
    float zf = zbs[i] / RV;
    if (!wallDone && wz < zf) { over(acc, wc.rgb, wc.a); wallDone = true; }
    if (zf < .05) continue;
    vec2 P = d.xy * zf;
    float px = abs(P.x);
    float pw = pxp * zf;
    // the lintel: one thin dark line across the top of the opening (src 7.30 / 7.60)
    float lint = step(px, WO) * (1. - smoothstep(pw * .7, pw * 1.7, abs(P.y - YT)));
    if (lint > 0.) over(acc, vec3(.32, .28, .27), lint * .85);
    float xo = px - WO;
    if (xo > 0. && P.y > YB && P.y < YT) {
      // F1: the long beam out to the edge; F2/F3: compact struts (left) / grid towers (right)
      // reaching ~.2 beyond the post (src 7.70: struts from x 500 to 180 px at 1080p)
      float kind = i == 1 ? 1. : (P.x < 0. ? 2. : 3.);
      float reach = i == 1 ? .55 : .30;
      vec4 mc = machinery(xo, abs(P.y), pw, kind);
      mc.a *= uLattice * (1. - smoothstep(reach - .04, reach, xo));
      over(acc, mc.rgb, mc.a);
    }
  }
  if (!wallDone) over(acc, wc.rgb, wc.a);
  return acc;
}

void main(){
  vec2 s = (vUv - .5) * vec2(uAspect, 1.);
  vec2 w = (s - uOff) / uZoom;
  vec3 c = parchment(w, uTime, uBright, uSpecks);
  if (uPlain > 0.) {
    // bare beige field of the gear shot, heavy vignette
    // (src 8.50-8.90: warm cream centre, a deep brown vignette)
    // R1-14 (R3, src 8.60-8.80 at 1080p: bright paper ~(235,216,198) - peach, not neutral cream;
    // corners ~L 25)
    vec3 b = vec3(1.075, .962, .875) * (.94 + .06 * fbm(s * 3. + uTime * .2));
    float r = length(s * vec2(.72, 1.));
    b = mix(b, vec3(.30, .19, .12), smoothstep(.26, .86, r) * .94);
    b *= 1. - smoothstep(.52, 1.0, r) * .70;
    b = mix(b, b * .25, uPlainDark * smoothstep(.1, -.6, s.x + .2 * s.y));
    b *= 1. - uPlainDark * .35;
    c = mix(c, b, uPlain);
  }
  // the flight through the doors: a pincushion lens on the background only (the instrument
  // stays straight); measured on the 60 fps source frames (k ~ 0 at src 7.50, .40 at 7.60, 1.49
  // at 7.83)
  vec2 sb = unwarp(s, uWarp);
  if (uSky > 0.) {
    // T5/T6: the frames stand in a clean light sky with soft grey clouds
    // (src 6.45-7.90: top centre (239,225,214) sd 6, bottom centre (229,212,202))
    // (values pre-compensated for the post grade, measured on the render)
    vec3 sk = mix(vec3(.962, .89, .848), vec3(1., .925, .87), smoothstep(-.5, .45, s.y));
    float cl = fbm(s * vec2(1.6, 2.4) + vec2(uTime * .02, 3.1));
    // soft clouds, lighter and darker than the base (src blurred sky: top sd 7, p5-p95 216-238;
    // below the case sd 10, 201-234)
    sk *= 1. + (cl - .5) * mix(.55, .48, smoothstep(.25, -.35, s.y));
    c = mix(c, sk, uSky);
  }
  float rr2 = dot(sb, sb);
  float pxp = uPx / (1. + 5. * uWarp * rr2 * rr2);
  if (uDoors > 0.) {
    // diagonal floor boards in perspective below the doorway
    vec2 fp = sb - vec2(0., -.03);
    if (fp.y < -.07) {
      float zf = .32 / -fp.y;
      vec2 fl = rot2(.70) * vec2(fp.x * zf, zf + uDoorZ * 2.2);
      float px = fl.x / .085;
      float gx = abs(fract(px) - .5);
      float lx = 1. - smoothstep(.5 - fwidth(px) * 1.4, .5, gx + fwidth(px) * .3);
      float pz = fl.y / .55 + hash11(floor(px) + 7.);
      float gz = abs(fract(pz) - .5);
      float lz = 1. - smoothstep(.5 - fwidth(pz) * 1.4, .5, gz + fwidth(pz) * .3);
      float fade = smoothstep(-.07, -.2, fp.y) * smoothstep(.15, .6, abs(fp.x) + (-.07 - fp.y));
      c = mix(c, c * vec3(.62, .58, .56), max(lx, lz * .8) * fade * .22 * uDoors);
    }
    vec4 d = doors(sb, pxp);
    c = mix(c, d.rgb / max(d.a, 1e-3), d.a * uDoors);
  }
  // black ink pieces over the end of the flight, before the T7 cut (src 7.834-7.918: a short bar
  // and blobs above tubes 3-5 rising, two pieces under the case on the last frame)
  for (int i = 0; i < 6; i++) {
    if (i >= uInkN) break;
    vec4 b = uInk[i];
    float dd = sdBox(s - b.xy, b.zw) + (vnoise(s * 70. + float(i) * 7.3) - .5) * min(b.z, b.w) * .7;
    c = mix(c, vec3(.06, .05, .05), 1. - smoothstep(-uPx, uPx, dd));
  }
  if (uSky > 0.) {
    // dark brown corners over sky and frames alike, deepening through the flight
    // (src top-left 150x120 box: L 88 / 75 / 61 / 56 at 6.80 / 7.30 / 7.60 / 7.70)
    // fitted to src 6.80 / 7.30 luminance (16 points: top and left edge rows, the diagonal, the
    // light leaf / sky regions at the sides; max error .10): linear in a stretched radius from .90
    // to 1.40, relative luminance down to .05
    vec2 vq = vec2(abs(s.x) / .889, abs(s.y) / .5 / 1.15);
    c = mix(c, vec3(.17, .13, .12), min(clamp((length(vq) - .90) / .50, 0., 1.) * 1.13, 1.) * uSky);
  }
  float l = dot(c, vec3(.3, .59, .11));
  c = mix(c, vec3(l) * vec3(.82, .88, .97), uCool * .7);
  c *= 1. - uDark * (1. - .22 * exp(-dot(s, s) * 3.));
  o = vec4(c, 1.);
}`;

// T1-T3: the line-art doorway tunnel (nested scaled copies of the instrument outline, the old
// reading drawn as wire digits), tan door posts, dark marble, then moss, cracks and parchment.
export const CORRIDOR_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform sampler2D uAtlas;
uniform float uAspect;
uniform float uPx;
uniform float uT;
uniform vec2 uVP;
uniform float uTravel;
uniform float uPTravel;
uniform float uLines;
uniform float uSpread;
uniform float uFadeIn;
uniform float uPillar;
uniform float uSolid;
uniform float uMoss;
uniform float uCrack;
uniform float uSoft;
uniform float uDiss;
uniform vec2 uFrontPosts;
uniform float uReveal;
uniform float uOnly;
uniform float uSparse;
uniform float uRow;
uniform float uCells[8];
// R6-A (T1 only; (.71, 0, 0, 0) = R5): x row scale, y log2 of the largest copy that shows its
// digits (the trail behind the row), z digits on the copies fading out (0..1), w the T1 look
// (bold wire digits, dimmer far copies)
uniform vec4 uT1;
// R6-A: x the copy lines nearer the vanishing point than this leave (r / half height; 0 = off),
// y the soft edge; z the copies left in the fade keep full strength (0 = as the hold); w unused
uniform vec4 uT1b;
${COMMON}
${GEOM_DEFINES}
${PARCHMENT}
#define GW ${GLYPH_W.toFixed(1)}
#define GH ${GLYPH_H.toFixed(1)}
#define GM ${GLYPH_MARGIN.toFixed(1)}
#define NCELLS ${CELLS.toFixed(1)}
// R6-E (T1 line art, tuned at 1080p against src 5.00-5.20, 4.70-5.57 checked): stroke width in 1080p px =
// W0 + W1 sqrt(sc); line light = min(sum, CAP) * K; the column's dark slit: half width XI, from YB below the
// vanishing point; case lines DC, the copies' feet LO; far copies in the hold B0 .. .95 over sc S0 .. S1
#define T1_W0 1.4
#define T1_W1 1.3
#define T1_CAP 1.15
#define T1_K 1.0
#define T1_XI .0088
#define T1_YB .055
#define T1_DC .15
#define T1_LO .3
#define T1_B0 .10
#define T1_S0 .12
#define T1_S1 .7

vec2 glyphUV(vec2 l, float cell){
  float sc = GH / DIGIT_H;
  vec2 g = vec2(l.x * sc + GW * .5, -l.y * sc + GH * .5);
  vec2 c = clamp((g + GM) / vec2(GW + 2. * GM, GH + 2. * GM), .003, .997);
  return vec2((cell + c.x) / NCELLS, c.y);
}

vec3 marble(vec2 s){
  float n = fbm(s * 2.6 + vec2(0., uT * .05));
  float v = abs(fbm(s * 2.0 + n * 1.6 + 3.) - .5);
  // (src 5.40-5.83: a dark teal ground with soft clouds; the veins only faint)
  vec3 c = mix(vec3(.036, .056, .060), vec3(.085, .11, .11), n);
  c += vec3(.10, .12, .11) * smoothstep(.05, .0, v) * .22;
  vec2 r = rot2(.62) * s;
  float scr = smoothstep(.975, 1., vnoise(vec2(r.x * 2.4, r.y * 85.))) * smoothstep(.3, .7, vnoise(r * vec2(4., .6)));
  c += vec3(.75) * scr * .45;
  return c;
}

// outline distance of the instrument seen face on (local units of one copy)
float devOutline(vec2 l, out float dig, out float dcase){
  float fi = clamp(floor(l.x / PITCH + 4.), 0., 7.);
  float xc = (fi - 3.5) * PITCH;
  vec2 lt = l - vec2(xc, 0.);
  // (R4: the R3-07 glass cap, as on the instrument)
  float body = glassSD(lt);
  body = max(body, GLASS_Y0 - lt.y);
  float tip = sdSeg(lt, vec2(0., TUBE_YD + TUBE_HW - .004), vec2(0., TUBE_YD + TUBE_HW + .016), .0075, .0055);
  float d = abs(min(body, tip));
  float dc = abs(sdRoundBox(lt - vec2(0., (SOCK_Y0 + SOCK_Y1) * .5), vec2(SOCK_HW, (SOCK_Y1 - SOCK_Y0) * .5), .006));
  dc = min(dc, abs(sdBox(l - PLINTH_C, PLINTH_H)));
  dc = min(dc, abs(sdBox(l - vec2(0., PLINTH_C.y - PLINTH_H.y - .022), vec2(PLINTH_H.x + .045, .022))));
  dcase = dc;
  int ii = int(fi);
  float cell = uCells[0];
  for (int k = 0; k < 8; k++) if (k == ii) cell = uCells[k];
  vec2 dl = lt - vec2(ii == 1 ? .026 : 0., DIGIT_CY);
  dig = abs(dl.x) < .045 && abs(dl.y) < .09 ? smoothstep(.55, .92, texture(uAtlas, glyphUV(dl, cell)).b) : 0.;
  return d;
}

// R6-A (src 4.45-4.60 at 1080p): the old reading floats mid-corridor as bold cream wire digits -
// the outline of each lit stroke (a double line round the digit body), not the thin support wire
float rowDigit(vec2 l){
  float fi = clamp(floor(l.x / PITCH + 4.), 0., 7.);
  vec2 lt = l - vec2((fi - 3.5) * PITCH, 0.);
  int ii = int(fi);
  float cell = uCells[0];
  for (int k = 0; k < 8; k++) if (k == ii) cell = uCells[k];
  vec2 dl = lt - vec2(ii == 1 ? .026 : 0., DIGIT_CY);
  if (abs(dl.x) >= .045 || abs(dl.y) >= .09) return 0.;
  vec4 a = texture(uAtlas, glyphUV(dl, cell));
  float rim = smoothstep(.05, .22, a.r) * (1. - smoothstep(.62, .92, a.r));
  return max(rim, smoothstep(.55, .92, a.b) * .5);
}

vec4 foliage(vec2 s){
  vec3 c = vec3(.03, .04, .02);
  float a = 0.;
  for (int L = 0; L < 2; L++) {
    // leaf-level grain (R1-06: cells <= 1/40 of the picture height)
    float scl = L == 0 ? 42. : 70.;
    vec2 x = s * scl + vec2(float(L) * 7.3, 0.);
    vec2 n = floor(x), f = fract(x);
    for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
      vec2 g = vec2(float(i), float(j));
      vec2 h = hash22(n + g + float(L) * 17.);
      vec2 r = g + h - f;
      vec2 q = rot2(h.x * 6.283) * r;
      float e = length(q / vec2(.62, .30)) - 1.;
      float lm = 1. - smoothstep(-.12, .04, e);
      if (lm > 0.) {
        float t2 = hash12(n + g + 3.1 + float(L));
        vec3 lc = t2 < .35 ? mix(vec3(.10, .15, .04), vec3(.30, .36, .10), t2 / .35)
                : t2 < .75 ? mix(vec3(.40, .44, .15), vec3(.64, .63, .28), (t2 - .35) / .4)
                : mix(vec3(.36, .27, .13), vec3(.52, .40, .20), (t2 - .75) / .25);
        if (t2 > .95) lc = vec3(.52, .22, .14);
        if (t2 < .03) lc = vec3(.86, .83, .72);
        lc *= .65 + .45 * clamp(-e, 0., 1.);
        lc += vec3(.10, .11, .05) * exp(-pow(q.y / .035, 2.)) * step(abs(q.x), .55);
        c = mix(c, lc, lm);
        a = max(a, lm);
      }
    }
  }
  return vec4(c, a);
}

vec2 voronoi(vec2 x){
  vec2 n = floor(x), f = fract(x);
  float d1 = 8., d2 = 8.;
  for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
    vec2 g = vec2(float(i), float(j));
    vec2 r = g + hash22(n + g) - f;
    float d = dot(r, r);
    if (d < d1) { d2 = d1; d1 = d; } else if (d < d2) d2 = d;
  }
  return vec2(sqrt(d1), sqrt(d2) - sqrt(d1));
}

// nearest cell: exact distance to its border (even crack widths), cell hash, distance to its seed
vec3 cellB(vec2 x){
  vec2 n = floor(x), f = fract(x);
  vec2 mr = vec2(0.), mg = vec2(0.);
  float md = 8.;
  for (int j = -1; j <= 1; j++) for (int i = -1; i <= 1; i++) {
    vec2 g = vec2(float(i), float(j));
    vec2 r = g + hash22(n + g) - f;
    float d = dot(r, r);
    if (d < md) { md = d; mr = r; mg = g; }
  }
  md = 8.;
  for (int j = -2; j <= 2; j++) for (int i = -2; i <= 2; i++) {
    vec2 g = mg + vec2(float(i), float(j));
    vec2 r = g + hash22(n + g) - f;
    if (dot(mr - r, mr - r) > 1e-5) md = min(md, dot(.5 * (mr + r), normalize(r - mr)));
  }
  return vec3(md, hash12(n + mg + 5.7), length(mr));
}

void main(){
  vec2 s = (vUv - .5) * vec2(uAspect, 1.);
  vec3 col = marble(s);
  // mirrored grey mechanical bands at the far sides
  {
    float ax = abs(s.x);
    float band = smoothstep(.66, .74, ax) * (1. - smoothstep(.09, .12, abs(s.y - .01)));
    float mech = fbm(vec2(ax * 26., s.y * 60.)) * (.6 + .4 * step(.5, fract(ax * 18.)));
    col = mix(col, vec3(.30, .31, .30) * (.5 + mech), band * .55 * uLines);
  }
  // wall / ceiling junction lines toward the vanishing point (through the post tops)
  {
    vec2 d = s - uVP;
    float aa = uPx * 1.2;
    float g = 0.;
    for (int k = 0; k < 4; k++) {
      vec2 dir = normalize(vec2(k < 2 ? .58 : -.58, (k == 0 || k == 2) ? .53 : -.53));
      float along = dot(d, dir);
      g = max(g, (1. - smoothstep(0., aa, abs(d.x * dir.y - d.y * dir.x))) * smoothstep(.12, .3, along));
    }
    col += vec3(.45, .43, .38) * g * .28 * (uLines + uSolid) * (1. - uMoss);
  }
  // the far door at the end of the corridor
  {
    vec2 d = s - uVP;
    // src 5.70-5.80: a thin outline doorway (no dark fill), its top and bottom edges brighter,
    // with a narrow paler slit (the farthest door) in its middle
    // (measured: +-.035 x +-.132; its top and bottom are short white lines, no dark fill)
    float fr = sdBox(d, vec2(.035, .132));
    float line = 1. - smoothstep(uPx * .6, uPx * 1.8, abs(fr));
    float edge = exp(-pow((abs(d.y) - .132) / (uPx * 1.6), 2.)) * step(abs(d.x), .035);
    float k0 = (uPillar + uSolid) * (1. - uCrack);
    col += vec3(.30, .30, .28) * line * .4 * k0 + vec3(.70, .68, .62) * edge * .75 * k0;
  }
  // overgrowth: leaves creep in from the right over walls, posts and floor (the corridor stays
  // readable between the leaves), light hatching on floor and ceiling; then pale cracked plates
  if (uMoss > 0.) {
    float front = mix(1.2, -1.3, uMoss);
    float m = smoothstep(-.06, .06, s.x - front + (fbm(s * 4.) - .5) * .5);
    vec4 fo = foliage(s);
    vec2 hs = rot2(.785) * s;
    float hatch = smoothstep(.55, .9, sin(hs.y * 260.) * .5 + .5) * smoothstep(.12, .3, abs(s.y)) * smoothstep(.3, .7, vnoise(s * 6.));
    vec3 mc = fo.rgb * .8 + vec3(.55, .55, .35) * hatch * .35;
    // (src 5.95-6.10: sunlit clumps next to deep shadow, not an even carpet)
    float lit = smoothstep(.36, .72, fbm(s * 3.1 + 7.3));
    mc *= .40 + 1.05 * lit;
    mc += vec3(.20, .22, .06) * smoothstep(.62, .85, fbm(s * 5.5 + 2.9)) * lit;
    float clump = smoothstep(.46, .56, fbm(s * 2.2 + 1.7));
    float wall = smoothstep(.10, .42, max(abs(s.x) * .85, abs(s.y) * 1.45));
    float cov = fo.a * clump * .92 * wall;
#define R5_SUBA .28
#define R5_AS 3.8
#define R5_CA0 .30
    if (uCrack > 0.) {
      // R1-06 (R3, src 6.10-6.20): pale puffy lumps of very different sizes (~50-250 px at
      // 1080p) parted by dark crevices that taper and break off; big lumps stay whole, others
      // split into mid and small lumps (decided per lump, so the crevices stay consistent)
      vec2 w = s + vec2(fbm(s * 2.2), fbm(s * 2.2 + 4.1)) * .22;
      w += vec2(fbm(s * 8. + 1.), fbm(s * 8. + 6.)) * .05;
      w += vec2(vnoise(s * 34. + 2.), vnoise(s * 34. + 9.)) * .010;
      vec3 A = cellB(w * R5_AS);
      vec3 B = cellB(w * 10. + 7.1);
      vec3 C = cellB(w * 22. + 3.3);
      float subA = step(A.y, R5_SUBA);
      float subB = subA * step(fract(B.y * 7.31), .32);
      float sf = uSoft;
      float aaS = uPx * 1.6 + sf * .016;
      float ea = A.x / R5_AS, eb = B.x / 10., ec = C.x / 22.;
      // (R4: thinner crevices - src 6.10 at 640: ~1/13 of the patch size)
      float wa = mix(.0010, .0036, vnoise(w * 7. + 1.)) * (1. + sf);
      float wb = mix(.0008, .0024, vnoise(w * 13. + 4.)) * (1. + sf);
      float wc = mix(.0005, .0014, vnoise(w * 25. + 6.)) * (1. + sf);
      // rounded lumps: each cell is cut by a wobbly disc round its seed, so the crevices widen
      // into dark gaps where several lumps meet (like twigs) instead of straight polygon edges
      float ra = (A.z - mix(.70, .98, vnoise(w * 6. + 2.))) / R5_AS;
      float rb = (B.z - mix(.66, .94, vnoise(w * 12. + 7.))) / 10.;
      float rc = (C.z - mix(.62, .90, vnoise(w * 26. + 1.))) / 22.;
      ea = min(ea, -ra);
      eb = min(eb, -rb);
      ec = min(ec, -rc);
      // R4-03 (R5, src 6.10 at 640): where a crevice breaks off, the lumps on both sides are one
      // surface - no puffy darkening or mossy rim there either - so the pale patches join up into
      // big pieces (src: area-weighted median diameter 106 px, R4 42)
      float va = smoothstep(R5_CA0, R5_CA0 + .20, vnoise(w * 5.5 + 3.));
      float vb = smoothstep(.22, .42, vnoise(w * 9. + 5.));
      float ca = (1. - smoothstep(wa, wa + aaS, ea)) * va;
      float cb = (1. - smoothstep(wb, wb + aaS, eb)) * vb * subA;
      float cc = (1. - smoothstep(wc, wc + aaS, ec)) * smoothstep(.28, .48, vnoise(w * 17. + 8.)) * subB;
      float crack = max(ca, max(cb * .92, cc * .78)) * (1. - .6 * sf);
      // puffy: bright in the middle of each lump, darker toward its crevices
      float pa = mix(1., smoothstep(.0, .028, ea), va);
      float pb = mix(1., smoothstep(.0, .016, eb), subA * vb);
      float pc2 = mix(1., smoothstep(.0, .009, ec), subB);
      float puff = .70 + .48 * pa * pb * pc2;
      float id = subB > .5 ? C.y : subA > .5 ? B.y : A.y;
      // (src 6.117/6.167 lumps: cream, pale olive, yellow-green, a few pinkish; muted)
      vec3 pale = id < .36 ? vec3(.84, .81, .67) : id < .62 ? vec3(.76, .74, .54)
                : id < .84 ? vec3(.70, .72, .51) : id < .93 ? vec3(.81, .73, .66) : vec3(.58, .58, .42);
      pale *= 1.0 + .18 * hash12(vec2(id * 91.7, 3.1));
      vec3 plate = pale * puff;
      // cauliflower texture inside the lumps (folds and darker olive patches), dark specks, a
      // mossy rim at the crevices
      plate *= .62 + .70 * fbm(w * 34. + 2.);
      plate *= .86 + .28 * fbm(rot2(.7) * w * 95. + 4.);
      plate = mix(plate, plate * vec3(.62, .66, .46), smoothstep(.56, .74, fbm(w * 21. + 6.2)) * .55);
      plate *= 1. - .25 * smoothstep(.62, .80, fbm(rot2(1.9) * s * 120. + 2.));
      // R1-06 (R4, src 6.10-6.20 at 640): moss grain inside the lumps - small round bumps with
      // dark gaps and olive / cream flecks, so the pale patches read as moss, not smooth stone
      {
        vec2 wq = w + vec2(vnoise(w * 40. + 3.), vnoise(w * 40. + 8.)) * .006;
        vec2 g1 = voronoi(wq * 105. + 3.7);
        vec2 g2 = voronoi(rot2(.9) * wq * 58. + 1.3);
        float b1 = 1. - smoothstep(.18, .66, g1.x);
        float b2 = 1. - smoothstep(.22, .70, g2.x);
        float bmp = max(b1 * .9, b2 * .75);
        // (contrast varies over the lump: clumps of tight moss next to flatter patches)
        float gk = smoothstep(.25, .75, vnoise(w * 9. + 5.5));
        plate *= mix(.50 + .70 * bmp, .35 + .97 * bmp, gk);
        float fl = hash12(floor(rot2(.9) * w * 58. + 1.3) + .37);
        plate = mix(plate, plate * vec3(.62, .74, .40), step(.70, fl) * .55 * b2);
        plate = mix(plate, plate * vec3(1.12, 1.08, .92), step(fl, .12) * .6 * b2);
      }
      float rim = (1. - smoothstep(wa, wa * 2.6 + aaS, ea)) * (1. - ca) * va;
      plate = mix(plate, vec3(.30, .35, .12), rim * .45);
      plate = mix(plate, vec3(.10, .095, .05), crack);
      // R1-08 (src 6.20-6.30): the lumps blur into a grainy mottled mass of pale and dark
      // blotches with fine speckle everywhere
      if (sf > 0.) {
        float mo = fbm(s * vec2(5., 6.) + vec2(fbm(s * 2.) * 1.5, 0.) + 4.4);
        vec3 mot = mix(vec3(.17, .13, .08), vec3(.86, .78, .70), smoothstep(.40, .70, mo));
        mot = mix(mot, vec3(.40, .50, .24), smoothstep(.55, .75, fbm(s * 9. + 1.3)) * .45);
        // flecks in clusters (bits of foliage), not an even static
        float spk = hash12(floor(gl_FragCoord.xy * .45) + 1.7);
        float camp = .25 + .75 * smoothstep(.40, .70, vnoise(s * 26. + 4.));
        mot *= 1. + (spk - .5) * 1.1 * camp;
        plate = mix(plate * (.85 + .3 * mo), mot, sf * .7);
      }
      float edge = smoothstep(.55, .85, abs(s.x));
      plate = mix(plate, vec3(.80, .70, .66), edge * .4);
      float ck = smoothstep(0., 1., uCrack);
      mc = mix(mc, plate, ck);
      // the lumps fill the floor and ceiling; the corridor still shows in the middle band
      float bandY = smoothstep(.08, .24, abs(s.y) + (fbm(s * 3. + 2.) - .5) * .14);
      cov = mix(cov, mix(.55, .97, bandY) * mix(.85, 1., wall), ck);
    }
    col = mix(col, mc, m * cov);
  }
  // tan door posts on both walls at many depths: flat boards with a darker inner side,
  // thin lintel lines; no solid lintels (the ceiling is out of frame)
  for (int k = 0; k < 18; k++) {
    // frames evenly spaced in depth (src 5.70-5.80: near posts +-.63, then +-.32, +-.21, ...)
    float sc = 1.09 / (float(k) + 1. - fract(uPTravel));
    vec2 l = (s - uVP) / sc;
    float aa = uPx / sc * 1.3;
    float ax = abs(l.x);
    float ay = abs(l.y);
    float vert = 1. - smoothstep(.53 - aa, .53 + aa, ay);
    float post = smoothstep(.56 - aa, .56 + aa, ax) * (1. - smoothstep(.60 - aa, .60 + aa, ax)) * vert;
    // a second, thinner board just inside each post (the frames read as clusters of boards)
    post = max(post, smoothstep(.495 - aa, .495 + aa, ax) * (1. - smoothstep(.515 - aa, .515 + aa, ax)) * vert * .85);
    float side = smoothstep(.52 - aa, .52 + aa, ax) * (1. - smoothstep(.56 - aa, .56 + aa, ax)) * vert * smoothstep(.12, .35, sc);
    float lint = (1. - smoothstep(aa * .6, aa * 1.6, abs(ay - .53))) * step(.35, ax) * step(ax, .88) * smoothstep(.26, .34, sc);
    float fade = smoothstep(.035, .09, sc) * (1. - smoothstep(1.15, 1.45, sc));
    if (post + side + lint > 0.) {
      float n = fbm(vec2(l.x * 40., l.y * 3.) + float(k) * 3.1);
      vec3 pc = vec3(.62, .51, .45) * (.80 + .32 * n) * (.92 + .08 * l.y);
      pc = mix(pc, pc * .6, smoothstep(.565, .575, ax) * (1. - smoothstep(.585, .595, ax)) * .5);
      vec3 sd = vec3(.30, .25, .23) * (.8 + .3 * n);
      float a = fade * min(uPillar + uSolid * .4, 1.) * (1. - .35 * uSoft);
      pc = mix(pc, pc * vec3(.82, .92, .62), uMoss * .45);
      col = mix(col, sd, side * a);
      col = mix(col, pc, post * a);
      col += vec3(.40, .38, .34) * lint * a * .5;
    }
    // R1-05 (R3, src 5.57-5.83 at 1080p): machinery bolted to the outside of each door frame at
    // mid height, mirrored up/down and left/right - a dark block against the post, a light bar
    // with a small gear, two angled slabs (the nearest frame's arms reach in from the frame
    // edges at x ~ +-.65-.89, the next frame's sit between the posts at x ~ +-.36-.46)
    if (uSolid > 0. && ax > .59 && ax < .915 && ay < .12) {
      vec2 m = vec2(ax, ay);
      float am = fade * uSolid * (1. - .35 * uSoft) * smoothstep(.22, .40, sc) * (1. - uCrack);
      float blk = sdBox(m - vec2(.65, 0.), vec2(.042, .068));
      float bar = sdBox(m - vec2(.75, 0.), vec2(.12, .010));
      // slab: a thin quad rising from (.61, .045) to (.86, .088)
      vec2 sa = vec2(.61, .045), sb = vec2(.86, .088);
      vec2 sv = sb - sa;
      float hsl = clamp(dot(m - sa, sv) / dot(sv, sv), 0., 1.);
      float slab = length(m - sa - sv * hsl) - .013;
      float gr = length(m - vec2(.72, 0.));
      float gan = atan(m.y, m.x - .72);
      float gear = gr - (.026 + .006 * step(.5, fract(gan * 8. / 6.2832 + uPTravel * .3)));
      float hole = gr - .009;
      float ea = aa * 1.2;
      float fBlk = 1. - smoothstep(-ea, ea, blk);
      float fBar = 1. - smoothstep(-ea, ea, bar);
      float fSlab = 1. - smoothstep(-ea, ea, slab);
      float fGear = (1. - smoothstep(-ea, ea, gear)) * smoothstep(-ea, ea, hole);
      vec3 lite = vec3(.60, .56, .54) * (.85 + .25 * fbm(vec2(m.x * 30., m.y * 60.) + float(k)));
      vec3 dark = vec3(.045, .045, .052);
      col = mix(col, dark, fBlk * am);
      // a light rim on the block's outer edge
      col = mix(col, lite * .8, (1. - smoothstep(0., ea * 2., abs(blk))) * am * .7);
      col = mix(col, lite, fBar * am);
      // (src: the slabs read darker than the bar - grey metal with a light top edge)
      col = mix(col, lite * .40, fSlab * am);
      col = mix(col, lite, (1. - smoothstep(0., ea * 2., abs(slab))) * am * .6);
      col = mix(col, lite * 1.05, fGear * am);
      // R3-03 (R4, src 5.70 at 1080p): the machinery is busier than one bar and two slabs - a
      // zigzag truss between the bar and each slab, a rib along each slab, bolts on the bar and
      // a dark end block with a light rim at the outer end
      float tr = 8.;
      for (int q = 0; q < 6; q++) {
        float x0 = .645 + .036 * float(q), x1 = x0 + .036;
        float y0 = q % 2 == 0 ? .014 : .045 + .172 * (x0 - .61) - .014;
        float y1 = q % 2 == 0 ? .045 + .172 * (x1 - .61) - .014 : .014;
        tr = min(tr, sdSeg(m, vec2(x0, y0), vec2(x1, y1), .0022, .0022));
      }
      float fTr = 1. - smoothstep(-ea, ea, tr);
      col = mix(col, lite * .72, fTr * am);
      vec2 sn = normalize(vec2(-sv.y, sv.x));
      float rib = abs(dot(m - sa, sn)) - .0018;
      float fRib = (1. - smoothstep(-ea, ea, rib)) * step(.04, hsl) * step(hsl, .96);
      col = mix(col, lite * .95, fRib * am * .8);
      float bx = fract((m.x - .655) / .03) - .5;
      float bolt = length(vec2(bx * .03, m.y)) - .0042;
      float fBolt = (1. - smoothstep(-ea, ea, bolt)) * step(.64, m.x) * step(m.x, .865) * (1. - step(gr, .036));
      col = mix(col, dark * 2., fBolt * am);
      float eb = sdBox(m - vec2(.888, .030), vec2(.020, .030));
      col = mix(col, dark, (1. - smoothstep(-ea, ea, eb)) * am);
      col = mix(col, lite * .85, (1. - smoothstep(0., ea * 2., abs(eb))) * am * .8);
      float ebi = sdBox(m - vec2(.888, .030), vec2(.009, .012));
      col = mix(col, lite * .7, (1. - smoothstep(0., ea * 2., abs(ebi))) * am * .6);
    }
  }
  // R1-05 (src 5.58-5.83): the receding wall panels near the vanishing point merge into a pair
  // of light tan triangles pointing at it (the "butterfly"); vertical panel joints
  {
    vec2 dv = s - uVP;
    float X = abs(dv.x), Y = abs(dv.y);
    float tri = (1. - smoothstep(.60 * X - uPx, .60 * X + uPx, Y)) * smoothstep(.012, .03, X) * (1. - smoothstep(.17, .215, X));
    if (tri > 0.) {
      float dep = .11 / max(X, 1e-3) - fract(uPTravel) * 1.;
      // each panel: a light front face, a darker side towards the vanishing point, a dark gap
      float fp = fract(dep * 1.5);
      float gapK = mix(.06, .42, smoothstep(.05, .16, X));
      float pw = max(fwidth(dep * 1.5), 1e-4);
      float gap = smoothstep(gapK - pw, gapK + pw, fp) * (1. - smoothstep(1. - pw * 1.5, 1., fp));
      vec3 pc = vec3(.68, .56, .50) * (.88 + .12 * fbm(vec2(dep * 9., dv.y * 30.)));
      pc = mix(pc, pc * .62, smoothstep(gapK + .25, gapK + .3, fp));
      pc = mix(pc * .25, pc, gap);
      pc *= .78 + .22 * smoothstep(.30, .05, X);
      float a = min(uPillar + uSolid * .4, 1.) * (1. - .35 * uSoft);
      col = mix(col, pc, tri * a * .92);
    }
  }
  // nested line-art copies of the instrument
  if (uLines > 0. || uRow > 0.) {
    float L = 0.;
    float Dg = 0.;
    float maxS = exp2(mix(-4.5, 3.4, uSpread));
    float minS = exp2(mix(-6., 3.5, uFadeIn));
    // R6-A (src 4.53-4.65): the digits of the row trail back to the vanishing point before the
    // tube outlines of the copies come out
    float maxD = uT1.w > 0. ? exp2(uT1.y) : 0.;
    // R6-E (src 4.70-5.57 at 1080p): the bright column is the central walls of the copies (the
    // inner sides of the two middle tubes, x = +-.0112 sc) - two white bars out to the largest
    // copy's walls (+-24 px at 1080p); they stay while the centre empties
    float colK = uT1.w > 0. ? 1. - smoothstep(.0235, .0265, abs(s.x - uVP.x)) : 0.;
    // (R6-E: with the copies out to 2 (from 1.05) the far ones dim more - the R6-A curve while the
    // copies come out in the rush)
    float nearS = sqrt(min(maxS * .5, 1.));
    for (int k = 0; k < 34; k++) {
      float z = fract((float(k) - uTravel) / 34.);
      float sc = exp2(1.2 - z * 6.);
      if (uSparse > .5 && uOnly == 0. && (k - 2 * (k / 2)) == 1) continue;
      if (uOnly > 0.) {
        if (abs(log2(sc / uOnly)) > .089) continue;
      } else if (sc > max(maxS, maxD) * 1.2 || sc < minS * .8) continue;
      vec2 l = (s - uVP) / sc;
      if (abs(l.x) > .9 || l.y > .33 || l.y < -.4) continue;
      float dig, dcs;
      float d = devOutline(l, dig, dcs);
      float ln;
      if (uT1.w > 0.) {
        // R6-E (src 4.85-5.40 at 1080p: lines ~3 px, near white): the stroke is set in 1080p
        // pixels, so the picture is the same at any render size (#14 for T1) - one render pixel
        // of AA; a line thinner than a pixel keeps its ink and gets fainter
        float wq = (T1_W0 + T1_W1 * sqrt(sc)) / (uPx * 1080.);
        float hp = .5 * max(wq, 1.), gk = min(wq, 1.), ps = sc / uPx;
        ln = gk * (1. - smoothstep(hp - .5, hp + .5, d * ps));
        // (src 5.00-5.20: the copies' feet are thin and dim next to the walls and domes)
        ln *= mix(T1_LO, 1., smoothstep(GLASS_Y0 - .004, GLASS_Y0 + .008, l.y));
        ln = max(ln, gk * (1. - smoothstep(hp - .5, hp + .5, dcs * ps)) * T1_DC);
      } else {
        float hw = uPx * (.35 + .22 * sqrt(sc)) / sc;
        float aw = uPx * 1.2 / sc;
        ln = 1. - smoothstep(hw, hw + aw, d);
        ln = max(ln, (1. - smoothstep(hw, hw + aw, dcs)) * .45);
      }
      float fade = smoothstep(minS * .8, minS * 1.3, sc) * (1. - smoothstep(maxS * .75, maxS * 1.2, sc)) * (1. - smoothstep(1.55, 2.1, sc));
      // R6-A (18, src 5.45-5.55): in the fade the copies that are left keep full strength (each
      // goes in a frame or two) and the last layers are the near ones round 1.75
      if (uT1b.z > 0.) {
        fade = smoothstep(minS * mix(.8, .93, uT1b.z), minS * mix(1.3, 1.07, uT1b.z), sc) * (1. - smoothstep(maxS * .75, maxS * 1.2, sc))
          * (1. - smoothstep(mix(1.55, 1.85, uT1b.z), mix(2.1, 2.35, uT1b.z), sc));
      }
      if (uOnly > 0.) fade = 1.;
      float br = mix(.22, .8, smoothstep(.04, .6, sc));
      // (src 5.50-5.55: the small inner copies stay dim, the centre shows the corridor)
      if (uSparse > .5) br = mix(.10, .8, smoothstep(.35, 1.1, sc));
      float fadeD = fade;
      if (uT1.w > 0.) {
        // R6-A (#1, src 4.85-5.30): the far copies are fine grey lines - the centre is busy, not a
        // white glare; the trailing copies of the old reading keep their bold digits (R6-E: with the
        // brighter lines the far copies of the hold dim more)
        br = mix(br, mix(mix(.18, T1_B0, nearS), .95, smoothstep(mix(.05, T1_S0, nearS), mix(.5, T1_S1, nearS), sc)), uT1.w);
        dig = mix(dig, rowDigit(l), uT1.w);
        fadeD = max(fade, smoothstep(minS * .8, minS * 1.3, sc) * (1. - smoothstep(maxD * .75, maxD * 1.2, sc)));
        if (sc > maxS * 1.2) ln = 0.;
      }
      // R6-A (18, src 5.33-5.38): the lines next to the vanishing point leave first - the centre
      // is empty under the arches but for the bright column; then the copies go by size
      if (uT1b.x > 0.) {
        float tm = smoothstep(uT1b.x - uT1b.y, uT1b.x + uT1b.y, length(s - uVP) / .5);
        ln *= max(tm, colK);
        dig *= tm;
      }
      L += ln * fade * br;
      // (src 4.85-5.40: the digits show on the copies left and right of the centre, the middle is a
      // bright column with the fans stopping short of it)
      float dmid = mix(1., smoothstep(.06, .2, abs(s.x - uVP.x)), uT1.w);
      Dg += dig * fadeD * mix(br, smoothstep(.2, .55, sc), uT1.w) * (1. - smoothstep(.9, 1.6, sc)) * (1. - uT1.z) * dmid;
    }
    // the old reading as a single row of wire digits floating mid-corridor (first frames)
    if (uRow > 0.) {
      float scR = uT1.x;
      vec2 l = (s - uVP) / scR;
      float dig, dcs;
      float d = devOutline(l, dig, dcs);
      if (uT1.w > 0.) dig = mix(dig, rowDigit(l) * 2., uT1.w);
      Dg += dig * uRow * 1.3;
    }
    // bright spine above the vanishing point where the inner tube edges converge
    float sp = exp(-abs(s.x - uVP.x) / (uPx * 1.6)) * smoothstep(uVP.y, uVP.y + .04, s.y) * (1. - smoothstep(.3, .52, s.y));
    float Lc = min(L, 1.5);
    float Dc = min(Dg, 1.4);
    float kL = .55;
    float spk = .4;
    if (uT1.w > 0.) {
      // R6-E (src 4.70-5.57): a dark slit between the two bars of the column, down to the floor
      // (inside the walls of the ~.8 copy); the column replaces the thin spine
      vec2 dv = s - uVP;
      L *= 1. - (1. - smoothstep(T1_XI - uPx, T1_XI + uPx, abs(dv.x))) * smoothstep(-T1_YB - uPx * 3., -T1_YB + uPx * 3., dv.y);
      Lc = min(L, T1_CAP);
      Dc = min(Dg, 1.);
      kL = T1_K;
      spk = 0.;
    }
    vec3 lc = vec3(1., .95, .86);
    col += lc * ((Lc * kL + sp * spk) * uLines + Dc * .55 * max(uLines, uRow));
  }
  // dissolve into the parchment (mottled)
  if (uDiss > 0.) {
    // R1-08 (src 6.37-6.45): the paper comes through a fine speckle, from the edges inward
    float n = clamp(.15 + .7 * (fbm(s * 4.5 + 2.) * .55 + hash12(floor(gl_FragCoord.xy * .8) + 3.3) * .45) - .2 * smoothstep(.2, .45, abs(s.y)), 0., 1.);
    float m = smoothstep(n - .03, n + .03, uDiss * 1.06);
    col = mix(col, parchment(s, uT, .75, 1.), m);
  }
  // R1-07: the nearest door posts stay in front as dark boards while the moss cracks and the
  // picture dissolves into paper (src 6.10-6.34), continuing the T2 frames' positions
  if (uFrontPosts.x > 0.) {
    for (int k = 0; k < 2; k++) {
      // (src 6.10-6.34: they hang at ~+-.6/.8 and drift slowly outward, full height)
      float sc = uFrontPosts.y * (k == 0 ? 1. : .55);
      vec2 l = (s - uVP) / sc;
      float aa = uPx / sc * 1.3;
      float ax = abs(l.x);
      float post = smoothstep(.56 - aa, .56 + aa, ax) * (1. - smoothstep(.60 - aa, .60 + aa, ax));
      post = max(post, smoothstep(.495 - aa, .495 + aa, ax) * (1. - smoothstep(.515 - aa, .515 + aa, ax)) * .85);
      float vert = k == 0 ? 1. : 1. - smoothstep(.53 - aa, .53 + aa, abs(l.y));
      vec3 pc = vec3(.21, .18, .16) * (.85 + .3 * fbm(vec2(l.x * 40., l.y * 3.) + float(k) * 3.1));
      col = mix(col, pc, post * vert * uFrontPosts.x * (k == 0 ? .88 : .6));
    }
  }
  // standby -> corridor: the new image arrives through vertical streaks
  if (uReveal < 1.) {
    float vs = vnoise(vec2(s.x * 55., s.y * 1.2 + uT * 3.)) * .7 + vnoise(vec2(s.x * 160., 0.)) * .3;
    float m = smoothstep(vs - .08, vs + .08, uReveal * 1.25 - .1);
    col = mix(vec3(.018, .016, .016), col, m);
  }
  o = vec4(col, 1.);
}`;

// T7: warm sepia nebula, black fragments flying outward, a planet with a lit rim at the right,
// a black moon at the bottom, a black oval top left. The door image slides apart from a centre slit.
export const SPACE_FS = /* glsl */ `#version 300 es
precision highp float;
#define FRAG_N 124
#define FRAG_K 3.4
#define FRAG_FAR 2.4
in vec2 vUv;
out vec4 o;
uniform float uAspect;
uniform float uPx;
uniform float uT;
uniform float uReveal;
uniform vec2 uVP;
uniform float uRoll;
uniform float uSweepH;
uniform vec4 uBurst[28];
uniform int uBurstN;
${COMMON}

vec3 nebula(vec2 s){
  vec2 q = vec2(fbm(s * 1.7 + uT * .15), fbm(s * 1.7 + 4.));
  float n = fbm(s * 2.4 + q * 1.6);
  float n2 = fbm(s * 6. + q * 2.);
  vec3 c = mix(vec3(.50, .38, .32), vec3(.93, .87, .79), smoothstep(.28, .78, n));
  c = mix(c, c * .8, smoothstep(.5, .8, n2) * .5);
  // bright cloud bank behind the tubes
  c = mix(c, vec3(1., .97, .90), exp(-dot(s * vec2(.85, 1.9), s * vec2(.85, 1.9)) * 3.4) * .75);
  // warm dark rim
  float r = length(s * vec2(.74, 1.));
  c = mix(c, c * vec3(.80, .66, .60), smoothstep(.35, .95, r));
  c *= 1. - smoothstep(.75, 1.25, r) * .45;
  // sparse stars
  vec2 g = floor(s * 260.);
  float st = step(.9965, hash12(g)) * (.5 + .5 * hash12(g + 7.));
  c += vec3(1., .96, .9) * st * .55 * smoothstep(.1, .5, r);
  return c;
}

void main(){
  vec2 s0 = (vUv - .5) * vec2(uAspect, 1.);
  vec2 s = uVP + rot2(uRoll) * (s0 - uVP);
  vec3 c = nebula(s);
  if (uSweepH > 0.) {
    // R1-13 (src 8.434-8.45): the nebula drawn out into long horizontal streaks, strongest in
    // the lower half; ragged row ends at the left
    float row = floor(s0.y * 140.);
    float rl = hash11(row * 1.37);
    vec2 sq = vec2(s0.x * .06 + rl * .4, s0.y);
    vec3 st = nebula(sq) * (.92 + .16 * vnoise(vec2(s0.x * 3., s0.y * 160.)));
    st = mix(st, vec3(.88, .82, .74), .35);
    float low = smoothstep(.08, -.10, s0.y);
    float ragged = smoothstep(-.70 + .35 * rl, -.55 + .35 * rl, s0.x) * smoothstep(-.02, .12, s0.y) * (1. - low);
    c = mix(c, st, uSweepH * max(low, ragged * .9));
  }
  // planet, lit from the left: thin bright limb, storms along the terminator; it slides in from
  // the right and leaves toward the top right (fit to the original's limb, src 8.07-8.37)
  {
    float u = max(uT - .17, 0.);
    vec2 pc = vec2(1.10 + .5 * u + 3. * u * u, .27 + .4 * u + 1.5 * u * u);  // limb re-fit on src 8.10-8.30
    float R = .63;
    vec2 dv = s - pc;
    float d = length(dv);
    vec2 n = dv / R;
    float body = 1. - smoothstep(R - uPx, R + uPx, d);
    if (body > 0. || d < R + .08) {
      float nz = sqrt(max(1. - dot(n, n), 0.));
      vec3 N = vec3(n, nz);
      // R1-12 (src 8.20-8.30): mostly the night side, lit from behind-left - a thin bright
      // crescent; cloud bands and storms readable in the lit part, faint relief on the dark side
      float lit = clamp(dot(N, normalize(vec3(-.95, .22, -.12))), 0., 1.);
      float term = smoothstep(.0, .30, lit);
      vec2 sp = vec2(asin(clamp(n.x, -1., 1.)), asin(clamp(n.y, -1., 1.))) * 2.6 + vec2(uT * .05, 0.);
      float storms = fbm(sp * 2.2 + fbm(sp * 1.3) * 1.5);
      float bands = fbm(vec2(sp.x * .8, sp.y * 5.) + storms);
      vec3 pcol = vec3(.030, .028, .027) + vec3(.07, .065, .06) * storms * (1. - term);
      pcol += vec3(.62, .56, .48) * term * (.30 + .85 * storms * bands) * .75;
      pcol += vec3(.95, .88, .76) * pow(lit, 3.) * .45;
      float rim = smoothstep(R - .02, R - .002, d) * smoothstep(.0, .3, lit + .1);
      pcol += vec3(1., .93, .80) * rim * .9;
      c = mix(c, pcol, body * (1. - uSweepH));
      // atmosphere glow outside the lit limb
      float halo = exp(-max(d - R, 0.) / .014) * (1. - body) * smoothstep(-.2, .6, -n.x);
      c += vec3(.95, .82, .62) * halo * .55;
    }
  }
  // the dark moon below the tubes (thin lit rim on top) and the dark oval at the top left
  {
    vec2 mc = vec2(-.095 - .15 * uT, -.29 - .19 * uT);
    float mr = .15 + .06 * uT;
    float d = length(s - mc) - mr;
    float m = 1. - smoothstep(-uPx, uPx, d);
    vec3 mcol = vec3(.02) + vec3(.40, .34, .28) * exp(-pow((d + .004) / .003, 2.)) * smoothstep(-.2, .8, (s.y - mc.y) / mr);
    c = mix(c, mcol, m);
    vec2 oc = rot2(.3) * (s - vec2(-.33 - .42 * uT, .24 + .29 * uT));
    float od = length(oc / vec2(.065 + .085 * uT, .055 + .07 * uT)) - 1.;
    c = mix(c, vec3(.02), 1. - smoothstep(-.02, .02, od));
  }
  // black fragments: rocks at depth flying out past the camera
  // R6-B (U 17, src 8.01-8.42 at 60 fps): from 4.11 (uT .08) the rocks come at the camera FRAG_K
  // times faster and there are more of them (dark pieces at 480: src 48 / 61 / 58 / 46 at 4.15 /
  // 4.22 / 4.30 / 4.37, R5 35 / 36 / 31 / 27, R6 41 / 44 / 66 / 59). Each of R5's rocks that passes
  // the camera comes back in the far field (tiny); the extra ones join over 4.11-4.23 and are all
  // gone by 4.45. Before 4.11 the field is R5's
  float tk = max(uT - .08, 0.);
  for (int i = 0; i < FRAG_N; i++) {
    float fi = float(i);
    float h1 = hash11(fi * 1.37 + .2), h2 = hash11(fi * 2.71 + 1.3), h3 = hash11(fi * 3.17 + 2.9), h4 = hash11(fi * 5.3 + 4.1);
    float ang = h1 * 6.2832;
    float v = mix(.5, .9, h4);
    float z, grow = 1.;
    if (i < 60) {
      // (R5's rocks: all are still in front of the camera at 4.11 - z >= .28)
      z = mix(.35, 2.2, h3) - v * min(uT, .08) - v * FRAG_K * tk;
      if (z < .12) z += FRAG_FAR;
    } else {
      // the extra rocks join one by one over 4.11-4.23, each growing in over two frames
      float tb = .08 + .12 * hash11(fi * 7.7 + 5.3);
      if (uT <= tb) continue;
      // (one pass each, all gone by 4.45: src 8.35-8.42, the field thins out before the gears -
      // the far-born ones are the faster ones)
      float zb = mix(.5, 1.8, h3);
      float vx = max(v, (zb - .12) / (FRAG_K * (.42 - tb)));
      z = zb - vx * FRAG_K * (uT - tb);
      grow = smoothstep(0., .03, uT - tb);
    }
    if (z < .12 || z > FRAG_FAR) continue;
    vec2 pos = uVP + vec2(cos(ang), sin(ang) * .62) * mix(.06, .55, h2) / z;
    float rad = mix(.006, .028, h4 * h4) / z * grow;
    vec2 d = s - pos;
    if (dot(d, d) > rad * rad * 3.) continue;
    float a = atan(d.y, d.x) + uT * (h1 - .5) * 4.;
    float rr = rad * (1. + .32 * (vnoise(vec2(a * 1.6 + fi, fi)) - .5) + .18 * (vnoise(vec2(a * 4.5, fi * 3.)) - .5));
    float e = length(d) - rr;
    float m = 1. - smoothstep(-uPx * 1.2, uPx * 1.2, e);
    vec3 fc = vec3(.025, .022, .02) + vec3(.30, .25, .2) * exp(-pow((e + .002) / .002, 2.)) * clamp(dot(normalize(d), normalize(uVP - pos)), 0., 1.) * .5;
    c = mix(c, fc, m * (1. - uSweepH));
  }
  // R3-02 (R4, 60 fps src 7.934-8.017): on the cut frame the slit is full of black ink - a tall
  // stream over tubes 3-4 and a spray of dots - flying out of a point above the middle tubes:
  // the big blobs grow x1.44 per frame (k 21/s), the far dots x1.09 (k 5/s)
  if (uBurstN > 0 && uT < .079) {
    vec2 bc = vec2(0., .2037);
    float bt = max(uT - .004, 0.);
    float bfade = step(bt, .075);
    for (int i = 0; i < 28; i++) {
      if (i >= uBurstN) break;
      vec4 b = uBurst[i];
      float e = exp(min(b.w * bt, 2.3));
      vec2 pos = bc + (b.xy - bc) * e;
      float rad = b.z * e;
      vec2 d = s0 - pos;
      if (dot(d, d) > rad * rad * 2.5) continue;
      float a = atan(d.y, d.x);
      float rr = rad * (1. + .22 * (vnoise(vec2(a * 1.7 + float(i) * 3.1, float(i))) - .5));
      float m = 1. - smoothstep(-uPx * 1.2, uPx * 1.2, length(d) - rr);
      c = mix(c, vec3(.03, .026, .024), m * bfade);
    }
  }
  // the door corridor parts from a centre slit (the frames behind are drawn by the parchment
  // pass; this layer is blended over it)
  float mask = 1.;
  if (uReveal < 1.) {
    float g = uReveal * 1.0;
    mask = 1. - smoothstep(g * .95 - .01, g * .95 + .01, abs(s.x));
  }
  o = vec4(c * mask, mask);
}`;

// T9/T10: dark field. Phase 0: grainy planet ground, a fiery light band sweeping round an arch
// over the middle tubes and flooding to white. Phase 1: helix strands, orange smoke, light ribbons.
export const DARK_FS = /* glsl */ `#version 300 es
precision highp float;
#define LAD_GAIN 1.4
#define LAD_BGF 1.
#define LAD_ST 2.
#define LAD_W .9
in vec2 vUv;
out vec4 o;
uniform float uAspect;
uniform float uPx;
uniform float uT;
uniform float uPhase;
uniform float uRot;
uniform float uSweep;
uniform float uWidth;
uniform vec2 uArchC;
uniform vec2 uArchR;
uniform float uWhite;
uniform float uWhite2;
uniform float uGround;
uniform float uHelix;
uniform float uRib;
uniform float uWisp;
uniform float uHeadL;
uniform float uHeadU;
uniform float uHole;
uniform float uTunnel;
uniform float uFloor;
uniform float uFront;
uniform float uLayer;
${COMMON}

// T9 light band (src 9.03-9.29): a long fibrous diagonal from the lower left corner to the upper
// right, passing just under the middle tubes, and a loop rising off it over those tubes (an arch
// whose hole has the slanted diagonal as its floor). Eye-relative units, half-widths in .z
const vec3 DIAG[9] = vec3[9](
  vec3(-1.020, -0.420, 0.120),
  vec3(-0.700, -0.300, 0.100),
  vec3(-0.450, -0.200, 0.080),
  vec3(-0.220, -0.110, 0.062),
  vec3(0.020, -0.030, 0.052),
  vec3(0.250, 0.045, 0.056),
  vec3(0.480, 0.130, 0.070),
  vec3(0.720, 0.220, 0.085),
  vec3(1.050, 0.340, 0.100));
const vec3 ARCH[10] = vec3[10](
  vec3(-0.200, -0.100, 0.034),
  vec3(-0.215, -0.010, 0.030),
  vec3(-0.200, 0.090, 0.026),
  vec3(-0.150, 0.170, 0.022),
  vec3(-0.070, 0.215, 0.020),
  vec3(0.020, 0.228, 0.020),
  vec3(0.100, 0.210, 0.021),
  vec3(0.165, 0.165, 0.023),
  vec3(0.210, 0.105, 0.025),
  vec3(0.230, 0.050, 0.028));

vec3 polyDiag(vec2 p){
  float best = 1e9, bu = 0., bw = .05, bv = 0., acc = 0.;
  for (int i = 0; i < 8; i++) {
    vec2 a = DIAG[i].xy, b = DIAG[i + 1].xy, ab = b - a;
    float L = length(ab);
    float h = clamp(dot(p - a, ab) / (L * L), 0., 1.);
    vec2 d = p - (a + ab * h);
    float dd = dot(d, d);
    if (dd < best) { best = dd; bu = acc + h * L; bw = mix(DIAG[i].z, DIAG[i + 1].z, h); bv = sign(ab.x * d.y - ab.y * d.x) * sqrt(dd); }
    acc += L;
  }
  return vec3(bv, bu, bw);
}
vec3 polyArch(vec2 p){
  float best = 1e9, bu = 0., bw = .05, bv = 0., acc = 0.;
  for (int i = 0; i < 9; i++) {
    vec2 a = ARCH[i].xy, b = ARCH[i + 1].xy, ab = b - a;
    float L = length(ab);
    float h = clamp(dot(p - a, ab) / (L * L), 0., 1.);
    vec2 d = p - (a + ab * h);
    float dd = dot(d, d);
    if (dd < best) { best = dd; bu = acc + h * L; bw = mix(ARCH[i].z, ARCH[i + 1].z, h); bv = sign(ab.x * d.y - ab.y * d.x) * sqrt(dd); }
    acc += L;
  }
  return vec3(bv, bu, bw);
}

// light of one fibrous strand bundle at (v across, u along), half-width w
float fibres(float v, float u, float w, float streaky, float seed){
  float a = abs(v) / w;
  float f1 = vnoise(vec2(v / w * 6. + seed, u * 3. - uT * 7.));
  float f2 = vnoise(vec2(v / w * 17. + seed * 2.3, u * 6. - uT * 11.));
  float f3 = vnoise(vec2(v / w * 37. + seed * 4.1, u * 11. - uT * 15.));
  float fib = smoothstep(.35, .9, f1) * .5 + smoothstep(.45, .95, f2) * .45 + smoothstep(.55, .95, f3) * .35;
  fib = fib * fib * 1.6;
  float body = exp(-a * a * 1.3);
  float halo = exp(-pow(a / 3.2, 2.)) * .16;
  return body * mix(.55 + 1.25 * fib, .12 + 1.5 * fib, streaky) + halo * (1. - .5 * streaky);
}

// the band's light; also returns the hole mask (under the arch, above the diagonal)
vec3 swoosh(vec2 s, out float inside){
  vec2 p = (s - uArchC) / uArchR;
  float sw = 1. + 4.2 * uWidth * uWidth;
  vec3 g = polyDiag(p);
  // R3-01 (R4): src 9.20-9.37 - the flood swells from the lower-left limb and the eye; right of
  // the eye the band stays a sheet at mid height (the top right corner dark to 9.30)
  float swD = 1. + 4.2 * uWidth * uWidth * mix(1., .5, smoothstep(.12, .55, p.x) * (1. - uWhite));
  float a1 = abs(g.x) / (g.z * swD);
  // the comet's head runs up the diagonal from the lower left; its tail is the streaky part
  float on1 = smoothstep(uHeadL + .02, uHeadL - .30, g.y);
  float L1 = fibres(g.x, g.y, g.z * swD, mix(.45, 1., smoothstep(.95, .1, g.y)) * (1. - .8 * uWidth), 1.7) * on1 * .8;
  vec3 h = polyArch(p);
  // R2-06 (R3, src 9.07-9.22 at 1080p): the loop is the eye's thick fluid upper lid - its bright
  // body (L > 200) measured 99 / 108 / 120 / 127 px at the apex (tube height ~420 px), over a dark
  // lens-shaped hollow ~120-180 px tall; the R2 ring was ~50-60 px. The outer edge stays (lowered
  // ~28 px), the lid grows inward over the old hollow
  float wA0 = h.z * sw;
  // (thin again at both ends of the arch, ~.76 long: past its end points the signed distance
  // flips side, so an offset lid would show a straight seam along the end segments)
  // (the flood widens the band itself from 5.30; the eye's hollow opens up again - src 9.27-9.37)
  float kT = 1. - smoothstep(.35, .8, uWidth);
  float kL = smoothstep(.0, .12, h.y) * smoothstep(.764, .70, h.y) * kT;
  float eO = wA0 - .018 * kL, eI = -wA0 - .080 * kL;
  float wA = (eO - eI) * .5;
  float hA = h.x - (eO + eI) * .5;
  float a2 = abs(hA) / wA;
  // the loop grows off the diagonal once the head has passed its foot (diag length ~ .87)
  float on2 = smoothstep(uHeadL - .87 + .02, uHeadL - .87 - .25, h.y);
  float L2 = fibres(hA, h.y, wA, .3, 5.3) * on2;
  float inner = exp(-pow((h.x + h.z * 2.2) / (h.z * 2.6), 2.)) * on2 * .55
              * smoothstep(-.06, .02, p.y - (-.03 + .33 * (p.x - .02))) * smoothstep(.30, .22, abs(p.x - .015));
  vec3 c1 = mix(vec3(1., .975, .93), vec3(1., .62, .26), smoothstep(.3, 1.3, a1));
  // (R2-06: the thick lid is golden - white-yellow core, orange edges - not a white slab)
  vec3 c2 = mix(vec3(1., .93, .76), vec3(1., .60, .22), smoothstep(.15, 1.1, a2));
  vec3 C = (c1 * L1 + c2 * L2 * mix(1., .86, kL)) * 1.1 + vec3(1., .72, .30) * inner * (1. - .6 * uWidth);
  // the hole: under the arch, above the diagonal
  float ax = (p.x - .015) / .20;
  // (R2-06: the hollow's top follows the lid's lowered inner edge)
  float top = -.02 - .074 * (1. - smoothstep(.35, .8, uWidth)) + .215 * sqrt(max(1. - ax * ax, 0.));
  float flo = -.03 + .33 * (p.x - .02);
  inside = smoothstep(flo + .03, flo + .06, p.y) * smoothstep(top + .004, top - .03, p.y) * (1. - smoothstep(.85, 1., abs(ax)));
  return C * (1. - .92 * inside);
}

float helixLines(vec2 s, vec2 p0, vec2 dir, float amp, float k, float ph, float w, out float depth){
  vec2 n = vec2(-dir.y, dir.x);
  float u = dot(s - p0, dir), v = dot(s - p0, n);
  float L = 0.;
  depth = 0.;
  for (int j = 0; j < 2; j++) {
    float a = k * u + ph + float(j) * 3.14159;
    float y = amp * sin(a);
    float dz = cos(a);
    float slope = amp * k * cos(a);
    float dist = abs(v - y) / sqrt(1. + slope * slope);
    float ww = w * (1. + .5 * dz);
    float l = exp(-pow(dist / ww, 2.)) * (.55 + .45 * dz);
    L += l;
    depth = max(depth, l * dz);
  }
  // rungs between the strands
  float per = 6.2832 / k / 5.;
  float ur = (fract(u / per) - .5) * per;
  float y0 = amp * sin(k * u + ph), y1 = -y0;
  float inside = step(min(y0, y1), v) * step(v, max(y0, y1));
  L += exp(-pow(ur / (w * .7), 2.)) * inside * .9;
  return L;
}

// R1-18 (src 9.50-9.97): DNA ladders seen in perspective - each streams out of a far point
// towards the camera: radius, strand width and turn length grow along the axis, base-pair rungs
// join the two strands. Over the shot the far points gather at the vanishing point and the
// ladders come close (thick crossing bands, then >=3 bands converging, src 9.90-9.97).
// R4-01 (R5, src 9.55-9.88 at 1080p): q blends in the original's line art - the strands stay fine
// (~2-5 px, rungs 1-3 px) instead of thickening with the nearness, and the near parts go out of
// focus: wider but fainter by the same factor, so a near ladder reads as a translucent web.
// R6-C (src 9.55-9.88): smeared along the screen vector sm in one pass - each strand
// and rung widens by the part of the smear across it and fades by the root of the width ratio (R5
// averaged up to 10 shifted copies of all the ladders per pixel). With sm = 0 every expression
// reduces exactly to R5's (6.033 on: R1-18 pixel-identical)
float dna(vec2 s, vec2 F, float ang, float a0, float grow, float k, float ph, float w0, float q, vec2 sm){
  vec2 d = vec2(cos(ang), sin(ang));
  vec2 n = vec2(-d.y, d.x);
  float u = dot(s - F, d), v = dot(s - F, n);
  float smu = dot(sm, d), smv = dot(sm, n);
  float su = abs(smu), sv = abs(smv);
  if (u < -.01 - .5 * su) return 0.;
  float u1 = max(u, 0.);
  float g1 = 1. + grow * u1;
  float A = a0 * g1;
  float w = w0 * mix(g1, sqrt(g1) * 1.6, q);
  float b = q * .35 * max(g1 - 4., 0.) * w0;
  // (nothing of the ladder reaches further than this from its axis)
  if (abs(v) > A + 3. * (w + b) + .5 * sv) return 0.;
  float phi = k * log(g1) + ph;
  float dphi = k * grow / g1;
  float L = 0.;
  for (int j = 0; j < 2; j++) {
    float a = phi + float(j) * 3.14159;
    float y = A * sin(a);
    float dz = cos(a);
    float slope = a0 * grow * sin(a) + A * dz * dphi;
    float nr = sqrt(1. + slope * slope);
    float dist = abs(v - y) / nr;
    float ww = w * (.70 + .40 * dz);
    float w2 = ww + .5 * abs(smv - slope * smu) / nr;
    L = max(L, smoothstep(w2 + b, w2 * .35, dist) * pow(ww / (ww + b), .4) * sqrt(ww / w2) * (.50 + .50 * dz));
  }
  // base pairs: 10 per turn, bars across from one strand to the other
  float per = 6.2832 / 10.;
  float du = (fract(phi / per) - .5) * per / max(dphi, 1e-3);
  float y0 = abs(A * sin(phi));
  float inside = smoothstep(y0 + w * .3 + b + .5 * sv, y0 - w * .3, abs(v));
  float hs = .5 * su;
  L = max(L, smoothstep(w * .55 + hs + b, w * .2 + hs * .364, abs(du)) * pow(w * .55 / (w * .55 + b), .4) * sqrt(w * .55 / (w * .55 + hs)) * inside * .70);
  // fades in from the far point
  return L * smoothstep(.0, .06, u + hs);
}
// R1-18 (R3, src 9.85-9.98 at 1080p): two ladders sweep past right in front of the lens - wide
// white twisted bands crossing in a big X over the readout (A rising to the right ~9-18 deg,
// B falling ~33-43 deg; full width ~.06-.09 H, wider towards the frame edges). Screen space.
float kf5(float x, float v0, float v1, float v2, float v3, float v4){
  // keys at src 9.85, 9.883, 9.917, 9.95, 9.98
  float a = clamp((x - 9.85) / .033, 0., 1.), b = clamp((x - 9.883) / .034, 0., 1.);
  float c = clamp((x - 9.917) / .033, 0., 1.), d = clamp((x - 9.95) / .03, 0., 1.);
  return v0 + (v1 - v0) * a + (v2 - v1) * b + (v3 - v2) * c + (v4 - v3) * d;
}
float xband(vec2 s, float ang, float b0, float hw, float per, float ph, float seed){
  vec2 d = vec2(cos(ang), sin(ang));
  vec2 n = vec2(-d.y, d.x);
  vec2 P = vec2(0., b0);
  float u = dot(s - P, d), v = dot(s - P, n);
  // nearer the camera towards the frame edges
  float h = hw * (1. + .55 * u * u);
  // the twist narrows the band at its nodes (src 9.917: band A pinches left of the readout)
  float tw = cos(6.2832 * u / per + ph);
  float hh = h * (.38 + .62 * abs(tw));
  // ragged edges: the rungs stick out a little and the motion smears them
  float e = abs(v) / max(hh, 1e-4) + .14 * (vnoise(vec2(u * 70. + seed, sign(v))) - .5);
  float body = smoothstep(1.08, .86, e);
  // two strands along the edges, fine base-pair rungs across, smeared along the band (src 9.95:
  // a rough white 'scaly' band, not a smooth ribbon)
  float strand = exp(-pow((e - .80) / .15, 2.));
  float rung = smoothstep(.2, .8, .5 + .5 * cos(6.2832 * u / .0095 + seed + 3. * vnoise(vec2(v * 40., seed))));
  float grit = vnoise(vec2(u * 34. + seed, v / max(hh, 1e-4) * 2.5));
  float tex = .62 + .22 * rung + .16 * grit;
  return clamp(tex + strand * .30, 0., 1.) * body;
}
float xbands(vec2 s, float ts){
  float on = smoothstep(9.835, 9.87, ts);
  if (on <= 0.) return 0.;
  float aA = radians(kf5(ts, 6., 9., 18., 15., 14.));
  float bA = kf5(ts, .14, .16, .21, .21, .22);
  float wA = kf5(ts, .026, .032, .041, .045, .048);
  float aB = radians(kf5(ts, -40., -37., -33., -41., -43.));
  float bB = kf5(ts, .32, .27, .14, .15, .13);
  float wB = kf5(ts, .022, .030, .038, .044, .048);
  float A = xband(s, aA, bA, wA * 1.08, 1.45, ts * 6. + 1.9, 1.3);
  float B = xband(s, aB, bB, wB * 1.08, 1.25, -ts * 5. + 2.6, 4.7);
  // (kept under the bloom's knee: src 9.95-9.97 the digits still read beside and through them)
  return max(A, B) * on * .74;
}

// R4-01 (R5): the line-art look of the mid shot (src 9.55-9.88), ramped in over 5.62-5.70 and
// handed back to the R4 close-up bands from src 9.84 (ladder time .62-.76)
float ladQ(float t){ return smoothstep(.086, .238, t) * (1. - smoothstep(.62, .76, t)); }
// the ladders of the shot; front = 1: those crossing in front of the readout. R5: four more
// background ones in the mid shot (src 9.60: some eight or nine ladders, fine and see-through,
// where R4 had four or five heavy ones)
float ladders(vec2 s, float t, float front, vec2 sm){
  float L = 0.;
  vec2 VP = vec2(.02, .07);
  float cv = smoothstep(.40, .92, t);          // far points gather at the VP
  float q = ladQ(t);
  int nl = q > 0. ? 12 : 8;
  // R6-C: each pass runs over its own ladders only (front 1 / 3 / 6, the rest behind - in the same
  // order as before, so the sums are R5's), instead of all twelve with the others skipped
  bool fp = front > .5;
  // (R6-C: the near parts - away from the vanishing point, where the readout is)
  float mR = smoothstep(.30, .50, length(s - VP));
  int nk = fp ? 3 : nl - 3;
  for (int k = 0; k < 9; k++) {
    if (k >= nk) break;
    int i = fp ? (k == 0 ? 1 : k == 1 ? 3 : 6) : (k < 1 ? 0 : k < 2 ? 2 : k < 4 ? k + 2 : k + 3);
    float fi = float(i);
    bool isFront = fp;
    // start layout (src 9.52-9.70): far points spread round the readout, axes diagonal
    vec2 F0 = vec2(mix(-.30, .34, hash11(fi * 3.7 + .4)), mix(-.05, .20, hash11(fi * 5.1 + 2.)));
    // axis angles at the start / end of the shot (src 9.52 / 9.97)
    float a0 = (i == 0) ? 3.75 : (i == 1) ? -.18 : (i == 2) ? .62 : (i == 3) ? 3.38 : (i == 4) ? 4.9 : (i == 5) ? 2.2 : (i == 6) ? .2 : (i == 7) ? 5.6 : (i == 8) ? 1.45 : (i == 9) ? 2.75 : (i == 10) ? 4.3 : .95;
    float a1 = (i == 0) ? 3.85 : (i == 1) ? -.42 : (i == 2) ? .55 : (i == 3) ? 2.75 : (i == 4) ? 4.45 : (i == 5) ? 2.45 : (i == 6) ? .08 : (i == 7) ? 5.3 : (i == 8) ? 1.6 : (i == 9) ? 2.95 : (i == 10) ? 4.1 : .8;
    vec2 F = mix(F0, VP + .02 * vec2(cos(fi * 2.), sin(fi * 2.)), cv);
    float ang = mix(a0, a1, smoothstep(.0, 1., t)) + .08 * sin(t * 4. + fi);
    float grow = mix(5., 15., smoothstep(.2, 1., t)) * mix(.85, 1.2, hash11(fi * 9.3));
    // close up the ladders read as long twisted bands: radius small against the turn length,
    // the strands thick (src 9.90-9.97)
    float late = smoothstep(.55, 1., t);
    // (R5: src 9.60-9.80 the helices are wide for their fine strands - the one crossing under the
    // digits most of all)
    float rad = mix(.010, .014, hash11(fi * 2.9)) * (1. - .55 * late) * (1. + (isFront ? 1.1 : .5) * q);
    // (R6-C: and in the mid shot the ones behind the readout thicken as they come close - src
    // 9.70-9.80, thick bands; the front ones stay R5's, so the glyphs read through them)
    float wid = .0021 * (1. + 2.2 * late) * (1. + (isFront ? 0. : LAD_W) * q * smoothstep(.10, .60, t) * mR);
    // R1-18 (R4): by src 9.93 the background ladders are faint grey ghosts under the X bands
    // (R6-C: and they are gone by src 9.80 - three or four bright bands there)
    float ex = (i >= 8) ? q * mix(.55, .8, hash11(fi * 6.1)) * (1. - LAD_BGF * smoothstep(.45, .66, t)) : 1.;
    if (ex <= 0.) continue;
    L += dna(s, F, ang, rad, grow, mix(8.5, 5., late), -t * 22. + fi * 1.7, wid, q, sm) * mix(.75, 1., hash11(fi * 4.4)) * (1. - .8 * smoothstep(.76, .9, t)) * ex;
  }
  return L;
}
// the ladders' brightness: R5 dimmed the web 20 % in the mid shot (cream-peach where faint); R6-C:
// as they come close their cores burn cream-white (src 9.70-9.80: L > 200 on .07 / .14 of the web).
// Only the ones behind the readout and only away from the vanishing point (the near parts, which
// are smeared too): over the readout the glyphs read as in R5
float ladGain(float tl, float q5, float h, vec2 s0){
  float r = length(s0 - vec2(.02, .07));
  return (1. - .2 * q5) + LAD_GAIN * q5 * (.55 + .7 * smoothstep(.30, .65, tl)) * smoothstep(.35, .8, h) * smoothstep(.30, .50, r);
}
// R4-01 (R5, src 9.55-9.88): the ladders rush past the lens - smeared along the rays from the
// vanishing point: next to the readout they stay sharp, towards the frame's edges the smear grows
// fast (src 9.60: the near helix at the left is a see-through web of strands streaked sideways
// over ~30-80 px). The smear length goes as the cube of the distance from the vanishing point
float ladSmear(vec2 s0, float t, float front){
  vec2 VP = vec2(.02, .07);
  vec2 dv = s0 - VP;
  float r = length(dv);
  // (R6-C: and grows as they come close - src 9.80, the streaks reach across half the frame)
  float sl = .12 * ladQ(t) * r * r * r * (1. + LAD_ST * smoothstep(.30, .65, t));
  float np = sl / max(uPx, 1e-5);
  // R6-C: one analytic smear, centred half way along it (R5: up to 10 jittered samples); under a
  // pixel none (sm = 0: R5's ladders exactly). One call either way
  vec2 sm = np < 1. ? vec2(0.) : dv * (sl / max(r, 1e-4));
  return ladders(s0 - .5 * sm, t, front, sm);
}

// the tunnel's white floor at tunnel time fu (s after TUBE9); returns its mask and colour
// (R2-04, R3: edge fitted per 60 fps frame, src 9.417 / 9.433 / 9.45 / 9.467 / 9.483 = fu 0 / .016 /
// .033 / .05 / .066 as y = b + k x - c x^2: b -.08/-.093/-.125/-.17/-.37, k .45/.413/.425/.40/.25,
// c 0/.10/.25/.40/.45 - a steep diagonal rising to the right from the first frame, sinking out
// of the bottom by 9.50; the R2 floor was nearly level and only tilted by 9.45)
float tunnelFloor(vec2 s0, float fu, out vec3 fc){
  float fb = fu < .016 ? mix(-.08, -.093, fu / .016) : fu < .033 ? mix(-.093, -.125, (fu - .016) / .017) : mix(-.125, -.186, clamp((fu - .033) / .017, 0., 1.)) - .2 * clamp((fu - .05) / .016, 0., 1.) - .25 * clamp((fu - .066) / .017, 0., 1.);
  float fk = fu < .016 ? mix(.45, .413, fu / .016) : fu < .033 ? mix(.413, .425, (fu - .016) / .017) : mix(.425, .49, clamp((fu - .033) / .017, 0., 1.)) - .15 * clamp((fu - .05) / .016, 0., 1.);
  float fc2 = fu < .016 ? mix(0., .10, fu / .016) : fu < .033 ? mix(.10, .25, (fu - .016) / .017) : mix(.25, .40, clamp((fu - .033) / .017, 0., 1.)) + .05 * clamp((fu - .05) / .016, 0., 1.);
  // R3 (R1-17 non-regression, src 9.418-9.467 at 1080p): right of x ~ +.05..+.35 the floor's edge
  // runs flat at the foot of the right tubes (-.074 at +.15 -> -.094 at +.45, the same in all four
  // frames) instead of rising across them - the big right digit stays above it
  float yP = fb + fk * s0.x - fc2 * s0.x * s0.x;
  float yF = -.064 - .066 * s0.x;
  float hm = max(.03 - abs(yP - yF), 0.) / .03;
  float fe = s0.y - (min(yP, yF) - hm * hm * .03 * .25);
  fc = vec3(.99, .985, .97) - vec3(.06, .055, .05) * smoothstep(-.4, .6, s0.x) * smoothstep(-.5, .0, fe);
  fc -= vec3(.25) * exp(-pow(fe / .012, 2.)) * smoothstep(.4, -.2, s0.x);
  return smoothstep(.006, -.006, fe) * uFloor;
}

void main(){
  vec2 s0 = (vUv - .5) * vec2(uAspect, 1.);
  vec2 s = rot2(uRot) * s0;
  vec3 c = vec3(.012, .011, .010);
  if (uPhase < .5) {
    float inside;
    vec3 band = swoosh(s0, inside);
    if (uLayer > .5) {
      // second pass: the share of the band's light that lies in front of the tubes
      vec3 fr = band * uFront * (1. - uTunnel);
      // R2-04 (src 9.433: the floor's edge cuts across the right tubes): the tunnel floor is
      // nearer than the instrument, so it is added over it here (and left dark in the first pass)
      if (uTunnel > 0.) {
        vec3 fc;
        float fm = tunnelFloor(s0, uT, fc);
        fr += fc * fm * uTunnel;
      }
      o = vec4(fr, 1.);
      return;
    }
    // ground: dark grainy surface under the instrument (a planet seen from very close)
    float hy = -.005 + .07 * s.x - .12 * s.x * s.x;
    float g = smoothstep(hy + .004, hy - .004, s.y) * uGround;
    float tex = fbm(s * vec2(14., 30.)) * .6 + vnoise(s * 160.) * .4;
    vec3 gc = vec3(.05, .048, .046) + vec3(.13) * tex * smoothstep(-.7, hy, s.y);
    gc += vec3(.9, .85, .75) * step(.93, vnoise(s * vec2(90., 160.))) * smoothstep(hy - .35, hy, s.y) * .5;
    // the band lights the cloud tops of the ground under it (src 9.03-9.20)
    float bl = dot(band, vec3(.33));
    gc += vec3(.85, .80, .72) * smoothstep(.35, .85, fbm(s * vec2(6., 14.) + 2.)) * smoothstep(.0, .6, bl) * .45;
    c = mix(c, gc, g * (1. - uTunnel));
    c += band * (1. - uFront) * (1. - uTunnel);
    // the flood: the limbs' white swallows everything but the eye's hole and the upper left
    float fl = smoothstep(.0, 1., uWhite) * (1. - inside);
    float keepDark = smoothstep(.0, -.35, s0.x - .9 * (s0.y - .25)) * (1. - uWhite2);
    c = mix(c, vec3(1., .985, .96), fl * (1. - keepDark) * (1. - uTunnel));
    // inside the tunnel (src 9.40-9.47): a dark ribbed vault, the readout's shelf, a white floor
    if (uTunnel > 0.) {
      vec3 tc = vec3(.018, .016, .015);
      vec2 O = vec2(.02, -.75);
      float rr = length((s0 - O) * vec2(.62, 1.));
      for (int i = 0; i < 4; i++) {
        float fi = float(i);
        float r0 = .98 + fi * .14 + .25 * fract(uT * 2.2 + fi * .25);
        float rib = exp(-pow((rr - r0) / (.018 + .012 * fi), 2.));
        tc += vec3(.11, .10, .09) * rib * smoothstep(-.1, .2, s0.y);
      }
      tc += vec3(.16, .13, .11) * exp(-pow((s0.y - .30) / .06, 2.)) * smoothstep(.95, .2, abs(s0.x));
      tc += vec3(.07, .06, .05) * fbm(s0 * vec2(3., 12.) + uT) * smoothstep(-.05, .4, s0.y);
      // the white floor: what is left of the lower limb (tunnelFloor)
      vec3 fc;
      float floorM = tunnelFloor(s0, uT, fc);
      // (drawn in front of the instrument in the second pass; black underneath)
      tc *= 1. - floorM;
      // R2-04 (src 9.417-9.433): we leave the glass tube - its wall is a bright warm-white ring
      // round the vault (inner edge ~(.62, .48) at 9.417, ~(.72, .56) at 9.433, out of frame by
      // 9.45), smeared by the speed, shading to grey at its inner edge
      {
        vec2 wq = (s0 - vec2(-.01, .03)) / vec2(1., .78);
        float rho = length(wq);
        float R0 = .62 + 6.2 * uT;
        float ang = atan(wq.y, wq.x);
        float sm = vnoise(vec2(ang * 9., rho * 2. - uT * 40.)) * .6 + vnoise(vec2(ang * 31., rho * 5.)) * .4;
        // (src 9.45: the left wall has gone, only the right near tube still glows)
        float wEnd = s0.x < 0. ? 1. - smoothstep(.020, .032, uT) : 1. - smoothstep(.026, .040, uT);
        float wall = smoothstep(R0 - .07, R0 + .03, rho + (sm - .5) * .05) * wEnd;
        vec3 wc = mix(vec3(.52, .47, .43), vec3(1., .95, .86), smoothstep(R0 - .02, R0 + .12, rho)) * (.88 + .16 * sm);
        // (the wall never covers the floor: the floor is drawn over it in the original)
        tc = mix(tc, wc, wall * (1. - floorM));
      }
      c = mix(c, tc, uTunnel);
    }
  } else {
    if (uLayer > .5) {
      float tl = clamp((uT - .025) / .525, 0., 1.);
      float hf = ladSmear(s0, tl, 1.);
      hf = max(hf, xbands(s0, uT + 9.45));
      float q5 = ladQ(tl);
      vec3 fr = mix(mix(vec3(1., .80, .52), vec3(1., .86, .72), q5), vec3(1., .96, .90), smoothstep(.2 + .4 * q5, .8 + .7 * q5, hf)) * hf * (1. - .2 * q5) * uHelix * uFront;
      // R2-04 (src 9.475-9.483): the tunnel floor is still sinking out of the bottom of the frame
      // (tunnel time = uT + .033: phase 1 counts from DARK2)
      if (uFloor > 0.) {
        vec3 fc;
        float fm = tunnelFloor(s0, uT + .033, fc);
        fr += fc * fm;
      }
      o = vec4(fr, 1.);
      return;
    }
    // orange smoke wisps
    float w = fbm(s * 3. + vec2(uT * 1.6, uT * .4));
    float w2 = fbm(s * 7. - vec2(uT * 2.4, 0.));
    c += vec3(1., .42, .09) * pow(w, 3.) * 1.4 * uWisp * smoothstep(-.2, .6, s.x + .3 * s.y);
    c += vec3(1., .6, .25) * pow(w2, 4.) * .9 * uWisp;
    // DNA ladders (the front ones are added again over the readout in the second pass)
    float tl = clamp((uT - .025) / .525, 0., 1.);
    float h = ladSmear(s0, tl, 0.);
    // (R5: the original's web is cream-peach, not orange, where it is faint, and only goes white
    // where strands overlap)
    float q5 = ladQ(tl);
    c += mix(mix(vec3(1., .80, .52), vec3(1., .86, .72), q5), vec3(1., .96, .90), smoothstep(.2 + .4 * q5, .8 + .7 * q5, h)) * h * ladGain(tl, q5, h, s0) * uHelix;
    // R4-01 (R5, src 9.60-9.85): a broad orange glow over the right and upper right (the end digit
    // that left the frame and the big one right of the middle light the smoke there)
    // (upper-right quarter R-B src 9.60 / 9.70 / 9.80: 64 / 55 / 98 - it swells towards 9.80)
    c += vec3(.62, .22, .03) * q5 * (.45 + .55 * smoothstep(.45, .62, tl)) * smoothstep(-.05, .75, s0.x) * smoothstep(-.35, .3, s0.y) * (.55 + .45 * fbm(s0 * 2.5 + vec2(uT * .8, 0.)));
    // light ribbons crossing the frame
    for (int i = 0; i < 6; i++) {
      float fi = float(i);
      float a = mix(-.55, .55, hash11(fi * 3.1 + 1.)) + .25 * sin(uT * 3. + fi);
      vec2 dir = vec2(cos(a), sin(a));
      vec2 p0 = vec2(mix(-.6, .6, hash11(fi * 7.7)), mix(-.25, .3, hash11(fi * 5.3))) + dir.yx * vec2(-1., 1.) * (uT - .3) * mix(.4, 1.2, hash11(fi));
      float dist = abs(dot(s - p0, vec2(-dir.y, dir.x)));
      float wdt = mix(.012, .05, hash11(fi * 9.1)) * (.6 + uRib);
      float on = smoothstep(fi * .12, fi * .12 + .2, uRib);
      float rb = exp(-pow(dist / wdt, 2.)) * (.6 + .4 * fbm(vec2(dot(s, dir) * 8. - uT * 20., fi)));
      c += mix(vec3(1., .62, .25), vec3(1., .95, .86), exp(-pow(dist / (wdt * .3), 2.))) * rb * on * 1.1;
    }
  }
  o = vec4(c, 1.);
}`;
