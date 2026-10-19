import { COMMON } from './common';
import { GEOM_DEFINES } from '../show/geom';

// Foreground smoke that gathers into the case (T4), drawn over the instrument.
export const SMOKE_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform float uAspect;
uniform float uK;
uniform float uA;
uniform float uTime;
uniform vec4 uRO;
${COMMON}
${GEOM_DEFINES}
float ridged(vec2 p){
  float a = .5, r = 0.;
  for (int i = 0; i < 5; i++) {
    float n = 1. - abs(vnoise(p) * 2. - 1.);
    r += a * n * n;
    p = rot2(.7) * p * 2.03 + 1.7;
    a *= .5;
  }
  return r;
}
// The overgrowth collapses into a murky dark mass that turns into branching black ink and
// condenses into the instrument case.
void main(){
  vec2 s = (vUv - .5) * vec2(uAspect, 1.);
  vec2 l = (s - uRO.yz) / uRO.x;
  float k = smoothstep(0., 1., uK);
  float e0 = length((l - vec2(0., -.03)) / vec2(.82, .42)) - 1.;
  float e1 = sdBox(l - PLINTH_C, PLINTH_H + vec2(.02, .012)) / .22;
  float env = mix(e0, e1, k);
  env += (fbm(l * 4. + vec2(0., -uTime * .5)) - .5) * mix(.75, .35, k);
  float inside = smoothstep(.2, -.15, env);
  // early murky mass: dark foliage debris with light speckles
  // R1-08 (R3, src 6.30-6.40): grainy, speckled - light flecks of the old foliage all through
  // the dark mass, mottled at several scales
  float mass = inside * (.72 + .5 * (fbm(l * 11. + 3.) - .5));
  mass *= .62 + .38 * smoothstep(.30, .62, fbm(l * vec2(16., 22.) + 5.1));
  float sp = hash12(floor(gl_FragCoord.xy * .45) + floor(uTime * 20.) * 3.7);
  float fleck = smoothstep(.62, .92, sp) * smoothstep(.30, .6, vnoise(l * 30. + 2.));
  // ink tendrils: ridged noise, branching, reaching up between the tubes
  vec2 w = l + vec2(fbm(l * 3. + uTime * .3), fbm(l * 3. - 2.1)) * .12;
  float rd = ridged(w * vec2(12., 15.) + vec2(0., uTime * .4));
  float rd2 = ridged(w * vec2(26., 30.) + 5.3);
  // tendrils reach up between the tubes while condensing, then settle onto the case
  float eT = mix(e0, sdBox(l - vec2(0., PLINTH_C.y + .10), vec2(PLINTH_H.x + .03, PLINTH_H.y + .12)) / .10, smoothstep(.0, .5, k));
  eT = mix(eT, e1, smoothstep(.55, 1., k)) + (fbm(l * 4. + 2.) - .5) * .9;
  float reach = smoothstep(.6, -.2, eT);
  // ink threads: iso-contours of warped noise at two scales (branching, thin)
  // stretched sideways: the threads read as wisps/strands rather than round blobs
  float c1 = 1. - abs(fbm(w * vec2(5., 9.) + vec2(0., uTime * .3)) * 2. - 1.);
  float c2 = 1. - abs(fbm(w * vec2(11., 20.) + 3.7 - vec2(uTime * .2, 0.)) * 2. - 1.);
  // src 6.38-6.53: a dense fibrous black tangle (most of the area round the tubes is ink)
  // R1-08 (src 6.30-6.50): soft dark wisps rising round the tubes (thick soft ridges of warped
  // noise at two scales, drifting upward), granular, densest low down; no hard contour lines
  float w1 = 1. - abs(fbm(w * vec2(6., 8.) + vec2(0., -uTime * .6)) * 2. - 1.);
  float w2 = 1. - abs(fbm(w * vec2(13., 17.) + 3.7 + vec2(0., -uTime * .9)) * 2. - 1.);
  float gran = .72 + .28 * vnoise(gl_FragCoord.xy * .9);
  float w3 = 1. - abs(fbm(w * vec2(24., 30.) + 8.1 + vec2(0., -uTime * 1.2)) * 2. - 1.);
  float wisp = smoothstep(.70, .95, w1) * .75 + smoothstep(.72, .96, w2) * .6 + smoothstep(.75, .97, w3) * .45;
  // densest under the digits (round the sockets and the case), thin wisps up between the tubes
  float low = 1. - .82 * smoothstep(-.01, .07, l.y) * smoothstep(.27, .20, l.y);
  float ten = reach * (wisp + .06) * gran * low;
  float haze = inside * (.14 + .24 * fbm(l * 7. + uTime * .2)) * (1. - k * .5);
  float fill = inside * smoothstep(.40, .60, rd) * .85 * smoothstep(.75, 1., k);
  ten = max(ten, haze);
  float a = mix(mass, max(ten, fill), smoothstep(.04, .28, k));
  // R2 (src 6.50-6.70): the smoke turns into thin wiry black strands tangled round the sockets
  // and the case, the tangle condenses into the case's own rectangle (a dark crusted block) and
  // clears at 6.80
  float kw = smoothstep(2.58, 2.76, uTime);
  vec2 bc = vec2(0., mix(-.07, PLINTH_C.y + .01, kw));
  vec2 bh = vec2(mix(.66, PLINTH_H.x + .02, kw), mix(.15, PLINTH_H.y + .03, kw));
  float rag = (fbm(l * vec2(7., 4.) + 4.1) - .5) * mix(.16, .05, kw) + (vnoise(l * 22.) - .5) * .05;
  float band = smoothstep(.02, -.03, sdBox(l - bc, bh) + rag);
  // R1-08 (R3, src 6.40-6.62): ink in water - soft dark billows with fibrous edges (6.45-6.50)
  // that draw out into thick branching root-like strands, mostly horizontal (6.55-6.62); the
  // strands taper and end (no closed net), their edges stay soft
  vec2 wq = l + vec2(fbm(l * vec2(3., 5.) + 1.3 + vec2(uTime * .2, 0.)), fbm(l * vec2(3., 5.) - 4.2)) * vec2(.07, .05);
  wq += vec2(fbm(wq * 11. + 2.), fbm(wq * 11. + 7.)) * .018 + vec2(0., -uTime * .03);
  #define N2(p) (vnoise(p) * .65 + vnoise((p) * 2.1 + 3.1) * .35)
  float pxl = max(length(fwidth(l)), 1e-6);
  float bil = fbm(wq * vec2(6., 9.) + 8.2 + vec2(0., -uTime * .2));
  float fibr = fbm(rot2(.2) * wq * vec2(10., 42.) + 3.1);
  // R3-04 (R4, src 6.47-6.55): while the fibres curl up, the billows' fibrous grain runs upward
  // too (not smeared sideways)
  float kv = smoothstep(2.50, 2.55, uTime) * (1. - smoothstep(2.60, 2.66, uTime));
  fibr = mix(fibr, fbm(rot2(-.12) * wq * vec2(40., 11.) + 3.1), kv * .6);
  float cloud = smoothstep(.38, .58, bil + (fibr - .5) * .45);
  cloud *= .62 + .38 * smoothstep(.35, .7, fibr);
  float g1 = N2(wq * vec2(6., 14.) + .3);
  float g2 = N2(rot2(.45) * wq * vec2(12., 22.) + 7.7);
  float g3 = N2(rot2(-.5) * wq * vec2(22., 34.) + 2.9);
  float g4 = N2(rot2(1.2) * wq * vec2(16., 24.) + 5.3);
  // fibrous, ragged edges
  float hair = (vnoise(wq * vec2(70., 160.)) - .5) * .0035;
  float d1 = abs(g1 - .5) / max(fwidth(g1), 1e-5) * pxl + hair;
  float d2 = abs(g2 - .5) / max(fwidth(g2), 1e-5) * pxl + hair * .7;
  float d3 = abs(g3 - .5) / max(fwidth(g3), 1e-5) * pxl;
  float d4 = abs(g4 - .5) / max(fwidth(g4), 1e-5) * pxl + hair * .5;
  float kr = smoothstep(2.57, 2.66, uTime);
  float ramp = pxl * mix(9., 3.2, kr);
  float wd1 = mix(.0030, .0125, smoothstep(.3, .8, vnoise(wq * vec2(3., 7.) + 1.)));
  float wd2 = mix(.0018, .0065, smoothstep(.3, .8, vnoise(wq * vec2(5., 11.) + 6.)));
  float wd3 = mix(.0007, .0022, vnoise(wq * vec2(9., 19.) + 2.));
  float S1 = (1. - smoothstep(wd1 * .5, wd1 * .5 + ramp, d1)) * smoothstep(.16, .40, vnoise(wq * vec2(2.5, 6.) + 4.));
  float S2 = (1. - smoothstep(wd2 * .5, wd2 * .5 + ramp, d2)) * smoothstep(.18, .42, vnoise(wq * vec2(4., 9.) + 9.));
  float S3 = (1. - smoothstep(wd3 * .5, wd3 * .5 + ramp * .7, d3)) * smoothstep(.14, .38, vnoise(wq * vec2(7., 15.) + 5.));
  float S4 = (1. - smoothstep(wd2 * .4, wd2 * .4 + ramp, d4)) * smoothstep(.24, .48, vnoise(wq * vec2(5., 8.) + 1.5));
  float roots = max(max(S1, S4 * .9), max(S2 * .95, S3 * .8)) * (.82 + .18 * fibr);
  // dense dark clumps where the strands bunch up
  roots = max(roots, smoothstep(.62, .78, bil) * .9);
  float strands = mix(cloud, max(roots, cloud * .6), kr);
  strands *= .74 + .26 * smoothstep(.30, .60, fbm(l * vec2(3.5, 5.) + 5.7));
  // (src 6.55-6.62: the strands are solid black in their cores)
  strands = min(1., strands * mix(1., 1.3, kr));
  float aWire = band * strands;
  // R3-04 (R4, src 6.47-6.55 at 1080p): out of the billows, bundles of thin black fibres curl up
  // round and between the tubes (mostly vertical), before the strands draw out sideways (6.55+)
  float kf = smoothstep(2.50, 2.555, uTime) * (1. - smoothstep(2.605, 2.665, uTime));
  if (kf > 0.) {
    float fib = 0.;
    float grow = smoothstep(2.50, 2.585, uTime);
    for (int i = 0; i < 14; i++) {
      float fi = float(i);
      float h1 = hash11(fi * 3.17 + .5), h2 = hash11(fi * 7.31 + 1.7), h3 = hash11(fi * 1.93 + 4.1), h4 = hash11(fi * 5.03 + 2.9);
      float x0 = -.62 + 1.24 * (fi + .5 + .6 * (h1 - .5)) / 14.;
      float y0 = .00 + .05 * h2;
      float len = (.09 + .13 * h3) * (.35 + .65 * grow);
      float u = (l.y - y0) / len;
      if (u < -.05 || u > 1.05 || abs(l.x - x0) > .06) continue;
      float uc = clamp(u, 0., 1.);
      // 2-3 strands per bundle, curling more toward the tip
      for (int j = 0; j < 3; j++) {
        float fj = float(j);
        if (j == 2 && h4 < .4) break;
        float ph = fi * 2.1 + fj * 1.9 + uTime * (3. + 2. * h4);
        float xc = x0 + (fj - 1.) * (.004 + .004 * h2) * (1. - .5 * uc)
                 + (.010 + .014 * h4) * sin(uc * (5. + 4. * h1) + ph) * uc
                 + (h3 - .5) * .05 * uc * uc;
        // ragged: the width wavers, the strands fray and break toward the tip
        float wv = .65 + .7 * vnoise(vec2(l.y * 90. + fi * 5.1, fj * 3.3));
        float wdt = mix(.0032, .0011, uc) * (1. - .25 * fj) * wv;
        xc += (fj - 1.) * .010 * uc * uc * (.5 + h1);
        float d = abs(l.x - xc) - wdt;
        float gap = smoothstep(.18, .34, vnoise(vec2(l.y * 55. + fi * 9.7 + fj * 4.1, 2.)) + .25 * (1. - uc));
        float on = smoothstep(-.05, .06, u) * (1. - smoothstep(.72, 1.0, u)) * gap;
        fib = max(fib, (1. - smoothstep(-pxl * 1.2, pxl * 1.6, d)) * on * (.85 - .15 * fj));
      }
    }
    aWire = max(aWire, fib * kf);
  }
  float blockE = sdBox(l - PLINTH_C, PLINTH_H) + (fbm(l * 30.) - .5) * .016;
  float block = 1. - smoothstep(-.002, .002, blockE);
  float crust = fbm(l * vec2(70., 52.) + 3.3);
  float aBlock = block * (.86 + .14 * crust);
  float kb = smoothstep(2.73, 2.79, uTime);
  a = mix(a, aWire, smoothstep(2.50, 2.60, uTime));
  a = max(a * (1. - kb * (1. - smoothstep(.0, .06, -sdBox(l - PLINTH_C, PLINTH_H + vec2(.0, .03))) * .0)) * (1. - .75 * kb), aBlock * kb);
  a = clamp(a, 0., 1.) * uA;
  vec3 c = mix(vec3(.085, .09, .05), vec3(.02, .018, .016), smoothstep(.0, .45, k));
  // the mass is flecked with pale bits of the old foliage (src 6.30-6.40)
  c = mix(c, vec3(.60, .58, .48), fleck * (1. - smoothstep(.04, .28, k)));
  // the crust: lighter grey-brown flecks on the dark block
  float crust2 = vnoise(l * vec2(260., 210.)) * .5 + vnoise(l * vec2(90., 70.) + 1.7) * .5;
  // stone-like: grey blotches and fine flecks on the dark block (src 6.70)
  float blot = smoothstep(.48, .78, fbm(l * vec2(26., 19.) + 8.8));
  c = mix(c, vec3(.27, .26, .23), (blot * .65 + smoothstep(.56, .80, crust) * smoothstep(.45, .75, crust2) * .6) * block * kb);
  o = vec4(c * a, a);
}`;

// Thin ribbons drawn as triangle strips built on the CPU (cables).
export const RIBBON_VS = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec4 aV; // x y across shade
uniform float uAspect;
out vec2 vS;
void main(){
  vS = aV.zw;
  gl_Position = vec4(aV.x / (uAspect * .5), aV.y / .5, 0., 1.);
}`;

