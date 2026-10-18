import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';

type Worldline = 'steins_gate' | 'beta';
export type IrisAttention = 'idle' | 'user' | 'password' | 'busy';

export interface IrisHandle {
  /** A keystroke: the pupil catches and the gaze flicks. */
  twitch: () => void;
  /** Rejected: the pupil snaps tight and the light flushes red. */
  flinch: () => void;
}

/** Iris outer radius at rest, in stage pixels. */
export const IRIS_R = 148;
const PUPIL: Record<IrisAttention, number> = { idle: 47, user: 43, password: 37, busy: 29 };
const ACCENT: Record<Worldline, [number, number, number]> = {
  steins_gate: [143 / 255, 220 / 255, 255 / 255],
  beta: [233 / 255, 138 / 255, 79 / 255],
};

const VERT = 'attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }';
/* The iris is light, not paint: radial fibres in two frequencies, a collarette, crypts and contraction furrows,
 * warm near the pupil (filament light), paper-white in the middle, cool toward the limbus. */
const FRAG = `
precision highp float;
uniform vec2 uRes; uniform vec2 uC; uniform float uS; uniform vec2 uGaze;
uniform float uPupil; uniform float uIris; uniform float uTime; uniform float uGlow; uniform float uTint;
uniform float uErr; uniform float uPA; uniform float uIntro; uniform float uRot; uniform vec3 uAccent;
float h(vec2 q) { q = fract(q * vec2(123.34, 456.21)); q += dot(q, q + 45.32); return fract(q.x * q.y); }
float n(vec2 q) {
  vec2 i = floor(q); vec2 f = fract(q); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(h(i), h(i + vec2(1.0, 0.0)), u.x), mix(h(i + vec2(0.0, 1.0)), h(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 q) { float v = 0.0; float a = 0.5; for (int k = 0; k < 4; k++) { v += a * n(q); q = q * 2.03 + vec2(17.1, 9.3); a *= 0.5; } return v; }
void main() {
  vec2 fc = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y);
  vec2 p = (fc - uC) / uS - uGaze;
  float r = length(p);
  float halo = uIris + 110.0;
  if (r > halo) { gl_FragColor = vec4(0.0); return; }
  float t = (r - uPupil) / max(uIris - uPupil, 1.0);
  float a = atan(p.y, p.x) + uRot + 0.22 * t * clamp(uPupil / 47.0 - 1.0, -0.8, 1.2);
  vec2 c0 = vec2(cos(a), sin(a));
  float f3 = fbm(c0 * 5.0 + vec2(t * 1.2, 2.0));
  /* Fibres wave a little as they run out, like stroma, instead of ruling straight spokes. */
  float aw = a + 0.024 * sin(t * 8.0 + f3 * 6.0);
  vec2 c = vec2(cos(aw), sin(aw));
  float f1 = n(c * (160.0 + t * 2.2) + vec2(uTime * 0.004, 0.0));
  float f2 = fbm(c * (44.0 + t * 1.6) + 3.1);
  float fib = (0.62 * pow(f2, 1.6) * 1.9 + 0.48 * pow(f1, 2.4) * 1.6) * (0.5 + 0.95 * f3);
  float tc = 0.32 + 0.05 * (f3 - 0.5) + 0.02 * sin(a * 13.0 + f3 * 3.0);
  float coll = exp(-pow((t - tc) / 0.034, 2.0)) * (0.5 + 0.8 * f2);
  /* Contraction furrows and crypts read as shadow, not as extra light. */
  float furrow = (exp(-pow((t - 0.64) / 0.012, 2.0)) + exp(-pow((t - 0.79) / 0.012, 2.0)) * 0.8) * smoothstep(0.4, 0.66, f3) * 0.5;
  float crypt = smoothstep(0.54, 0.7, fbm(c * 12.0 + vec2(t * 2.2, 7.0))) * smoothstep(0.34, 0.44, t) * (1.0 - smoothstep(0.74, 0.88, t));
  float inside = smoothstep(-0.004, 0.012, t) * (1.0 - smoothstep(0.95, 1.0, t));
  float em = mix(1.0, 0.3, smoothstep(0.0, 1.0, t)) * (1.0 - 0.55 * smoothstep(0.82, 0.97, t));
  float grow = 1.0 - smoothstep(uIntro * 1.25 - 0.22, uIntro * 1.25, t);
  float ruff = exp(-pow((r - uPupil - 1.3) / 1.3, 2.0)) * (0.5 + 0.6 * f1);
  float rim = exp(-pow((t - 0.985) / 0.012, 2.0)) * 0.22;
  float e = (inside * (fib * em * (1.0 - crypt * 0.82) * (1.0 - furrow) + coll * 0.5) + ruff * 0.95 + rim) * grow * uGlow;
  float glow = (r > uIris ? exp(-(r - uIris) / 30.0) * 0.075 : 0.06 * inside) * uIntro * uGlow;
  vec3 warm = vec3(1.0, 0.7, 0.42);
  vec3 paper = vec3(0.95, 0.92, 0.86);
  vec3 cool = vec3(0.58, 0.63, 0.72);
  vec3 col = mix(warm, paper, smoothstep(0.02, 0.42, t));
  col = mix(col, cool, smoothstep(0.5, 1.0, t));
  float front = uTint * 1.35;
  col = mix(col, uAccent * 1.08 + 0.06, (1.0 - smoothstep(front - 0.22, front, t)) * 0.92);
  col = mix(col, vec3(1.0, 0.36, 0.28), uErr * 0.78);
  e *= 1.0 + uErr * 0.35;
  /* Glass: a soft window reflection that drifts against the gaze. */
  vec2 rq = p + uGaze * 0.9 - vec2(-0.36, -0.44) * uIris;
  rq = mat2(0.82, 0.57, -0.57, 0.82) * rq;
  float refl = (1.0 - smoothstep(0.55, 1.0, length(rq / vec2(0.2 * uIris, 0.085 * uIris)))) * 0.12 * step(r, uIris) * uIntro * uPA;
  vec3 rgb = col * e * 0.9 + col * glow + vec3(refl);
  float pm = (1.0 - smoothstep(uPupil - 0.9, uPupil + 0.5, r)) * uPA * smoothstep(0.05, 0.35, uIntro);
  float A = clamp(max(max(rgb.r, rgb.g), rgb.b), 0.0, 1.0);
  A = max(A, pm);
  gl_FragColor = vec4(min(rgb, vec3(A)), A);
}`;

