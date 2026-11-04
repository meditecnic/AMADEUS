// GLSL for the light cone, ported from the approved mock-up (hiddenBy, uExpand / uExpandT / uCollapsed).
// Everything is lines, points and one translucent cone skin: no textures, no lights. The glow and scan lines
// of β are not here (B3c).

/** Does the segment from the eye `o` to the point `p` pass through the cone's skin first? Then `p` is behind it. */
const HIDDEN_BY = /* glsl */ `
uniform float uHc; uniform float uSlope;
float hiddenBy(vec3 o, vec3 p){
  vec3 d = p - o;
  vec3 q = o - vec3(0., -uHc, 0.);
  float k = uSlope * uSlope;
  float A = d.x * d.x + d.z * d.z - k * d.y * d.y;
  float B = 2. * (q.x * d.x + q.z * d.z - k * q.y * d.y);
  float C = q.x * q.x + q.z * q.z - k * q.y * q.y;
  if (abs(A) < 1e-7) return 0.;
  float disc = B * B - 4. * A * C;
  if (disc < 0.) return 0.;
  float sq = sqrt(disc);
  float h = 0.;
  float t1 = (-B - sq) / (2. * A);
  float t2 = (-B + sq) / (2. * A);
  float y1 = o.y + d.y * t1, y2 = o.y + d.y * t2;
  if (t1 > 1e-3 && t1 < .996 && y1 > -uHc && y1 < .002) h = 1.;
  if (t2 > 1e-3 && t2 < .996 && y2 > -uHc && y2 < .002) h = 1.;
  return h;
}`;

/** Screen-space quads along 3D segments. Flags in iG.y: 1 engraving, 2 hidden-line test, 4 collapsed tick, 8 only for the open group. */
export const LINE_VERT = /* glsl */ `
uniform vec2 uRes; uniform float uPx; uniform float uFlat;
uniform float uFogNear; uniform float uFogFar; uniform float uFogMin;
uniform float uFocus; uniform float uDim; uniform float uFocus2;
uniform vec3 uLight; uniform vec3 uCam;
uniform float uExpand; uniform float uExpandT; uniform float uCollapsed; uniform float uMonthLo; uniform float uMonthHi;
attribute vec3 iA; attribute vec3 iB; attribute vec3 iX;
attribute vec4 iP;
attribute vec2 iR;
attribute vec2 iG;
attribute vec3 iC;
varying float vSide; varying float vHalf; varying float vDist; varying float vKind;
varying float vAlpha; varying float vRev; varying float vWy; varying vec3 vCol; varying float vHi;
varying vec3 vWp; varying float vFlags;
void main(){
  vec3 a = iA; vec3 b = iB;
  float isStroke = mod(floor(iG.y / 4.), 2.);
  float gated = mod(floor(iG.y / 8.), 2.);
  float open = (uExpand > -.5 && abs(iG.x - uExpand) < .5) ? 1. : 0.;
  float keep = 1.;
  if (isStroke > .5) { float k = open * uExpandT; a = mix(a, iX, k); b = mix(b, iX, k); keep = 1. - k; }
  if (gated > .5 && uCollapsed > .5) keep = open * uExpandT;
  a.y *= (1. - uFlat); b.y *= (1. - uFlat);
  vec4 ca = projectionMatrix * viewMatrix * vec4(a, 1.);
  vec4 cb = projectionMatrix * viewMatrix * vec4(b, 1.);
  vec2 sa = ca.xy / ca.w * uRes * .5;
  vec2 sb = cb.xy / cb.w * uRes * .5;
  vec2 d = sb - sa; float L = length(d);
  d = L > 1e-4 ? d / L : vec2(1., 0.);
  vec2 n = vec2(-d.y, d.x);
  float t = position.x; float side = position.y;
  vec4 c = mix(ca, cb, t);
  float hw = max(iP.x, .6) * uPx * .5;
  float w = hw + uPx;
  vec2 off = n * side * w + d * (t * 2. - 1.) * uPx * .5;
  c.xy += off / (uRes * .5) * c.w;
  gl_Position = c;
  vSide = side * w; vHalf = hw;
  vec3 wp = mix(a, b, t);
  vWy = wp.y; vWp = wp; vFlags = iG.y;
  vDist = iP.w + t * length(iB - iA);
  vKind = iP.z; vRev = mix(iR.x, iR.y, t);
  float fog = mix(1., uFogMin, smoothstep(uFogNear, uFogFar, c.w));
  float dim = 1.;
  if (uFocus > -.5 && iG.x > -.5 && abs(iG.x - uFocus) > .5 && abs(iG.x - uFocus2) > .5) dim = uDim;
  vHi = (uFocus > -.5 && (abs(iG.x - uFocus) < .5)) ? 1. : 0.;
  float shade = 1.;
  if (mod(iG.y, 2.) > .5) {
    vec2 rd = normalize(vec2(wp.x, wp.z) + 1e-5);
    vec3 nrm = normalize(vec3(rd.x, -.9, rd.y));
    float lam = max(0., dot(nrm, normalize(uLight)));
    float front = smoothstep(-.15, .25, dot(nrm, normalize(uCam - wp)));
    shade = (.22 + .78 * lam) * mix(.35, 1., front);
  }
  if (isStroke > .5 && uMonthHi > uMonthLo && (wp.y < uMonthLo || wp.y > uMonthHi)) dim = min(dim, .22);
  vAlpha = iP.y * fog * dim * shade * keep * (iP.x < .6 ? iP.x / .6 : 1.);
  vCol = iC;
}`;

