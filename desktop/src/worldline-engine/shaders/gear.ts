import { COMMON } from './common';

// Instanced gear quads evaluated as SDFs. aE carries the 3D tilt (an ellipse squash along an
// angle) and how thin the rim/spokes are (light watch wheels vs. heavy black gears).
export const GEAR_VS = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec2 aCorner;
layout(location = 1) in vec4 aA; // cx cy R angle  (stage units)
layout(location = 2) in vec4 aB; // teeth depth style seed
layout(location = 3) in vec4 aC; // rgb alpha
layout(location = 4) in vec4 aD; // blur hub metal spokes
layout(location = 5) in vec4 aE; // squash squashAngle thin -
uniform float uAspect;
out vec2 vL;
out vec4 vB;
out vec4 vC;
out vec4 vD;
out vec4 vE;
out float vR;
void main(){
  float pad = 1.03 + aD.x / max(aA.z, 1e-4) * 2.2;
  vec2 l = aCorner * pad;
  float ca = cos(aE.y), sa = sin(aE.y);
  mat2 R = mat2(ca, sa, -sa, ca);
  mat2 Ri = mat2(ca, -sa, sa, ca);
  vec2 m = R * (vec2(1., aE.x) * (Ri * l));
  vec2 sp = aA.xy + m * aA.z;
  float c = cos(aA.w), s = sin(aA.w);
  vL = mat2(c, -s, s, c) * l;
  vB = aB; vC = aC; vD = aD; vE = aE; vR = aA.z * mix(1., aE.x, .5);
  gl_Position = vec4(sp.x / (uAspect * .5), sp.y / .5, 0., 1.);
}`;

export const GEAR_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vL;
in vec4 vB;
in vec4 vC;
in vec4 vD;
in vec4 vE;
in float vR;
out vec4 o;
uniform float uPx;
${COMMON}
float gearSD(vec2 q, float teeth, float depth, float style, float hub, float spokes, float thin){
  float r = length(q);
  float a = atan(q.y, q.x);
  float per = 6.2831853 / teeth;
  float root = 1. - depth;
  float f = (fract(a / per + .5) - .5) * per * r;
  float hb = per * root * .29;
  float ht = per * .16;
  float y = clamp((r - root) / depth, 0., 1.);
  float hw = mix(hb, ht, y);
  float dTooth = max(abs(f) - hw, r - 1.);
  float d = min(r - root, dTooth);
  if (style > 2.5) {
    d = max(d, (root - .085) - r);
    return d;
  }
  d = max(d, hub - r);
  if (style > .5 && style < 1.5) {
    float rimW = mix(.10, .045, thin);
    float rIn = hub + mix(.11, .05, thin), rOut = root - rimW;
    float dWeb = max(rIn - r, r - rOut);
    float sp = 6.2831853 / spokes;
    float dSpoke = abs(fract(a / sp + .5) - .5) * sp * r - mix(.055, .02, thin);
    d = max(d, -max(dWeb, -dSpoke));
  } else if (style > 1.5) {
    float rm = (hub + root) * .5;
    float sp = 6.2831853 / spokes;
    float ah = floor(a / sp + .5) * sp;
    float hr = min((root - hub) * .30, sin(PI / spokes) * rm * .62);
    d = max(d, -(length(q - rm * vec2(cos(ah), sin(ah))) - hr));
  }
  return d;
}
void main(){
  float d = gearSD(vL, vB.x, vB.y, vB.z, vD.y, vD.w, vE.z);
  float ds = d * vR;
  float aa = max(uPx * .9, vD.x);
  float cov = 1. - smoothstep(-aa, aa, ds);
  if (cov <= .002) discard;
  vec3 col = vC.rgb;
  float metal = vD.z;
  if (metal > 0.) {
    float r = length(vL);
    float marks = .5 + .5 * sin(r * 170. + vB.w * 20.);
    col *= .9 + .08 * marks * metal + .18 * (1. - r) * metal;
    float edge = 1. - smoothstep(0., .004 + vD.x, -ds);
    col += vec3(.5, .48, .45) * edge * .25 * metal;
  }
  float a = cov * vC.a;
  o = vec4(col * a, a);
}`;