export const RIBBON_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vS;
out vec4 o;
uniform float uAlpha;
void main(){
  float a = abs(vS.x);
  float fw = max(fwidth(vS.x), 1e-4);
  float cov = 1. - smoothstep(1. - fw * 1.5, 1. + fw * .5, a);
  float hl = exp(-pow((vS.x + .38) / .26, 2.)) * (.22 + .18 * vS.y);
  // black insulated wire, a soft round highlight (no bright stripes)
  vec3 c = vec3(.010) + vec3(.26, .245, .22) * hl;
  float al = cov * uAlpha;
  o = vec4(c * al, al);
}`;

// Exit pieces: paper blobs cut from the snapshot (their union is the paper), black shards,
// radial capsules and orange bokeh. One instanced quad each.
export const PIECE_VS = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec2 aCorner;
layout(location = 1) in vec4 aA; // cx cy radius elong
layout(location = 2) in vec4 aB; // angle kind alpha blur
layout(location = 3) in vec4 aC; // homeX homeY tint seed
uniform float uAspect;
out vec2 vL;
out vec4 vA;
out vec4 vB;
out vec4 vC;
void main(){
  float r = aA.z;
  float L = r * max(aA.w - 1., 0.);
  // the torn / cusped ink pieces (kind 1) are squashed along a random axis (x * .75..1.25), so
  // their outline reaches 1.33 r (torn) or 1.67 r (cusped, base radius 1.25 r): the quad must
  // cover that or the piece is cut off with straight edges
  float ext = aB.y > .5 && aB.y < 1.5 ? (aC.y > 0. ? 1.75 : 1.4) : 1.;
  vec2 hh = vec2(L + r, r) * ext + aB.w * 1.5 + r * .05 + .002;
  vec2 lp = aCorner * hh;
  float c = cos(aB.x), s = sin(aB.x);
  vec2 p = aA.xy + mat2(c, s, -s, c) * lp;
  vL = lp; vA = aA; vB = aB; vC = aC;
  gl_Position = vec4(p.x / (uAspect * .5), p.y / .5, 0., 1.);
}`;