export const LINE_FRAG = /* glsl */ `
uniform vec3 uCam; uniform float uFlat;
${HIDDEN_BY}
uniform float uReveal; uniform vec3 uHiCol; uniform float uHiMix;
uniform float uDash; uniform float uOpacity; uniform float uPx;
varying float vSide; varying float vHalf; varying float vDist; varying float vKind;
varying float vAlpha; varying float vRev; varying float vWy; varying vec3 vCol; varying float vHi;
varying vec3 vWp; varying float vFlags;
void main(){
  if (vRev > uReveal) discard;
  float hid = 0.;
  if (vFlags > .5 && uFlat < .5) hid = hiddenBy(uCam, vWp);
  float aa = 1. - smoothstep(vHalf - .5 * uPx, vHalf + .5 * uPx, abs(vSide));
  float m = 1.;
  float p = vDist * uDash;
  float fw = max(fwidth(p), 1e-4);
  if (vKind > .5 && vKind < 1.5) {
    float q = fract(p / 11.) * 11.;
    m = smoothstep(0., fw, q) * (1. - smoothstep(5. - fw, 5., q));
  } else if (vKind > 1.5 && vKind < 2.5) {
    float q = fract(p / 53.) * 53.;
    m = (q < 34. || (q > 41. && q < 46.)) ? 1. : 0.;
  } else if (vKind > 2.5) {
    float q = fract(p / 5.) * 5.;
    m = q < 1.6 ? 1. : 0.;
  }
  if (hid > .5 && mod(floor(vFlags / 4.), 2.) > .5) { hid = 0.; m *= .3; }
  if (hid > .5) {
    if (mod(vFlags, 2.) > .5) discard;
    float q = fract(vDist * uDash / 7.) * 7.;
    m *= q < 3. ? .5 : 0.;
  }
  vec3 col = mix(vCol, uHiCol, vHi * uHiMix);
  float a = aa * m * vAlpha * uOpacity;
  if (a < .004) discard;
  gl_FragColor = vec4(col, a);
}`;

/** Billboard quads for the stars; a collapsed record is invisible until its group opens. */
export const STAR_VERT = /* glsl */ `
uniform vec2 uRes; uniform float uPx; uniform float uFlat;
uniform float uFogNear; uniform float uFogFar; uniform float uFogMin;
uniform float uFocus; uniform float uFocus2; uniform float uDim; uniform float uSel; uniform float uHover; uniform float uAppear;
uniform float uMonthLo; uniform float uMonthHi;
attribute vec3 iPos;
attribute vec4 iS;
attribute vec2 iT;
attribute float iK;
uniform float uExpand; uniform float uExpandT;
uniform vec3 uCam;
${HIDDEN_BY}
varying vec2 vUv; varying float vR; varying float vKind; varying float vAlpha; varying float vPin; varying float vSel; varying float vHid;
void main(){
  vHid = uFlat < .5 ? hiddenBy(uCam, iPos) : 0.;
  vec3 p = iPos; p.y *= (1. - uFlat);
  vec4 c = projectionMatrix * viewMatrix * vec4(p, 1.);
  float sel = abs(iT.x - uSel) < .5 ? 1. : 0.;
  float hov = abs(iT.x - uHover) < .5 ? 1. : 0.;
  float r = iS.x * uPx * (1. + .4 * hov + .2 * sel);
  float R = r + (iS.w > .5 ? 5. * uPx : 0.) + 2. * uPx;
  vec2 corner = position.xy * 2. - 1.;
  c.xy += corner * R / (uRes * .5) * c.w;
  gl_Position = c;
  vUv = corner * R; vR = r; vKind = iS.y; vPin = iS.w; vSel = sel;
  float fog = mix(1., uFogMin, smoothstep(uFogNear, uFogFar, c.w));
  float dim = 1.;
  if (uFocus > -.5 && abs(iS.z - uFocus) > .5 && abs(iS.z - uFocus2) > .5) dim = uDim;
  if (uMonthHi > uMonthLo && (iPos.y < uMonthLo || iPos.y > uMonthHi)) dim = min(dim, .22);
  float appear = smoothstep(iT.y, iT.y + .06, uAppear);
  float open = (uExpand > -.5 && abs(iS.z - uExpand) < .5) ? 1. : 0.;
  float colA = iK > .5 ? max(open * uExpandT, sel) : 1.;
  vAlpha = fog * dim * appear * max(.75, sel) * colA;
}`;

