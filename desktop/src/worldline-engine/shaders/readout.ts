import { COMMON } from './common';
import { GEOM_DEFINES } from '../show/geom';
import { GLYPH_W, GLYPH_H, GLYPH_MARGIN, CELLS } from '../engine/glyphs';

// The instrument, composited over the scene. Geometry and materials follow the original at the
// lock framing: flat black case with a thin brown circuit board on top and a few parts on it,
// white ceramic sockets, clear frosted tubes with a glowing white rim and a white tip, a black
// support bar at the top, a perforated anode sheet, the dark cathode stack, a black lead comb,
// and a hot yellow-orange digit. uMode 1 = "ghost" (translucent, overexposed: fragments/gears),
// uMode 2 = line art (cream outlines on dark: the dark light-sweep shots).
// The view ray is traced against the real volumes (cylinders, case box), so the same shader
// serves the frontal lock framing and the low 3/4 views.
export const READOUT_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform sampler2D uScene;
uniform sampler2D uAtlas;
uniform float uAspect;
uniform float uPx;
uniform vec4 uRO;
uniform vec3 uView;
uniform float uBoardOnly;
uniform float uDigA[8];
uniform float uDigB[8];
uniform float uMix[8];
uniform float uLit[8];
uniform float uFlash[8];
uniform float uGlow;
uniform float uHalo;
uniform float uGlowAvg;
uniform float uPlinth;
uniform float uGlass;
uniform float uEnvDark;
uniform float uTGlow;   // R2-04: warm white glass of the near tubes in the T10 tunnel
uniform float uClear;   // R2: clear glass in the dark exit (src 13.45-13.60: bokeh seen through the tubes)
uniform float uRimFade; // R1-25 (R4): the glass rims / speculars melt away in the exit burst
uniform float uMode;
uniform float uBlur;
uniform float uLineK;
uniform float uTime;
uniform vec4 uWarp;
uniform float uWarpY;
uniform vec3 uWarpB; // R3-06 (R4): the end tubes lift / drop on the tube wall (t6, t7, t0)
float gWarpPx = 1.;
${COMMON}
${GEOM_DEFINES}
#define GW ${GLYPH_W.toFixed(1)}
#define GH ${GLYPH_H.toFixed(1)}
#define GM ${GLYPH_MARGIN.toFixed(1)}
#define NCELLS ${CELLS.toFixed(1)}
#define CASE_D .075
#define PCB_T .006
#define PCB_IN .010

// R3 (R1-01, src 11.60 at 1080p): the anode mesh runs from 371 px to 515 px (clear glass between
// it and the top mica), ~42 px either side of the axis
#define MESH_Y0 .0235
#define MESH_Y1 .1565
#define MESH_R .039

// glass envelope: cylinder + dome; R3: the lower corners are rounded where the glass sits on the
// socket (src 11.60: ~12 px radius, the paper shows outside the curve)
float envSD(vec2 l){
  float body = glassSD(l);
  float foot = sdRoundBox(l - vec2(0., (SOCK_Y1 - .001 + .6) * .5), vec2(TUBE_HW, (.6 - SOCK_Y1 + .001) * .5), .012);
  return max(body, foot);
}
// exhaust tip: a short, wide glass nub on the dome (measured .232-.251, half-width .0088)
float tipSD(vec2 l){ return sdRoundBox(l - vec2(0., .2415), vec2(.0088, .0095), .0058); }
float tubeSD(vec2 l){ return min(envSD(l), tipSD(l)); }
float socketSD(vec2 l){ return sdRoundBox(l - vec2(0., (SOCK_Y0 + SOCK_Y1) * .5), vec2(SOCK_HW, (SOCK_Y1 - SOCK_Y0) * .5), .007); }
// mica disc at the top of the electrode stack, seen almost edge-on
float micaSD(vec2 l){ return sdRoundBox(l - vec2(0., .1775), vec2(.0403, .0048), .004); }
// R3 (R1-01, src 11.60): the bottom of the stack is a thin dark mica (.0102-.0148), a bright gap
// line (.0083) and a dark barrel-shaped press (-.0335 to .0055, ~36 px either side) with four
// narrow light slits between its leads - not five black legs on the socket
float mica2SD(vec2 l){ return sdRoundBox(l - vec2(0., .0121), vec2(.0355, .0032), .0018); }
// (src 11.60 tube 2, dark-pixel extents: 534-556 px +-36.5 px, then narrowing to +-24 px at 577 px;
// the foot runs into the socket at ~580 px)
float cupHW(float y){ return .0338 - .62 * max(-.016 - y, 0.); }
float combSD(vec2 l, out float fx){
  float hw = cupHW(l.y);
  fx = l.x / .0338;
  float d = max(abs(l.x) - hw, abs(l.y + .01565) - .02115);
  // rounded foot
  vec2 q = vec2(abs(l.x) - (cupHW(-.0368) - .007), -.0368 + .007 - l.y);
  if (q.x > 0. && q.y > 0.) d = max(d, length(q) - .007);
  return d;
}

vec2 glyphUV(vec2 l, float cell){
  float sc = GH / DIGIT_H;
  vec2 g = vec2(l.x * sc + GW * .5, -l.y * sc + GH * .5);
  vec2 c = clamp((g + GM) / vec2(GW + 2. * GM, GH + 2. * GM), .003, .997);
  return vec2((cell + c.x) / NCELLS, c.y);
}

// R1-14: blurred wire coverage of tube k's digit (0 outside its atlas cell)
float haloA(vec2 dl, int k){
  float sc = GH / DIGIT_H;
  vec2 g = vec2(dl.x * sc + GW * .5, -dl.y * sc + GH * .5);
  vec2 c = (g + GM) / vec2(GW + 2. * GM, GH + 2. * GM);
  if (c.x < 0. || c.x > 1. || c.y < 0. || c.y > 1.) return 0.;
  float a1 = textureLod(uAtlas, vec2((uDigA[k] + c.x) / NCELLS, c.y), 3.).a;
  float a2 = textureLod(uAtlas, vec2((uDigB[k] + c.x) / NCELLS, c.y), 3.).a;
  return mix(a1, a2, uMix[k]);
}

// perforated sheet: round holes on a hex lattice (1 inside a hole); fades to its mean when the
// lattice gets too fine for the pixel grid
float holeMask(vec2 p, float pxCell){
  vec2 r = vec2(1., 1.7320508);
  vec2 h = r * .5;
  vec2 a = mod(p, r) - h;
  vec2 b = mod(p - h, r) - h;
  vec2 gv = dot(a, a) < dot(b, b) ? a : b;
  float aa = clamp(1. / max(pxCell, .5), .05, .5);
  float m = 1. - smoothstep(.31 - aa, .31 + aa, length(gv));
  return mix(.34, m, smoothstep(1.7, 3.4, pxCell));
}

float lineM(float d, float w, float aa){ return 1. - smoothstep(w, w + aa, abs(d)); }
float pxAt(float t){ return gWarpPx * uPx * (uView.z > 0. ? t : 1.) / uRO.x; }