export const PIECE_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vL;
in vec4 vA;
in vec4 vB;
in vec4 vC;
out vec4 o;
uniform sampler2D uSnap;
uniform float uAspect;
uniform float uPx;
${COMMON}
void main(){
  float r = vA.z;
  float L = r * max(vA.w - 1., 0.);
  float kind = vB.y;
  vec2 lp = vL;
  float d;
  if (kind > .5 && kind < 1.5) {
    // R1-21: a torn ink piece - the black left between round paper pieces: a few uneven arms
    // between round bites, the edge roughened (src 13.10-13.20)
    vec2 p = lp / r;
    float seed = vC.x;
    // uneven: squashed along a random axis, 3-4 cusped arms
    p = rot2(hash11(seed * 2.3) * 6.28) * p;
    p.x *= .75 + .5 * hash11(seed * 4.9);
    float N = 3. + floor(hash11(seed * 7.1) * 2.);
    float an = atan(p.y, p.x);
    float rb = .62 + .38 * vnoise(vec2(an * 1.6 + seed * 10., seed));
    float dd = length(p) - rb;
    float bites = 1e3;
    for (int k = 0; k < 5; k++) {
      if (float(k) >= N) break;
      float a = (float(k) + .5 + (hash11(seed * 3.3 + float(k)) - .5) * .55) * 6.2831853 / N;
      float D = 1.0 + .4 * hash11(seed * 5.7 + float(k) * 1.9);
      float R = D - .14 - .12 * hash11(seed * 9.1 + float(k));
      bites = min(bites, length(p - D * vec2(cos(a), sin(a))) - R);
    }
    dd = max(dd, -bites);
    dd += (vnoise(p * 5. + seed * 13.) - .5) * .05;
    if (vC.y > 0.) {
      // the cusped pieces of src 12.98-13.05: smooth, shallow concave sides (big round bites)
      // meeting in sharp points; no convex outline between them
      dd = length(p) - 1.25;
      bites = 1e3;
      for (int k = 0; k < 5; k++) {
        if (float(k) >= N) break;
        float a = (float(k) + .5 + (hash11(seed * 3.3 + float(k)) - .5) * .5) * 6.2831853 / N;
        float D = 1.7 + .5 * hash11(seed * 5.7 + float(k) * 1.9);
        float R = D - (.40 + .18 * hash11(seed * 9.1 + float(k)));
        bites = min(bites, length(p - D * vec2(cos(a), sin(a))) - R);
      }
      dd = max(dd, -bites) + (vnoise(p * 5. + seed * 13.) - .5) * .012;
    }
    d = dd * r;
  } else {
    d = length(vec2(max(abs(lp.x) - L, 0.), lp.y)) - r;
  }
  float aa = uPx + vB.w;
  float cov = 1. - smoothstep(-aa, aa, d);
  if (cov <= .002) discard;
  vec3 col;
  if (kind < .5) {
    float c = cos(vB.x), s = sin(vB.x);
    vec2 off = mat2(c, s, -s, c) * vec2(lp.x / max(vA.w, 1.), lp.y);
    vec2 uv = (vC.xy + off) / vec2(uAspect, 1.) + .5;
    col = texture(uSnap, uv).rgb;
    float k = clamp(-d / r, 0., 1.);
    col *= mix(1., .72 + .28 * smoothstep(0., .55, k), vC.w);
    col = mix(col, vec3(.58, .32, .11), vC.z);
  } else if (kind < 1.5) {
    col = vec3(.012, .011, .010);
  } else {
    float k = clamp(-d / max(vB.w + r * .3, 1e-4), 0., 1.);
    col = mix(vec3(.85, .40, .12), vec3(1., .66, .38), vC.z) * (.86 + .2 * (1. - k));
  }
  float a = cov * vB.z;
  // the lights add up where they overlap (only partly covering what is behind)
  o = vec4(col * a, kind > 1.5 ? a * .6 : a);
}`;