export const STAR_FRAG = /* glsl */ `
uniform vec3 uInk; uniform vec3 uPaper; uniform float uPx;
varying vec2 vUv; varying float vR; varying float vKind; varying float vAlpha; varying float vPin; varying float vSel; varying float vHid;
void main(){
  float d = length(vUv);
  float aa = .75 * uPx;
  float a = 0.; vec3 col = uInk;
  if (vKind < 1.5) {
    a = 1. - smoothstep(vR - aa, vR + aa, d);
    if (vKind > .5) col = uInk * .82;
  } else {
    float ring = 1. - smoothstep(.55 * uPx, .55 * uPx + aa, abs(d - vR));
    float fill = 1. - smoothstep(vR - aa, vR, d);
    col = mix(uPaper, uInk, ring / max(ring + fill * .9, 1e-3));
    a = max(ring, fill * .9);
  }
  if (vHid > .5 && vSel < .5) {
    float ring = 1. - smoothstep(.5 * uPx, .5 * uPx + aa, abs(d - max(vR, 2.2 * uPx)));
    a = ring * .55; col = uInk;
  }
  if (vPin > .5) {
    float halo = 1. - smoothstep(.5 * uPx, .5 * uPx + aa, abs(d - (vR + 3.6 * uPx)));
    a = max(a, halo * .55);
  }
  a *= vAlpha;
  if (a < .004) discard;
  gl_FragColor = vec4(col, a);
}`;

/** The bezel round the disc: a matte band that hides a little of what is under it. */
export const BEZEL_VERT = /* glsl */ `
uniform float uFlat;
varying vec3 vW;
void main(){
  vW = position;
  gl_Position = projectionMatrix * viewMatrix * vec4(position.x, 0., position.z, 1.);
}`;

export const BEZEL_FRAG = /* glsl */ `
uniform vec3 uPaper; uniform vec3 uInk; uniform float uHole; uniform float uDisk; uniform float uFlat;
varying vec3 vW;
void main(){
  float r = length(vW.xz);
  float aa = fwidth(r) * 1.2;
  float inDisk = 1. - smoothstep(uDisk - aa, uDisk + aa, r);
  float band = smoothstep(uHole - aa, uHole + aa, r) * inDisk;
  float a = band * .5 * (1. - uFlat) + inDisk * (1. - band) * .04 * (1. - uFlat);
  vec3 col = uPaper * 1.18 + uInk * .012;
  if (a < .003) discard;
  gl_FragColor = vec4(col * a, a);
}`;

/** The cone's skin: months alternate light and dark, a month or a topic can be tinted. */
export const SKIN_VERT = /* glsl */ `
uniform float uFlat;
varying vec3 vW; varying vec3 vN;
void main(){
  vec3 p = position; p.y *= (1. - uFlat);
  vW = position; vN = normal;
  gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.);
}`;

export const SKIN_RINGS = 64;

export const SKIN_FRAG = /* glsl */ `
uniform vec3 uInk; uniform vec3 uAcc; uniform vec3 uCam; uniform vec3 uLight;
uniform float uRings[${SKIN_RINGS}]; uniform float uNR; uniform float uRingBase; uniform float uThToday; uniform float uHc;
uniform float uFlat; uniform float uA;
uniform vec2 uMonth; uniform float uTopicPhi; uniform float uTopicOn;
varying vec3 vW; varying vec3 vN;
void main(){
  float th = uThToday * (1. + vW.y / uHc);
  float k = uRingBase;
  for (int i = 0; i < ${SKIN_RINGS}; i++) { if (float(i) >= uNR) break; if (uRings[i] < th) k += 1.; }
  float band = mod(k, 2.);
  vec3 n = normalize(vN);
  vec3 v = normalize(uCam - vW);
  float facing = dot(n, v);
  float lam = max(0., dot(n * sign(facing + 1e-4), normalize(uLight)));
  float edge = pow(1. - abs(facing), 3.);
  float a = (band > .5 ? 1. : .45) * (.55 + .45 * lam) + edge * .9;
  vec3 col = uInk;
  if (uMonth.y > uMonth.x && th >= uMonth.x && th <= uMonth.y) { col = uAcc; a += 1.6; }
  if (uTopicOn > .5) {
    float ph = atan(vW.x, vW.z);
    float d = abs(mod(ph - uTopicPhi + 3.14159265, 6.2831853) - 3.14159265);
    float sec = 1. - smoothstep(.16, .26, d);
    col = mix(col, uAcc, sec); a += sec * 1.4;
  }
  a *= uA * (1. - uFlat);
  if (facing < 0.) a *= .55;
  gl_FragColor = vec4(col * a, a);
}`;