vec4 cylCoords(vec3 ro, vec3 rd, float xk){
  vec3 w0 = ro - vec3(xk, 0., 0.);
  float a = dot(rd, rd), b = rd.y, e = w0.y, dd = dot(rd, w0);
  float den = max(a - b * b, 1e-12);
  float tr = (b * e - dd) / den;
  float sl = (a * e - b * dd) / den;
  vec3 pr = ro + tr * rd;
  vec2 side = normalize(vec2(rd.z, -rd.x));
  float lx = dot(vec2(pr.x - xk, pr.z), side);
  float hl = 1. / max(length(rd.xz), 1e-6);
  float tin = tr - sqrt(max(TUBE_HW * TUBE_HW - lx * lx, 0.)) * hl;
  float ts = tr - sqrt(max(SOCK_HW * SOCK_HW - lx * lx, 0.)) * hl;
  return vec4(lx, sl, tin, ro.y + ts * rd.y);
}

// thin grey-brown outline colour of the line-art / ghost passes
vec3 lineColor(){ return vec3(.62, .55, .48) * uLineK; }

void tubeLayer(int ti, vec2 l, float ys, vec2 ld, vec2 lm, float pxr, inout vec3 col, inout float cov){
  bool LINE = uMode > 1.5;
  bool GHOST = uMode > .5 && uMode < 1.5;
  float litT = uLit[ti] * uGlow * (1. + .6 * uFlash[ti]);
  float L = min(litT, 1.);
  vec2 ls = vec2(l.x, ys);
  float dc = socketSD(ls);
  float cc = (1. - smoothstep(-pxr * .8, pxr * .8, dc)) * uGlass;
  float de = envSD(l);
  float dtp = tipSD(l);
  float dt = min(de, dtp);
  float tc = (1. - smoothstep(-pxr * .8, pxr * .8, dt)) * uGlass;
  // (R3: the cathode stacks sit off the tube axes - measured per tube, geom DIGIT_DX)
  vec2 dl = ld - vec2(DIGIT_DX[ti], DIGIT_CY);
  vec2 dlL = ti == 1 ? dl - vec2(.026, 0.) : dl;
  vec4 a1 = texture(uAtlas, glyphUV(dlL, uDigA[ti]), uBlur);
  vec4 a2 = texture(uAtlas, glyphUV(dlL, uDigB[ti]), uBlur);
  vec4 cg = mix(a1, a2, uMix[ti]);
  float flick = 1. + .035 * (vnoise(vec2(uTime * 41. + float(ti) * 11., float(ti))) - .5);
  // neon (R3, R1-01 - src 11.60 at 1080p): a thin saturated yellow core (~(255,240,83), 4.5-6 px
  // on a ~140 px digit, the support wires ~3 px) in a soft orange sheath (~(226,173,9) ->
  // (215,125,5)); never a white core - the post turns anything above 1.0 towards white
  // (pre-post values: the post's shoulder maps 1.24 -> ~250 and its hot push adds a little G/B, so
  // this lands on the measured ~(252,229,58))
  vec3 coreC = vec3(1.24, .86, .11);
  vec3 sheathC = vec3(.88, .50, .04);
  float stroke = clamp(cg.a * 1.15, 0., 1.);
  float core = smoothstep(.30, .80, cg.r);
  vec3 halo = vec3(.95, .42, .03) * cg.g * litT * flick;
  vec3 em = (sheathC * stroke * .95 + coreC * core * .45) * litT * flick + halo * .40;
  vec3 LC = lineColor();
  float nxg = clamp(l.x / TUBE_HW, -1., 1.);
  vec2 fq = dl * vec2(1.5, 1.);
  float fill = exp(-dot(fq, fq) / .011);
  // (R3: one stack of the ten fitted cathodes - src 11.60 shows fewer, thicker dark lines)
  float stack = texture(uAtlas, glyphUV(dl, 11.), uBlur).b;
  float inM = step(abs(lm.x), MESH_R) * smoothstep(MESH_Y0, MESH_Y0 + .002, l.y) * smoothstep(MESH_Y1, MESH_Y1 - .002, l.y);

  if (tc > 0.) {
    vec2 n2 = vec2(nxg, 0.);
    if (l.y > DOME_Y) { float F; vec2 dn = domeN(domeQ(l - vec2(0., DOME_Y)), F); n2 = dn * F; }
    float nl = clamp(length(n2), 0., 1.);
    vec2 disp = rot2(uRO.w) * (n2 * pow(nl, 3.) * .010 * uRO.x);
    vec3 bg = mix(texture(uScene, vUv - disp / vec2(uAspect, 1.)).rgb, col, cov);
    float edgeK = 1. - sqrt(max(1. - nl * nl, 0.));   // 0 facing the camera, 1 at grazing
    vec3 g;
    if (LINE) {
      // line art on the dark field: the tube is a dark amber volume behind thin outlines
      // (src 9.03-9.70: a translucent dark amber body, warmer when lit)
      g = mix(bg, bg * .35 + vec3(.19, .085, .028) * (.5 + .5 * L), .80);
      g += vec3(1., .40, .08) * fill * .10 * litT;
      if (uTGlow > 0.) {
        // R2-04 (src 9.417-9.45 at 1080p): inside the tunnel the near (outer) tubes are glowing
        // warm white glass (~(245,220,148) left, ~(253,249,223) right), the middle ones stay amber
        float sxg = abs((vUv.x - .5) * uAspect);
        float outerG = smoothstep(.40, .62, sxg);
        // (src 9.45: the left one darkens first, the right one still glows)
        outerG *= (vUv.x < .5 ? smoothstep(.55, .9, uTGlow) : 1.);
        vec3 wg = bg * .30 + vec3(1., .80, .46) * (.78 + .25 * L);
        g = mix(g, wg, uTGlow * outerG * .92);
      }
      g += LC * stack * .16 * (1. - .5 * L);
      float meshEdge = lineM(abs(lm.x) - MESH_R, .00035, pxr) * step(MESH_Y0, lm.y) * step(lm.y, MESH_Y1);
      g += LC * (lineM(micaSD(l), .0004, pxr) * .55 + meshEdge * .30);
      float fx;
      g += LC * lineM(combSD(l, fx), .00035, pxr) * .40;
    } else if (GHOST) {
      // double exposure: the scene shows through a clear shell; only the neon digit is strong
      g = mix(bg, vec3(1.), .05 + .10 * edgeK);
      g += LC * stack * .10;
      g += vec3(1., .74, .20) * fill * .10 * litT;
    } else {
      // ---- the real tube ----
      // clear glass: slightly darker transmission, milkier at grazing angles and in the dome
      // (measured: the glass passes ~.82 of the paper behind it, a little desaturated; the
      // dome and the tip read as grey glass, the rim as a ~6 px bright line)
      vec3 milk = vec3(.965, .955, .94) * mix(1., .35, uEnvDark);
      float bl = dot(bg, vec3(.3, .59, .11));
      float domeK = smoothstep(TUBE_YD - .012, TUBE_YD + .010, l.y);
      g = mix(bg * (.97 - .08 * edgeK), mix(bg, vec3(bl), .18) * .83, domeK);
      float Lw = L * (ti == 1 ? .1 : 1.) * (1. - uEnvDark);
      // (R2: src 11.60 tube band is warmer - ~40 % of it saturated amber vs 27 % here before)
      g = mix(g, g * vec3(1., .84, .58), .60 * Lw * (1. - domeK));
      // the lit digit warms everything inside the glass a little (amber glass at the lock)
      g = mix(g, g * vec3(1., .78, .48), .48 * Lw * smoothstep(.0, .8, fill));
      // R3 (R1-01, src 11.60 at 1080p): milky glass - the haze grows towards the walls (the mesh
      // brightens smoothly from ~176 at the axis to ~212 next to the wall, no hard mesh edge), the
      // bright band along each wall ~7-9 px wide (left) / ~6 px (right); warmed by the lit digit
      float wy = exp(-pow((l.y - DIGIT_CY) / .080, 2.));
      float wall = smoothstep(.66, .86, abs(nxg)) * (1. - domeK);
      g = mix(g, g * vec3(1.04, .80, .52), wall * Lw * wy * .60);
      // anode mesh: a perforated cylinder
      // R3 (R1-01, src 11.60 at 1080p): light holes in a darker sheet (the bright set breaks into
      // separate dots, the dark set is one network), ~4.5 px pitch, low contrast (mean |dL/dx| ~3-8);
      // unlit: sheet ~125-135, holes ~150-170. Lit: bright warm orange all over - ~(212,142,68)
      // more than 12 px from the strokes, ~(222,133,30) 5-12 px from them
      if (inM > 0.) {
        float ax = clamp(lm.x / MESH_R, -1., 1.);
        float cth = sqrt(max(1. - ax * ax, 0.));
        vec2 mu = vec2(asin(ax) * MESH_R, lm.y) * 240.;
        float hole = holeMask(mu, 1. / (240. * pxr)) * (.55 + .45 * cth);
        float Lm = L * (ti == 1 ? .12 : 1.);
        float nearS = clamp(cg.g * 1.7, 0., 1.);
        vec3 sheet = bg * vec3(.55, .47, .42);
        vec3 holeC = bg * vec3(.72, .62, .55) + vec3(.02);
        sheet = mix(sheet, mix(vec3(.86, .56, .15), vec3(.88, .52, .08), nearS), Lm);
        holeC = mix(holeC, mix(vec3(.98, .68, .21), vec3(.98, .60, .10), nearS), Lm);
        vec3 meshC = mix(sheet, holeC, hole);
        meshC *= mix(1., .45, uEnvDark);
        float meshSoft = smoothstep(MESH_R, MESH_R - .0035, abs(lm.x));
        g = mix(g, meshC, inM * meshSoft * mix(.92, .55, uEnvDark) * (1. - .8 * uClear));
      }
      // (src 11.60: next to the lit strokes the glass and mesh read saturated orange, ~(215,125,5))
      g = mix(g, g * vec3(1.02, .62, .16), clamp(cg.g * 1.5, 0., 1.) * Lw * .55 * (1. - domeK));
      // unlit cathode stack: dark wires (warmed behind a lit digit)
      // (R3, src 11.60: in a lit tube the stack is lit too - under 4 % of its mesh area is darker
      // than L 100, the wires read ~110; unlit, they are near black)
      float Lst = L * (ti == 1 ? 0. : 1.);
      vec3 wire = mix(vec3(.05, .045, .042), vec3(.52, .30, .09), Lst);
      g = mix(g, wire, stack * .85 * (1. - .30 * Lst) * (1. - .55 * uClear));
      // (src 13.53: a warm orange haze fills the clear glass around the lit digit)
      // (R2 re-check at 1080p, src 13.53: the clear tubes are filled with a warm amber haze, the dome too)
      g += (vec3(1., .52, .16) * .30 * L * (ti == 1 ? .3 : 1.) + vec3(.075, .05, .035)) * uClear * (1. - .45 * domeK);
      // mica disc + the small wire loops above it
      float mica = 1. - smoothstep(-pxr, pxr, micaSD(l));
      vec3 micaC = vec3(.025, .025, .03) + vec3(.26, .25, .24) * exp(-pow((l.y - .1815) / .0011, 2.));
      g = mix(g, micaC, mica * .95 * (1. - .5 * uClear));
      // R3 (src 11.60): two shallow wire arcs on the mica (peaks ~5 px above it at +-28 px), not
      // three round loops
      float lx3 = abs(l.x) - .0255;
      float loop = abs(length(vec2(lx3, l.y - .1828) / vec2(.0160, .0048)) - 1.) * .0048;
      g = mix(g, vec3(.06), (1. - smoothstep(.00045, .00045 + pxr, loop)) * step(.1828, l.y) * .85);
      // the dark tab at the top of the cathode stack (src: 366-380 px, ~12 px wide, in every tube)
      vec2 tq = dl - vec2(-.0004, .0655);
      tq.x += (tq.y + .006) * .25;
      float tab = 1. - smoothstep(-pxr, pxr, sdRoundBox(tq, vec2(.0058, .0062), .0028));
      g = mix(g, vec3(.07, .06, .055) + vec3(.22, .09, .02) * Lw, tab * .92 * (1. - .5 * uClear));
      // R3 (src 11.60, 516-523 px): the gap under the anode reads ~180-200 with the dark foot of
      // the cathode stack in its middle (~+-7 px)
      float gapK = smoothstep(.0150, .0165, l.y) * (1. - smoothstep(MESH_Y0 - .001, MESH_Y0 + .001, l.y)) * (1. - smoothstep(.030, .040, abs(l.x)));
      g = mix(g, g * .88, gapK);
      float foot = 1. - smoothstep(-pxr, pxr, sdRoundBox(dl - vec2(-.0008, .019 - DIGIT_CY), vec2(.0072, .0036), .0015));
      g = mix(g, vec3(.06, .055, .05) + vec3(.20, .08, .02) * Lw, foot * .90 * (1. - .5 * uClear));
      // R3 (R1-01): bottom mica, the bright gap line under it, and the dark barrel with its slits
      float m2 = 1. - smoothstep(-pxr, pxr, mica2SD(l));
      g = mix(g, vec3(.06, .065, .055) + vec3(.34, .13, .03) * Lw, m2 * .92 * (1. - .5 * uClear));
      float gl = exp(-pow((l.y - .0074) / max(.0008, pxr * .7), 2.)) * (1. - smoothstep(.033, .036, abs(l.x)));
      g = mix(g, vec3(.80, .72, .63) + vec3(.12, .04, -.06) * Lw, gl * .70 * (1. - .5 * uClear));
      float fx;
      float dcmb = combSD(l, fx);
      float cmb = 1. - smoothstep(-pxr, pxr, dcmb);
      float yb = clamp((l.y + .0368) / .0423, 0., 1.);
      // five dark lobes between four light slits (src 11.60 tube 2 at y 545-560: lobes ~10-55,
      // slits ~60 / 174 / 130 / 50 at -21 / -6 / +9 / +23 px; lit from the left; a blue-black foot)
      float sx = fx * .036;
      vec3 cmbC = vec3(.045, .05, .055) + vec3(.16, .15, .14) * (1. - smoothstep(-.85, -.35, fx)) * (1. - .5 * smoothstep(.2, .8, -fx - .2));
      cmbC = mix(cmbC, vec3(.02, .085, .14), (1. - smoothstep(.05, .55, yb)) * .80);
      cmbC = mix(cmbC, vec3(.17, .08, .045), Lw * smoothstep(.15, .85, yb) * .80);
      cmbC *= mix(.55, 1., smoothstep(.0, .45, yb));
      float sl = 0.;
      for (int k = 0; k < 4; k++) {
        float xs = -.0195 + .0142 * float(k);
        float wk = k == 1 ? 1. : (k == 2 ? .78 : .32);
        sl = max(sl, exp(-pow((sx - xs) / max(.0012, pxr * .7), 2.)) * wk);
      }
      sl *= smoothstep(-.031, -.024, l.y) * (1. - smoothstep(.001, .0045, l.y));
      cmbC = mix(cmbC, vec3(.78, .70, .62) + vec3(.12, .02, -.10) * Lw, sl * .90);
      g = mix(g, cmbC * mix(1., .55, uEnvDark), cmb * .97 * (1. - .4 * uClear));
      float hz = pow(smoothstep(.30, .98, abs(nxg)), 1.6) * (1. - domeK) * (1. - uEnvDark) * (1. - uClear);
      // (src 11.60: the walls are warm even on the unlit tube between two lit ones, ~(200,176,161))
      float nb = .5 * (uLit[max(ti - 1, 0)] * (ti == 2 ? .2 : 1.) + uLit[min(ti + 1, 7)] * (ti == 0 ? .2 : 1.)) * uGlow;
      vec3 hzC = mix(milk * vec3(.93, .85, .78), vec3(1., .76, .46), clamp(Lw * wy * .8 + nb * .30 * (1. - uEnvDark), 0., 1.));
      g = mix(g, hzC, hz * .42);
      // lit digit: replaces what is behind it (adding it to the bright paper would wash it out)
      g = mix(g, sheathC * flick, stroke * L * .92);
      g = mix(g, coreC * flick, core * L * .92);
      // (R3: the halo stays outside the core - added on top it pushed the core over 1.0, which the
      // post turns white)
      g += halo * .30 * (1. - .3 * uEnvDark) * (1. - .85 * core) + (sheathC * stroke * .04) * litT * (1. - core);
      g += (sheathC * stroke + coreC * core) * max(litT - 1., 0.) * .45;
      // glass surface: thin bright rim right at the edge, soft speculars
      // (R3, src 11.60: the rim peaks 0-1 px inside the outer edge)
      float dIn = -de;
      float rimHi = exp(-pow((dIn - (nxg < 0. ? .0030 : .0024)) / (nxg < 0. ? .0034 : .0026), 2.));
      // (no bright rim along the glass foot behind the press - only in its rounded corners)
      rimHi *= 1. - (1. - smoothstep(.026, .042, abs(l.x))) * (1. - smoothstep(SOCK_Y1 + .004, SOCK_Y1 + .012, l.y));
      rimHi *= 1. - uRimFade;
      g = mix(g, vec3(1., .99, .98) * mix(1., .55, uEnvDark), rimHi * .97);
      g += vec3(.12, .115, .11) * rimHi * (1. - uEnvDark);   // src 11.56: rim peaks ~+40 over the paper
      g += vec3(1., .55, .2) * rimHi * uGlowAvg * uEnvDark * .25;
      float s1 = exp(-pow((nxg + .58) / .06, 2.)) * smoothstep(-.03, .06, l.y) * (1. - smoothstep(TUBE_YD, TUBE_YD + .03, l.y));
      float s2 = exp(-pow((nxg - .70) / .03, 2.)) * smoothstep(-.02, .10, l.y) * (1. - smoothstep(TUBE_YD, TUBE_YD + .02, l.y));
      g += vec3(1.) * (s1 * .05 + s2 * .045) * (1. - uRimFade);
      // dome highlight: a soft arc following the shoulder
      g += vec3(1.) * exp(-pow((nl - .82) / .07, 2.)) * step(TUBE_YD, l.y) * smoothstep(-.6, .2, n2.y) * .05 * (1. - uEnvDark) * (1. - uRimFade);
      // the tip: a small glass nub with its own outline
      float tipM = 1. - smoothstep(-pxr, pxr, dtp);
      vec3 tipC = mix(bg, vec3(bl), .2) * .80 + vec3(1.) * exp(-pow((l.x + .004) / .002, 2.)) * .08;
      g = mix(g, tipC, tipM * .9 * (1. - uEnvDark * .5));
      g = mix(g, g * .80, lineM(dtp, .0004, pxr) * tipM);
      if (uRimFade > 0.) {
        // R1-25 (R4, src 13.833-13.867): the tube shapes melt into the smeared picture - the
        // glass, mica, stack and comb go, only the lit digits (and their glow) stay
        vec3 dg = mix(bg, sheathC * flick, stroke * L * .92);
        dg = mix(dg, coreC * flick, core * L * .92);
        dg += halo * .30 * (1. - .85 * core);
        g = mix(g, dg, uRimFade);
      }
    }
    float cover = LINE ? .88 : 1.;
    col = mix(col, g, tc * cover);
  }
  if (GHOST || LINE) {
    // the neon digit does not fade with the (ghost) glass
    float td = 1. - smoothstep(-pxr * .8, pxr * .8, de);
    if (GHOST) {
      // on the bright paper an additive glow would wash out: the neon replaces what is behind
      // (kept below 1.0: the post turns anything hotter into white, which hid the digits)
      float Lg = uLit[ti];
      vec3 hz = vec3(.95, .52, .05) * cg.g * Lg * flick * (.55 + .20 * uBlur);
      // out of focus (T7): the glow fills the tube; in focus (T8): an orange halo round the wire
      vec3 fg = vec3(1., .80, .25) * fill * Lg * .20 * uBlur;
      // (R1-14, T8: the red channel may run hot - the post's shoulder maps ~1.24 to ~250 - so the
      // glow reads as burning yellow-orange like src 8.70 instead of a flat .98 cap)
      col = mix(col, min(col * (1. - .25 * cg.g * Lg) + hz + fg, vec3(.98 + .27 * uHalo, .98, .98)), td);
      col = mix(col, sheathC * flick, stroke * Lg * .90 * td);
      col = mix(col, coreC * flick, core * Lg * .80 * td);
    } else {
      col += em * 1.05 * td;
      if (uTGlow > 0.) {
        // R3 (R1-17 non-regression, src 9.418-9.433): in the glowing near tubes the big digit stays
        // a yellow stroke on the warm white glass - added on top of it, it washed out to white
        float sxg = abs((vUv.x - .5) * uAspect);
        float tgk = smoothstep(.40, .62, sxg) * (vUv.x < .5 ? smoothstep(.55, .9, uTGlow) : 1.) * uTGlow;
        vec3 cR = col - em * 1.05 * td;
        // (src: a broad yellow glow round the strokes - the glass there reads yellow, not white)
        cR = mix(cR, vec3(1., .80, .22), clamp(cg.g * 1.3, 0., 1.) * L * .60 * td);
        cR = mix(cR, sheathC * flick * 1.10, stroke * L * .90 * td);
        cR = mix(cR, coreC * flick, core * L * .90 * td);
        col = mix(col, cR, tgk);
      }
    }
  }

  if (cc > 0.) {
    float cx = ls.x / SOCK_HW;
    // R3 (R1-01, src 11.60 at 1080p): dark grey-brown sockets (~(63-77) across the left and
    // middle, darker towards the board), the right third catching the light (up to ~200), a
    // narrow light left edge and a light top edge; a lit tube warms its socket
    float sy = clamp((ls.y - SOCK_Y0) / (SOCK_Y1 - SOCK_Y0), 0., 1.);
    vec3 ccol = vec3(.29, .25, .235) * (.70 + .30 * sy);
    ccol = mix(ccol, vec3(.82, .76, .70), smoothstep(.30, 1.0, cx) * .90);
    ccol = mix(ccol, vec3(.78, .72, .66), (1. - smoothstep(-.97, -.62, cx)) * .85);
    ccol = mix(ccol, vec3(.42, .37, .34), exp(-pow((ls.y - (SOCK_Y1 - .0010)) / .0011, 2.)) * .7);
    ccol *= mix(1., .40, uEnvDark);
    ccol += vec3(.36, .22, .10) * L * (ti == 1 ? .1 : 1.) * .30 * (1. - smoothstep(.2, .9, cx));
    ccol += vec3(1., .45, .12) * litT * .2 * uEnvDark;
    float k = (LINE || GHOST) ? 0. : 1.;
    col = mix(col, ccol, cc * k);
  }
  // outlines: a thin grey line around the glass (real tube: only a faint one, the rim does the rest)
  float olw = .00045;
  if (LINE) {
    col = mix(col, LC, (lineM(dt, olw, pxr * 1.2) * .85 + lineM(dc, olw, pxr * 1.2) * .65) * uGlass);
  } else if (GHOST) {
    col = mix(col, LC * .85, (lineM(dt, olw, pxr * 1.3) * .55 + lineM(dc, olw, pxr * 1.3) * .45) * uGlass);
    float fx;
    col = mix(col, LC * .85, lineM(combSD(l, fx), .0003, pxr * 1.2) * .30 * uGlass * tc);
  } else {
    col = mix(col, col * .88, lineM(dt + .0002, .0004, pxr) * .30 * uGlass);
  }
  cov = max(cov, max(tc, cc));
}

