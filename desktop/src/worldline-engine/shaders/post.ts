import { COMMON } from './common';

export const BRIGHT_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform sampler2D uTex;
uniform vec2 uTexel;
uniform float uThresh;
void main(){
  vec3 c = texture(uTex, vUv + uTexel * vec2(-.5, -.5)).rgb + texture(uTex, vUv + uTexel * vec2(.5, -.5)).rgb
         + texture(uTex, vUv + uTexel * vec2(-.5, .5)).rgb + texture(uTex, vUv + uTexel * vec2(.5, .5)).rgb;
  c *= .25;
  float br = max(c.r, max(c.g, c.b));
  float k = max(br - uThresh, 0.);
  k = k * k / (k + .35);
  o = vec4(c * (k / max(br, 1e-4)), 1.);
}`;

export const DOWN_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform sampler2D uTex;
uniform vec2 uTexel;
void main(){
  vec3 s = texture(uTex, vUv).rgb * 4.;
  s += texture(uTex, vUv - uTexel).rgb;
  s += texture(uTex, vUv + uTexel).rgb;
  s += texture(uTex, vUv + vec2(uTexel.x, -uTexel.y)).rgb;
  s += texture(uTex, vUv - vec2(uTexel.x, -uTexel.y)).rgb;
  o = vec4(s / 8., 1.);
}`;