function compile(gl: WebGLRenderingContext) {
  const sh = (type: number, src: string) => {
    const s = gl.createShader(type);
    if (!s) return null;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const vs = sh(gl.VERTEX_SHADER, VERT);
  const fs = sh(gl.FRAGMENT_SHADER, FRAG);
  const prog = gl.createProgram();
  if (!vs || !fs || !prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.bindAttribLocation(prog, 0, 'p');
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
  return prog;
}

const num = (cs: CSSStyleDeclaration, name: string, fallback: number) => {
  const v = parseFloat(cs.getPropertyValue(name));
  return Number.isFinite(v) ? v : fallback;
};

export const LightIris = forwardRef<IrisHandle, {
  /** Element carrying the CSS-timed properties (--intro, --pupil, --iris, --tint, --glow, --pa) and geometry (--s, --cx, --cy). */
  driver: React.RefObject<HTMLElement | null>;
  attention: IrisAttention;
  exiting: boolean;
  reduced: boolean;
  worldline: Worldline;
}>(function LightIris({ driver, attention, exiting, reduced, worldline }, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [glFailed, setGlFailed] = useState(false);
  const sim = useRef({ pupil: PUPIL.idle, pv: 0, gx: 0, gy: 0, gvx: 0, gvy: 0, err: 0, rot: 0, flinchUntil: 0, pointer: null as null | [number, number] });
  const kickRef = useRef<() => void>(() => undefined);
  const live = useRef({ attention, exiting, reduced, worldline });
  live.current = { attention, exiting, reduced, worldline };

  useImperativeHandle(ref, () => ({
    twitch: () => {
      if (live.current.reduced) return;
      const s = sim.current;
      s.pv -= 34;
      s.gvx += (Math.random() - 0.5) * 26;
      s.gvy += (Math.random() - 0.5) * 18;
      s.rot += 0.004;
    },
    flinch: () => {
      const s = sim.current;
      s.err = 1;
      if (!live.current.reduced) s.flinchUntil = performance.now() + 140;
      kickRef.current();
    },
  }), []);

  // Hand the current pupil to the CSS entrance so it continues from where it is.
  useLayoutEffect(() => {
    if (exiting) driver.current?.style.setProperty('--p0', sim.current.pupil.toFixed(2));
  }, [exiting, driver]);

  useEffect(() => {
    const onMove = (e: PointerEvent) => { sim.current.pointer = [e.clientX, e.clientY]; };
    window.addEventListener('pointermove', onMove, { passive: true });
    return () => window.removeEventListener('pointermove', onMove);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const gl = canvas?.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false }) as WebGLRenderingContext | null;
    const prog = gl ? compile(gl) : null;
    if (!canvas || !gl || !prog) { setGlFailed(true); return; }
    gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const U = (name: string) => gl.getUniformLocation(prog, name);
    const u = {
      res: U('uRes'), c: U('uC'), s: U('uS'), gaze: U('uGaze'), pupil: U('uPupil'), iris: U('uIris'), time: U('uTime'),
      glow: U('uGlow'), tint: U('uTint'), err: U('uErr'), pa: U('uPA'), intro: U('uIntro'), rot: U('uRot'), accent: U('uAccent'),
    };
    let dpr = 1;
    const size = () => {
      // Keep the backing store under ~6 MP so the full-screen opening stays smooth on integrated GPUs.
      const area = window.innerWidth * window.innerHeight;
      dpr = Math.min(2, window.devicePixelRatio || 1, Math.sqrt(6e6 / Math.max(1, area)));
      canvas.width = Math.round(window.innerWidth * dpr);
      canvas.height = Math.round(window.innerHeight * dpr);
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    size();
    window.addEventListener('resize', size);

    let raf = 0;
    let last = performance.now();
    const t0 = last;
    const frame = (now: number) => {
      // Real elapsed time (capped after a stall), integrated in small steps so slow frames don't slow the eye.
      const elapsed = Math.min(0.25, Math.max(0, (now - last) / 1000));
      const steps = Math.max(1, Math.ceil(elapsed * 120));
      const dt = elapsed / steps;
      last = now;
      const s = sim.current;
      const el = driver.current;
      const cs = el ? getComputedStyle(el) : null;
      const sc = cs ? num(cs, '--s', 1) : 1;
      const cx = cs ? num(cs, '--cx', window.innerWidth / 2) : window.innerWidth / 2;
      const cy = cs ? num(cs, '--cy', window.innerHeight * 0.38) : window.innerHeight * 0.38;
      const { attention: att, exiting: out, reduced: still, worldline: wl } = live.current;
      const t = (now - t0) / 1000;

      if (!still) {
        // Pupil: a spring toward the attention target, with the small unrest (hippus) of a living eye.
        const flinching = now < s.flinchUntil;
        const target = flinching ? 19 : PUPIL[att] + 1.6 * Math.sin(t * 1.3) + 0.9 * Math.sin(t * 2.9 + 1.1);
        const k = flinching ? 900 : 60;
        const c = 2 * Math.sqrt(k) * (flinching ? 0.95 : 0.62);
        // Gaze: follows the pointer a little, and looks down to the form while a field is in use.
        let tx = 0;
        let ty = 0;
        if (!out && s.pointer) {
          tx = Math.max(-1, Math.min(1, (s.pointer[0] - cx) / (window.innerWidth * 0.5))) * 5;
          ty = Math.max(-1, Math.min(1, (s.pointer[1] - cy) / (window.innerHeight * 0.5))) * 4;
        }
        if (!out && (att === 'user' || att === 'password')) { tx *= 0.4; ty = ty * 0.4 + 4.5; }
        const kg = out ? 140 : 70;
        const cg = 2 * Math.sqrt(kg) * 0.8;
        for (let i = 0; i < steps; i++) {
          s.pv += (k * (target - s.pupil) - c * s.pv) * dt;
          s.pupil = Math.max(8, s.pupil + s.pv * dt);
          s.gvx += (kg * (tx - s.gx) - cg * s.gvx) * dt;
          s.gvy += (kg * (ty - s.gy) - cg * s.gvy) * dt;
          s.gx += s.gvx * dt;
          s.gy += s.gvy * dt;
        }
        s.rot += elapsed * 0.006;
      }
      s.err = Math.max(0, s.err - elapsed * (still ? 1.2 : 2.6));

      const pupil = out && cs ? num(cs, '--pupil', s.pupil) : still ? PUPIL.user : s.pupil;
      const W = canvas.width;
      const H = canvas.height;
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(u.res, W, H);
      gl.uniform2f(u.c, cx * dpr, cy * dpr);
      gl.uniform1f(u.s, sc * dpr);
      gl.uniform2f(u.gaze, still ? 0 : s.gx, still ? 0 : s.gy);
      gl.uniform1f(u.pupil, pupil);
      gl.uniform1f(u.iris, cs ? num(cs, '--iris', IRIS_R) : IRIS_R);
      gl.uniform1f(u.time, t);
      gl.uniform1f(u.glow, cs ? num(cs, '--glow', 1) : 1);
      gl.uniform1f(u.tint, cs ? num(cs, '--tint', 0) : 0);
      gl.uniform1f(u.err, s.err);
      gl.uniform1f(u.pa, cs ? num(cs, '--pa', 1) : 1);
      gl.uniform1f(u.intro, still ? 1 : cs ? num(cs, '--intro', 1) : 1);
      gl.uniform1f(u.rot, s.rot);
      gl.uniform3f(u.accent, ...ACCENT[wl]);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      // Reduced motion draws only while something is still settling (an error flush); otherwise it stays still.
      if (!still || s.err > 0) raf = requestAnimationFrame(frame);
      else raf = 0;
    };
    raf = requestAnimationFrame(frame);
    const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };
    window.addEventListener('resize', kick);
    kickRef.current = kick;
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', size);
      window.removeEventListener('resize', kick);
    };
  }, [driver]);

  // Under reduced motion the loop sleeps; wake it for state changes (attention, error flush).
  useEffect(() => {
    kickRef.current();
  }, [attention, reduced, worldline]);

  return (
    <>
      <canvas ref={canvasRef} className="sl-iris-gl" aria-hidden="true" />
      {glFailed && <div className="sl-iris-fallback" aria-hidden="true"><i /></div>}
    </>
  );
});