// R4-02 (R5, src 8.70 at 1080p, rings of R - B round the wires): the original's orange cloud reaches
// 1.5-2 digit heights - the dark gears there read R - B ~27-31 against ~5-23 in R4 - while right
// next to the wires it is less saturated than R4 was (0.5 digit heights 46 vs 57). The energy
// moves out: a wide cloud, hollow round each digit and weaker along the row itself, screened over
// whatever is behind (so the dark gears warm up and the light paper barely changes), with a soft
// knee and no hard cut, so it fades out round; the near tint is weaker. (Also drawn above the
// instrument's box, where the readout pass otherwise leaves the picture alone.)
#define R5_NK .20
#define R5_NC .42
vec3 haloCloud(vec2 r, vec3 col){
  float hw = 0.;
  for (int k = 0; k < 8; k++) {
    if (uLit[k] <= 0.) continue;
    vec2 hd = r - vec2((float(k) - 3.5) * PITCH + DIGIT_DX[k], DIGIT_CY);
    if (k == 1) hd -= vec2(.026, 0.);
    vec2 hf = hd / vec2(.24, .28);
    vec2 hn = hd / vec2(.10, .14);
    hw += uLit[k] * exp(-dot(hf, hf)) * (1. - .7 * exp(-dot(hn, hn))) * (k == 1 ? .25 : 1.);
  }
  float ady = abs(r.y - DIGIT_CY);
  vec3 gw = vec3(.48, .22, .06) * .55 * (1. - exp(-max(hw, 0.) * uHalo / .55)) * mix(.40, 1., smoothstep(.07, .17, ady));
  return 1. - max(1. - col, 0.) * (1. - gw);
}