export const UP_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform sampler2D uTex;
uniform vec2 uTexel;
uniform float uWeight;
void main(){
  vec2 t = uTexel;
  vec3 s = texture(uTex, vUv + vec2(-t.x * 2., 0.)).rgb;
  s += texture(uTex, vUv + vec2(-t.x, t.y)).rgb * 2.;
  s += texture(uTex, vUv + vec2(0., t.y * 2.)).rgb;
  s += texture(uTex, vUv + vec2(t.x, t.y)).rgb * 2.;
  s += texture(uTex, vUv + vec2(t.x * 2., 0.)).rgb;
  s += texture(uTex, vUv + vec2(t.x, -t.y)).rgb * 2.;
  s += texture(uTex, vUv + vec2(0., -t.y * 2.)).rgb;
  s += texture(uTex, vUv + vec2(-t.x, -t.y)).rgb * 2.;
  o = vec4(s / 12. * uWeight, 1.);
}`;

export const COMPOSITE_FS = /* glsl */ `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform sampler2D uCombo;
uniform sampler2D uBloom;
uniform float uBloomK;
uniform float uFlash;
uniform vec3 uFlashColor;
uniform float uCA;
uniform float uGrain;
uniform float uFrame;
uniform float uFade;
uniform float uVig;
uniform float uExposure;
uniform vec2 uCenter;
uniform float uTear;
uniform float uTearSeed;
uniform vec3 uSlipA;
uniform vec3 uSlipB;
uniform float uZoom;
uniform float uLens;
uniform float uFlare;
uniform float uAspect;
uniform float uRed;
uniform float uStreak;
uniform float uPin;
uniform float uCAMode;
uniform float uCACyan;
uniform float uFloorDark;
uniform float uFlareStreak;
uniform float uFlareRays;
uniform float uVeil;
${COMMON}
vec3 ca(sampler2D t, vec2 uv){
  if (uCA <= 0.) return texture(t, uv).rgb;
  vec2 d = uv - uCenter;
  // mode 0: red out, blue in; mode 1: red (+ some blue) out, green in -> magenta / green fringes
  float gs = 1. - uCA * uCAMode;
  float bs = 1. - uCA + uCA * 1.3 * uCAMode;
  float rs = 1. + uCA;
  // R1-25: red out, cyan (green + blue) in - the burst capsules of src 13.83-13.90
  rs = mix(rs, 1. - .7 * uCA, uCACyan);
  gs = mix(gs, 1. + .5 * uCA, uCACyan);
  bs = mix(bs, 1. + .5 * uCA, uCACyan);
  return vec3(texture(t, uCenter + d * rs).r, texture(t, uCenter + d * gs).g, texture(t, uCenter + d * bs).b);
}
vec3 shoulder(vec3 c){
  vec3 hi = .78 + .22 * (1. - exp(-(c - .78) / .22));
  return mix(c, hi, step(.78, c));
}
void main(){
  vec2 uv = vUv;
  float split = 0.;
  float outside = 1.;
  if (uLens != 0.) {
    // > 0: barrel (edges squeezed, a wide lens pushing through doors)
    // < 0: pincushion (edges stretched outward, the outer tubes lean out and grow)
    vec2 d = (uv - uCenter) * vec2(uAspect, 1.);
    float r2 = dot(d, d);
    if (uLens > 0.) { float k = uLens * 1.7; d *= (1. + k * r2) / (1. + k * .6); }
    else { float k = -uLens * 1.4; d *= (1. + k * .25) / (1. + k * r2); }
    uv = uCenter + d / vec2(uAspect, 1.);
    vec2 e = max(abs(uv - .5) - .5, 0.);
    outside = 1. - smoothstep(0., .04, max(e.x, e.y));
  }
  if (uPin > 0.) {
    // edge-only pincushion measured on src 13.80: out = r (1 + c r^3), the centre keeps its
    // size, the outer tubes are pushed out ~1.35x and stretched ~2.3x radially (Newton inverse)
    vec2 d = (uv - uCenter) * vec2(uAspect, 1.);
    float ro = length(d);
    float c = 1.69 * uPin;
    float rr = ro;
    for (int i = 0; i < 4; i++) {
      float r3 = rr * rr * rr;
      rr -= (rr * (1. + c * r3) - ro) / (1. + 4. * c * r3);
    }
    d *= ro > 1e-5 ? rr / ro : 1.;
    uv = uCenter + d / vec2(uAspect, 1.);
  }
  if (uStreak > 0.) {
    // vertical smear: each column slides by its own amount
    uv.y += (vnoise(vec2(uv.x * 40., 7.)) - .5) * .12 * uStreak * .35;
  }
  if (uSlipA.z != 0. || uSlipB.z != 0.) {
    // two measured horizontal slips (src 7.884 / 7.902): the band with the instrument jumps
    // sideways for a frame while the rest of the picture stays put
    float yy = uv.y - .5;
    if (yy > uSlipA.x && yy < uSlipA.y) uv.x += uSlipA.z / uAspect;
    if (yy > uSlipB.x && yy < uSlipB.y) uv.x += uSlipB.z / uAspect;
  }
  if (uTear > 0.) {
    // signal tearing: a few horizontal bands slip sideways, fine scanline jitter, channel offset
    float sd = floor(uTearSeed);
    float band = floor(uv.y * (14. + 10. * hash11(sd * .37)) + hash11(sd) * 5.);
    float hb = hash11(band * 1.37 + sd * 3.11);
    float on = step(1. - .6 * uTear, hb);
    uv.x += on * (hash11(band * 2.71 + sd * 1.7) - .5) * .16 * uTear;
    uv.x += (hash11(floor(uv.y * 360.) + sd * 7.3) - .5) * .006 * uTear;
    split = (.004 + .010 * on) * uTear;
  }
  vec3 c;
  if (uZoom > 0.) {
    // radial impact: smear along the rays from the readout centre
    vec2 dv = uv - uCenter;
    c = vec3(0.);
    float j = hash12(gl_FragCoord.xy) / 16.;
    // the channels smear by different lengths in the magenta/green mode (the burst's coloured
    // streak ends, src 13.87-13.95)
    vec3 kc = mix(vec3(1.), vec3(1.16, .84, 1.06), uCAMode);
    for (int i = 0; i < 16; i++){
      float w = float(i) / 15. + j;
      vec2 qr = uCenter + dv * (1. - uZoom * w * kc.r);
      vec2 qg = uCenter + dv * (1. - uZoom * w * kc.g);
      vec2 qb = uCenter + dv * (1. - uZoom * w * kc.b);
      c += vec3(ca(uCombo, qr).r + ca(uBloom, qr).r * uBloomK,
                ca(uCombo, qg).g + ca(uBloom, qg).g * uBloomK,
                ca(uCombo, qb).b + ca(uBloom, qb).b * uBloomK);
    }
    c /= 16.;
  } else if (uStreak > 0.) {
    // columns smeared vertically by their own lengths (data-smear look)
    float cn = vnoise(vec2(uv.x * 180., 3.)) * .7 + vnoise(vec2(uv.x * 27., 9.)) * .3;
    float len = uStreak * (.01 + .12 * cn * cn);
    c = vec3(0.);
    float j = hash12(gl_FragCoord.xy) / 8.;
    for (int i = 0; i < 8; i++) {
      vec2 q = uv + vec2(0., (float(i) / 8. + j - .5) * len);
      c += ca(uCombo, q) + ca(uBloom, q) * uBloomK;
    }
    c /= 8.;
  } else {
    c = ca(uCombo, uv) + ca(uBloom, uv) * uBloomK;
  }
  if (split > 0.) {
    c.r = texture(uCombo, uv + vec2(split, 0.)).r + texture(uBloom, uv + vec2(split, 0.)).r * uBloomK;
    c.b = texture(uCombo, uv - vec2(split, 0.)).b + texture(uBloom, uv - vec2(split, 0.)).b * uBloomK;
  }
  c *= outside;
  if (uRed > 0.) {
    float edge = smoothstep(.22, .5, abs(vUv.x - .5)) * (.7 + .3 * vnoise(vec2(vUv.y * 6., 1.)));
    c = mix(c, c * vec3(1.3, .5, .4) + vec3(.42, .04, .01) * edge, edge * uRed);
  }
  if (uFlare > 0.) {
    // light core at the decimal point: hot centre, a thin horizontal streak, a few rays
    vec2 d = (vUv - uCenter) * vec2(uAspect, 1.);
    float r = length(d);
    float core = exp(-r * r / .0007) * 2.2 + exp(-r / .045) * .55 + exp(-r / .22) * .22;
    float streak = (exp(-abs(d.y) / .0032) * exp(-abs(d.x) / .55) * 1.1 + exp(-abs(d.y) / .018) * exp(-abs(d.x) / .28) * .22) * uFlareStreak;
    // soft, uneven rays: light breaking through between the tube parts, not a star filter
    float an = atan(d.y, d.x) / 6.2832 + .5;
    float rays = vnoise(vec2(an * 34., 0.)) * .6 + vnoise(vec2(an * 90., 4.)) * .4;
    rays = pow(rays, 2.5) * 1.4;
    rays *= exp(-r / .26) * smoothstep(.01, .1, r) * min(uFlareStreak, uFlareRays);
    c += (vec3(1., .86, .64) * core + vec3(1., .72, .40) * streak * 1.3 + vec3(1., .78, .52) * rays * .38) * uFlare;
  }
  if (uVeil > 0.) {
    // the lights crowd the lens: a cream wash, strongest above the floor of the case
    vec2 d = (vUv - uCenter) * vec2(uAspect, 1.);
    float m = smoothstep(-.32, .12, d.y) * (.55 + .45 * exp(-dot(d, d) / .5));
    c += vec3(.62, .52, .40) * uVeil * m * (1. - .45 * clamp(max(c.r, max(c.g, c.b)), 0., 1.));
  }
  if (uFloorDark > 0.) {
    // R1-25 (src 13.80-13.88): below the tubes the floor stays dark teal-grey between the bright
    // capsules - only the dim haze is pulled down, the capsules keep their light
    float fm = smoothstep(.42, .14, vUv.y);
    float lm = max(c.r, max(c.g, c.b));
    float kd = uFloorDark * fm * (1. - smoothstep(.40, .85, lm));
    // (src 13.83 floor ~(40,55,60): dark teal-grey, flat)
    c = mix(c, vec3(.030, .046, .052) + c * .12, kd);
  }
  c *= uExposure;
  float hot = max(max(c.r, max(c.g, c.b)) - 1., 0.);
  c += hot * vec3(.20, .34, .42);
  c = shoulder(max(c, 0.));
  float lum = dot(c, vec3(.3, .59, .11));
  vec2 fc = floor(gl_FragCoord.xy);
  float g = hash12(fc + vec2(uFrame * 37.17, uFrame * 11.31)) + hash12(fc * 1.37 + uFrame * 3.1) - 1.;
  c += g * uGrain * (.45 + .55 * (1. - abs(lum - .45) * 1.6));
  vec2 v = (vUv - .5) * vec2(1.25, 1.);
  c *= mix(1., 1. - smoothstep(.35, 1.0, length(v)) * .55, uVig);
  c = mix(c, uFlashColor, clamp(uFlash, 0., 1.));
  c = mix(c, vec3(0.), clamp(uFade, 0., 1.));
  o = vec4(clamp(c, 0., 1.), 1.);
}`;