void main(){
  vec2 s = (vUv - .5) * vec2(uAspect, 1.);
  vec3 col = texture(uScene, vUv).rgb;
  // instrument-only pincushion (T9 / tunnel): screen R = r (1 + k r^n) about uWarp.xy
  if (uWarp.z > 0.) {
    vec2 d = s - uWarp.xy;
    float R = length(d);
    if (R > 1e-5) {
      float k = uWarp.z, n = uWarp.w;
      float r = R / (1. + k * pow(R, n));
      for (int i = 0; i < 5; i++) {
        float rn = pow(r, n);
        r -= (r + k * rn * r - R) / (1. + (n + 1.) * k * rn);
        r = max(r, 0.);
      }
      float m = k * pow(r, n);
      gWarpPx = 1. / (1. + .5 * (n + 2.) * m);
      s = uWarp.xy + d * (r / R);
      // the tube wall: things out at the sides are nearer, so they also grow in height
      if (uWarpY > 0.) {
        vec2 d2 = s - uWarp.xy;
        float gy = 1. + uWarpY * pow(abs(d2.x), 4.);
        s.y = uWarp.y + d2.y / gy;
        gWarpPx /= sqrt(gy);
      }
      if (uWarpB != vec3(0.)) {
        // R3-06 (R4): sideways from the warp centre, in tube pitches: t5 .18, t6 .31, t7 .43, t0 -.43
        float lx = (s.x - uWarp.x) / uRO.x;
        s.y -= uWarpB.x * smoothstep(.215, .27, lx) + uWarpB.y * smoothstep(.335, .40, lx)
             - uWarpB.z * smoothstep(.22, .39, -lx);
      }
    }
  }
  float sc = uRO.x;
  vec2 q = s - ANCHOR - uRO.yz;
  bool P = uView.z > 0.;
  vec3 ro, rd;
  if (P) {
    float Dz = 1. / uView.z;
    float cy = cos(uView.x), sy = sin(uView.x), cp = cos(uView.y), sp = sin(uView.y);
    vec3 ax = vec3(cy, 0., -sy);
    vec3 ay = vec3(sy * sp, cp, cy * sp);
    vec3 an = cross(ax, ay);
    vec3 D = vec3(q, Dz);
    ro = -Dz * vec3(ax.z, ay.z, an.z);
    rd = vec3(dot(D, ax), dot(D, ay), dot(D, an));
  } else {
    ro = vec3(q, -1.);
    rd = vec3(0., 0., 1.);
  }
  mat2 Rr = rot2(-uRO.w);
  ro.xy = Rr * ro.xy;
  rd.xy = Rr * rd.xy;
  ro /= sc;
  rd /= sc;
  float t0 = -ro.z / rd.z;
  vec2 r = ro.xy + t0 * rd.xy;
  if (r.x < -.9 || r.x > .9 || r.y < -.5 || r.y > .33) {
    if (uHalo > 0. && uMode > .5 && uMode < 1.5) col = haloCloud(r, col);
    o = vec4(col, 1.);
    return;
  }
  bool LINE = uMode > 1.5;
  bool GHOST = uMode > .5 && uMode < 1.5;
  vec3 LC = lineColor();
  float caseK = LINE ? .78 : (GHOST ? .12 : 1.);
  // T9 (R1-16): the case itself is not drawn, only the board on it
  float caseOn = 1. - step(.5, uBoardOnly);

  float top = PLINTH_C.y + PLINTH_H.y;
  float pB = PLINTH_C.y - PLINTH_H.y;
  float W = PLINTH_H.x;
  float topP = top + PCB_T;

  // contact shadow on the paper below the case
  float under = smoothstep(W + .03, W - .02, abs(r.x)) * exp(-max(pB - .006 - r.y, 0.) / .02) * step(r.y, pB + .002);
  col *= 1. - .30 * under * uPlinth * (1. - uEnvDark) * caseK;

  bool seeTop = P && ro.y > top;
  bool seePcb = P && ro.y > topP;
  bool seeBot = P && ro.y < pB;
  bool seeR = P && ro.x > W;
  bool seeL = P && ro.x < -W;
  vec3 cc = vec3(0.);
  float ca = 0.;
  float tCase = 1e9;
#define LAYER(C_, A_, T_) { float a_ = (A_); cc = cc * (1. - a_) + (C_) * a_; ca = ca * (1. - a_) + a_; if (a_ > .02) tCase = min(tCase, (T_)); }

  float tf = (-CASE_D - ro.z) / rd.z;
  vec2 pf = ro.xy + tf * rd.xy;
  float pxf = pxAt(tf);
  float feet = min(sdRoundBox(pf - vec2(-.515, pB - .0045), vec2(.032, .0045), .002),
                   sdRoundBox(pf - vec2(.515, pB - .0045), vec2(.032, .0045), .002));
  LAYER(vec3(.012), (1. - smoothstep(-pxf * .8, pxf * .8, feet)) * uPlinth * caseK * caseOn, tf);

  float sf = sdRoundBox(pf - PLINTH_C, PLINTH_H, PLINTH_R);
  if (seeTop || seeBot || seeR || seeL) {
    float e2 = 2. * pxf;
    sf = max(max(pf.y - top - (seeTop ? e2 : 0.), pB - pf.y - (seeBot ? e2 : 0.)),
             max(pf.x - W - (seeR ? e2 : 0.), -W - pf.x - (seeL ? e2 : 0.)));
  }
  float af = (1. - smoothstep(-pxf * .8, pxf * .8, sf)) * uPlinth * caseOn;
  if (af > 0.) {
    float fy = (pf.y - pB) / (2. * PLINTH_H.y);
    vec3 pcol = vec3(.009, .009, .011) + vec3(.008, .008, .010) * fy;
    float bev = exp(-pow((top - pf.y - .0030) / .0018, 2.));
    pcol += vec3(.10, .11, .13) * bev * .5 + vec3(1., .45, .10) * bev * uGlowAvg * .18;
    float side = exp(-pow((abs(pf.x) - (W - .0025)) / .0022, 2.));
    pcol += vec3(.07, .08, .10) * side * (.3 + .5 * fy) * (pf.x < 0. ? 1.3 : .6);
    pcol += vec3(.018, .019, .022) * exp(-pow((fy - .62) / .25, 2.)) * (.6 + .4 * cos((pf.x + .2) / W * 1.6));
    pcol += (hash12(floor(pf * vec2(1100., 380.))) - .5) * .003;
    // navy-black front with a thin cool rim light on its left and bottom edges (src 11.56)
    pcol += vec3(.004, .006, .014);
    float rimL = exp(-pow((pf.x + W - .0022) / .0016, 2.)) * smoothstep(pB, pB + .02, pf.y);
    float rimB = exp(-pow((pf.y - pB - .0022) / .0016, 2.)) * smoothstep(W, W * .2, abs(pf.x + W * .25));
    pcol += vec3(.20, .28, .46) * (rimL * .55 + rimB * .4) * (1. - uEnvDark * .5);
    if (LINE) pcol = col * .12 + vec3(.004);
    LAYER(pcol, af * caseK, tf);
  }
  if (LINE || GHOST) {
    float ol = lineM(sdRoundBox(pf - PLINTH_C, PLINTH_H, PLINTH_R), .0006, pxf * 1.4) * uPlinth * caseOn;
    LAYER(LC * (LINE ? 1. : .5) + vec3(.0), ol * (LINE ? .9 : .5), tf);
  }

  if ((seeR || seeL) && !GHOST) {
    float xs = seeR ? W : -W;
    float te = (xs - ro.x) / rd.x;
    vec3 pe = ro + te * rd;
    float pxe = pxAt(te);
    float se = te > 0. ? max(abs(pe.z) - CASE_D, max(pe.y - top - (seeTop ? 2. * pxe : 0.), pB - pe.y - (seeBot ? 2. * pxe : 0.))) : 1.;
    float fwe = max(min(fwidth(se), pxe * 3. / max(abs(rd.x) / length(rd), .08)), 1e-5);
    float ae = (1. - smoothstep(-fwe * .8, fwe * .8, se)) * uPlinth * caseOn;
    if (ae > 0.) {
      float fy = (pe.y - pB) / (top - pB);
      vec3 ecol = vec3(.006) + vec3(.03, .032, .036) * exp(-pow((fy - .58) / .26, 2.));
      if (LINE) ecol = col * .1;
      LAYER(ecol, ae * caseK, te);
    }
  }
  if (seeBot && !GHOST) {
    float tb = (pB - ro.y) / rd.y;
    vec3 pb = ro + tb * rd;
    float sb = tb > 0. ? max(abs(pb.x) - W, abs(pb.z) - CASE_D) : 1.;
    float fwb = max(min(fwidth(sb), pxAt(tb) * 3. / max(abs(rd.y) / length(rd), .08)), 1e-5);
    LAYER(LINE ? col * .1 : vec3(.005), (1. - smoothstep(-fwb * .8, fwb * .8, sb)) * uPlinth * caseK * caseOn, tb);
  }
  if (seeTop && !GHOST) {
    float tt = (top - ro.y) / rd.y;
    vec3 pt = ro + tt * rd;
    float st = tt > 0. ? max(abs(pt.x) - W, abs(pt.z) - CASE_D) : 1.;
    float fwt = max(min(fwidth(st), pxAt(tt) * 3. / max(abs(rd.y) / length(rd), .08)), 1e-5);
    float at = (1. - smoothstep(-fwt * .8, fwt * .8, st)) * uPlinth * caseOn;
    if (at > 0.) {
      float fe = exp(-pow((pt.z + CASE_D) / .0035, 2.));
      vec3 tcol = vec3(.012) + vec3(.10, .11, .12) * fe * .4;
      if (LINE) tcol = col * .1 + LC * fe * .3;
      LAYER(tcol, at * caseK, tt);
    }
  }

  // circuit board on the case: a thin brown strip at the front, its top from above
  float zP = -CASE_D + PCB_IN;
  float WP = W - .008;
  float tpf = (zP - ro.z) / rd.z;
  vec2 pp = ro.xy + tpf * rd.xy;
  float pxp = pxAt(tpf);
  float spf = max(abs(pp.x) - WP, max(top - pp.y, pp.y - topP - (seePcb ? 2. * pxp : 0.)));
  float apf = (1. - smoothstep(-pxp * .8, pxp * .8, spf)) * uPlinth;
  if (apf > 0.) {
    int bi = int(clamp(floor(pp.x / PITCH + 4.), 0., 7.));
    float litP = uLit[bi] * uGlow;
    float lpx = pp.x - (float(bi) - 3.5) * PITCH;
    vec3 b = vec3(.34, .20, .09) * mix(1., .45, uEnvDark) * (.82 + .18 * vnoise(vec2(pp.x * 900., 0.)));
    b += vec3(.30, .22, .12) * exp(-pow((pp.y - (topP - .0008)) / .0007, 2.)) * .6;
    b += vec3(.55, .45, .25) * step(.85, hash12(floor(pp * vec2(260., 900.)))) * .25;
    b += vec3(1., .45, .10) * exp(-pow(lpx / .04, 2.)) * litP * .18;
    if (LINE) b = col * .1 + LC * (caseOn > .5 ? .25 : .55);
    LAYER(b, apf * (GHOST ? .2 : caseK), tpf);
  }
  if (seePcb && !GHOST) {
    float tq = (topP - ro.y) / rd.y;
    vec3 pq = ro + tq * rd;
    float sq = tq > 0. ? max(abs(pq.x) - WP, abs(pq.z) - (CASE_D - PCB_IN)) : 1.;
    float fwq = max(min(fwidth(sq), pxAt(tq) * 3. / max(abs(rd.y) / length(rd), .08)), 1e-5);
    float aq = (1. - smoothstep(-fwq * .8, fwq * .8, sq)) * uPlinth;
    if (aq > 0.) {
      vec2 bz = pq.xz;
      int bi = int(clamp(floor(bz.x / PITCH + 4.), 0., 7.));
      float bx = bz.x - (float(bi) - 3.5) * PITCH;
      vec3 pc = vec3(.26, .15, .07) * mix(1., .5, uEnvDark);
      float lane = abs(fract(bz.y * 38. + .5 * step(.5, fract(bz.x * 2.7))) - .5);
      pc += vec3(.10, .07, .03) * (1. - smoothstep(.07, .12, lane)) * step(.42, vnoise(bz * vec2(11., 37.)));
      pc += vec3(1., .42, .09) * exp(-dot(vec2(bx, bz.y), vec2(bx, bz.y)) / .0032) * uLit[bi] * uGlow * .35;
      if (LINE) pc = col * .1;
      LAYER(pc, aq * caseK, tq);
    }
  }

  // parts standing on the front edge of the board, in front of the sockets (as measured on the
  // original's lock frame): teal terminal block + white connector at the left end, two TO-92s
  // at tube 3, eight tall black blocks with gold pins under tubes 6-7, a small green board and a
  // TO-92 at the right end, screws at both ends.
  float zF = -.058;
  float tFp = (zF - ro.z) / rd.z;
  vec2 rF = ro.xy + tFp * rd.xy;
  if (rF.y > top - .002 && rF.y < top + .06 && uPlinth > 0.) {
    float pxc = pxAt(tFp);
    float sd = 1e3;
    vec3 pc2 = vec3(.02);
    vec2 q0 = rF - vec2(0., topP);
    // R3 (src 11.60 at 1080p): a plain teal terminal block (405-470 px), then a black rounded
    // part (470-505 px) with a small white connector in front of it (490-512 px, 597-611 px)
    float tb = sdRoundBox(q0 - vec2(-.486, .0135), vec2(.031, .0135), .002);
    if (tb < sd) { sd = tb; pc2 = vec3(.10, .26, .27) * (.70 + .30 * smoothstep(-.012, .012, q0.y - .0135)); }
    float cap = sdRoundBox(q0 - vec2(-.4385, .0115), vec2(.0165, .0115), .007);
    if (cap < sd) { sd = cap; float cy2 = (q0.y - .0115) / .0115; pc2 = vec3(.03) + vec3(.10) * exp(-pow((q0.x + .447) / .004, 2.)) * (1. - cy2 * cy2); }
    float wcn = sdRoundBox(q0 - vec2(-.425, .0080), vec2(.0098, .0068), .0015);
    if (wcn < sd) { sd = wcn; pc2 = vec3(.86, .85, .82) * (.75 + .25 * smoothstep(-.007, .006, q0.y - .008)); }
    for (int k = 0; k < 3; k++) {
      vec2 c = vec2(k == 0 ? -.240 : (k == 1 ? -.158 : .508), .0175);
      float tr = sdRoundBox(q0 - c, vec2(.0135, .0135), .005);
      tr = max(tr, -(q0.y - .004));
      if (tr < sd) { sd = tr; pc2 = vec3(.014) + vec3(.10) * exp(-pow((q0.x - c.x + .006) / .002, 2.)); }
      for (int m = -1; m <= 1; m++) {
        float lg = sdSeg(q0, vec2(c.x + float(m) * .0065, .0), vec2(c.x + float(m) * .0065, .0045), .0008, .0008);
        if (lg < sd) { sd = lg; pc2 = vec3(.62, .60, .55); }
      }
    }
    for (int k = 0; k < 8; k++) {
      float cx = .1333 + .0278 * float(k);
      float tp = sdRoundBox(q0 - vec2(cx, .024), vec2(.0112, .023), .0012);
      if (tp < sd) { sd = tp; pc2 = vec3(.016) + vec3(.08) * exp(-pow((q0.x - cx + .007) / .0022, 2.)) + vec3(.05) * exp(-pow((q0.y - .046) / .0012, 2.)); }
      float pin = sdSeg(q0, vec2(cx, .003), vec2(cx, .021), .0034, .0024);
      if (pin < sd) { sd = pin; float u = (q0.x - cx) / .0034; pc2 = vec3(.62, .46, .20) * (.55 + .45 * (1. - u * u)) + vec3(.35, .30, .18) * exp(-pow((u + .35) / .25, 2.)); }
    }
    float gb = sdRoundBox(q0 - vec2(.44, .0045), vec2(.051, .0045), .001);
    if (gb < sd) { sd = gb; pc2 = vec3(.08, .27, .14) * (.8 + .2 * step(.5, fract(q0.x * 160.))); }
    float sp = sdRoundBox(q0 - vec2(.546, .006), vec2(.0095, .006), .0015);
    if (sp < sd) { sd = sp; pc2 = vec3(.02); }
    float scr = length(q0 - vec2(sign(rF.x) * .568, .0035)) - .0055;
    if (scr < sd) { sd = scr; pc2 = vec3(.03) + vec3(.12) * exp(-pow(length(q0 - vec2(sign(rF.x) * .566, .005)) / .002, 2.)); }
    pc2 *= mix(1., .45, uEnvDark);
    pc2 += vec3(1., .45, .12) * uGlowAvg * .03;
    if (LINE) pc2 = col * .1 + LC * .2;
    float cc2 = (1. - smoothstep(-pxc * .8, pxc * .8, sd)) * uPlinth * (GHOST ? .2 : caseK);
    LAYER(pc2, cc2, tFp);
  }
#undef LAYER

  int j = int(clamp(floor(r.x / PITCH + 4.), 0., 7.));
  int idx[3];
  vec4 cyl[3];
  float on[3];
  float tTube = 1e9;
  float halo = 0., spill = 0.;
  for (int k = 0; k < 3; k++) {
    int i = j + k - 1;
    idx[k] = i;
    cyl[k] = vec4(0.);
    on[k] = 0.;
    if (i < 0 || i > 7) continue;
    vec4 c3 = cylCoords(ro, rd, (float(i) - 3.5) * PITCH);
    cyl[k] = c3;
    if (min(tubeSD(c3.xy), socketSD(c3.xw)) < pxAt(c3.z) * 3.) { on[k] = 1.; tTube = min(tTube, c3.z); }
    if (!LINE && !GHOST) {
      // the paper right next to the glass is a little darker (the tube shades it), and the lit
      // digits throw a warm glow on it (measured: ~(239,208,164) beside a lit tube vs a neutral
      // ~(241,233,228) above the row)
      float dOut = envSD(c3.xy);
      halo = max(halo, step(0., dOut) * exp(-dOut / .0045));
      vec2 gq = (r - vec2((float(i) - 3.5) * PITCH, .075)) / vec2(.085, .16);
      spill += uLit[i] * (i == 1 ? .2 : 1.) * exp(-dot(gq, gq));
    }
  }
  col *= 1. - .09 * halo * uGlass;
  col += col * vec3(.07, -.035, -.17) * min(spill, 1.6) * min(uGlow, 1.3) * (1. - uEnvDark) * uGlass;
  // R1-14 (R4, src 8.70 at 1080p): past the tight halo an orange cloud stains whatever is behind -
  // the dark gears near a lit digit read (109,77,62) at 0.4-0.6 digit heights from the wires and
  // (72,58,52) at 0.8-1.0, against (62,54,49) / (49,48,51) without it - while the light paper
  // barely changes (a screen blend). R4-02 (R5): wider and taller, see haloCloud
  if (GHOST && uHalo > 0.) col = haloCloud(r, col);
  if (GHOST && uHalo > 0. && abs(r.y - DIGIT_CY) < .30) {
    // R1-14 (R3, src 8.50-8.90 at 1080p): each lit digit burns in an orange bloom - bright
    // yellow-orange along the wires (out to ~1/4 of the digit height), then a soft warm glow
    // reaching about one digit height that tints the paper and the gears behind. The atlas
    // margin is too small for that radius, so the wire glow is a disc of blurred atlas samples.
    float hs = 0., ws = 0., wo = 0.;
    for (int k = 0; k < 8; k++) {
      if (uLit[k] <= 0.) continue;
      vec2 hd = r - vec2((float(k) - 3.5) * PITCH + DIGIT_DX[k], DIGIT_CY);
      // (the decimal point sits low and to the right in its tube, as in tubeLayer)
      if (k == 1) hd -= vec2(.026, 0.);
      vec2 he = hd / vec2(.072, .105);
      hs += uLit[k] * exp(-dot(he, he)) * (k == 1 ? .25 : 1.);
      if (abs(hd.x) > .095 || abs(hd.y) > .15) continue;
      // (src 8.70: the bright yellow body round each wire is ~4-13 px wide, up to ~45 px where
      // wires meet; R-B falls from ~117 at the wire to the paper's ~35 by ~0.6 digit heights)
      float ai = haloA(hd, k) * 2., ao = 0.;
      for (int j = 0; j < 10; j++) {
        float an = float(j) * .6283 + .3;
        vec2 o1 = vec2(cos(an), sin(an));
        ai += haloA(hd + o1 * .0065, k);
        ao += haloA(hd + o1 * .015, k) + haloA(hd + o1 * .028, k) * .6;
      }
      ws += uLit[k] * ai / 12.;
      wo += uLit[k] * ao / 16.;
    }
    // (R4-02, R5: the near tint is weaker than in R4 - .45 / .62)
    float hk = clamp(hs * uHalo, 0., 1.2);
    col = mix(col, col * vec3(1.06, .78, .46) + vec3(.55, .27, .03), clamp(hk * R5_NK, 0., R5_NC));
    col = mix(col, vec3(1., .60, .13), clamp(wo * 2.4 * uHalo, 0., 1.) * .72);
    col = mix(col, vec3(1.22, .93, .38), clamp(ws * 3.2 * uHalo, 0., 1.) * .92);
  }
  float tm = (-.012 - ro.z) / rd.z;
  vec2 rm = ro.xy + tm * rd.xy;
  bool caseAfter = ca > 0. && tCase < tTube;
  float cov = 0.;
  if (!caseAfter) { col = col * (1. - ca) + cc; cov = ca; }
  for (int pass = 0; pass < 3; pass++) {
    int kb = -1;
    float tb = -1e9;
    for (int k = 0; k < 3; k++) if (on[k] > 0. && cyl[k].z > tb) { tb = cyl[k].z; kb = k; }
    if (kb < 0) break;
    on[kb] = 0.;
    int i = idx[kb];
    float xi = (float(i) - 3.5) * PITCH;
    tubeLayer(i, cyl[kb].xy, cyl[kb].w, r - vec2(xi, 0.), rm - vec2(xi, 0.), pxAt(cyl[kb].z), col, cov);
  }
  if (caseAfter) col = col * (1. - ca) + cc;
  o = vec4(col, 1.);
}`;
