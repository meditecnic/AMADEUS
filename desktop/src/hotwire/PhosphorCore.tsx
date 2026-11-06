import { useEffect, useRef } from 'react';
import { TONE, rgba, type Tone } from './tone';

/**
 * Her presence without the asset pack: a phosphor dot sphere inside a signal ring (design/references.md §2.5).
 * Every form maps to a real state; nothing loops "for show" while she is offline.
 *
 * mode="scope" (asset pack installed): the sphere is dropped and the ring unrolls into a flat voiceprint.
 */
export type CoreState = 'listening' | 'thinking' | 'replying' | 'speaking' | 'connecting' | 'offline';

interface Props {
  state: CoreState;
  tone: Tone;
  /** Increments once per arriving sentence (or voice frame); each step kicks the signal. */
  beat: number;
  mode?: 'core' | 'scope';
  /** [EMO:…] tag. Only bends the ring a little. */
  emotion?: string;
  reduced?: boolean;
  className?: string;
}

interface Params {
  scale: number; spin: number; jitter: number; inflow: number;
  ringAmp: number; ringFreq: number; ringNoise: number; alpha: number; breathe: number;
}

const TARGET: Record<CoreState, Params> = {
  listening: { scale: 1, spin: (Math.PI * 2) / 40, jitter: 0, inflow: 0, ringAmp: 0.016, ringFreq: 3, ringNoise: 0.002, alpha: 1, breathe: 0.02 },
  thinking: { scale: 0.7, spin: (Math.PI * 2) / 6, jitter: 0.014, inflow: 1, ringAmp: 0.022, ringFreq: 19, ringNoise: 0.03, alpha: 1, breathe: 0 },
  replying: { scale: 1, spin: (Math.PI * 2) / 24, jitter: 0.002, inflow: 0, ringAmp: 0.024, ringFreq: 5, ringNoise: 0.006, alpha: 1, breathe: 0.008 },
  speaking: { scale: 1, spin: (Math.PI * 2) / 24, jitter: 0.002, inflow: 0, ringAmp: 0.03, ringFreq: 6, ringNoise: 0.008, alpha: 1, breathe: 0.008 },
  connecting: { scale: 0.9, spin: (Math.PI * 2) / 80, jitter: 0, inflow: 0, ringAmp: 0.006, ringFreq: 2, ringNoise: 0.002, alpha: 0.5, breathe: 0.03 },
  offline: { scale: 0.92, spin: 0, jitter: 0, inflow: 0, ringAmp: 0, ringFreq: 1, ringNoise: 0, alpha: 0.2, breathe: 0 },
};

const EDGE: Record<string, number> = { tsundere: 1, annoyed: 0.85, surprised: 0.6, intellectual: 0.25, happy: -0.6, embarrassed: -0.3 };

const N = 1400;
const BUCKETS = 10;
const RING_SAMPLES = 280;
const NOISE = Array.from({ length: 1024 }, () => Math.random() * 2 - 1);

function sphere() {
  const golden = Math.PI * (3 - Math.sqrt(5));
  const pts = new Float32Array(N * 4);
  for (let i = 0; i < N; i += 1) {
    const y = 1 - (i / (N - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = golden * i;
    pts[i * 4] = Math.cos(th) * r;
    pts[i * 4 + 1] = y;
    pts[i * 4 + 2] = Math.sin(th) * r;
    pts[i * 4 + 3] = Math.random();
  }
  return pts;
}

export function PhosphorCore({ state, tone, beat, mode = 'core', emotion, reduced = false, className }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({ state, tone, mode, emotion, reduced });
  live.current = { state, tone, mode, emotion, reduced };
  const energy = useRef(0);
  const redraw = useRef<(() => void) | null>(null);

  useEffect(() => {
    energy.current = Math.min(2.6, energy.current + 1);
  }, [beat]);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const pts = sphere();
    const cur: Params = { ...TARGET[live.current.state] };
    let w = 0;
    let h = 0;
    let dpr = 1;
    let angle = 0.6;
    let t = 0;
    let edge = 0;
    const bucketX: number[][] = Array.from({ length: BUCKETS }, () => []);

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
    };

    const step = (dt: number) => {
      const target = TARGET[live.current.state];
      const k = 1 - Math.exp(-dt * 3.2);
      (Object.keys(cur) as (keyof Params)[]).forEach((key) => { cur[key] += (target[key] - cur[key]) * k; });
      edge += ((EDGE[live.current.emotion ?? ''] ?? 0) - edge) * k;
      energy.current *= Math.exp(-dt * 2.1);
      angle += cur.spin * dt;
      t += dt;
    };

    const ringRadius = (R: number, i: number, amp: number) => {
      const th = (i / RING_SAMPLES) * Math.PI * 2;
      const f = cur.ringFreq;
      const sharp = Math.max(0, edge);
      const soft = Math.max(0, -edge);
      const wave = Math.sin(f * th + t * 1.4) * (0.7 + soft * 0.3)
        + Math.sin((f * 3 + 1) * th - t * 2.3) * 0.28 * sharp
        + Math.sin((f + 2) * th + t * 0.7) * 0.18;
      const n = NOISE[(i * 7 + Math.floor(t * 50)) % NOISE.length] * cur.ringNoise;
      return R * (1 + amp * wave + n);
    };

    const draw = () => {
      const { tone: tn, mode: md } = live.current;
      const c = TONE[tn];
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const e = energy.current;
      const breath = 1 + cur.breathe * Math.sin((t * Math.PI * 2) / 5);
      const cx = w / 2;
      const cy = h / 2;

      if (md === 'scope') {
        // Flat voiceprint under the portrait.
        const amp = (cur.ringAmp * 7 + e * 0.12) * h * 0.5;
        ctx.globalCompositeOperation = 'source-over';
        ctx.strokeStyle = rgba(c.ash, 0.25 * cur.alpha);
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let x = 0; x <= w; x += 8) { ctx.moveTo(x, cy - 3); ctx.lineTo(x, cy + 3); }
        ctx.stroke();
        ctx.globalCompositeOperation = 'lighter';
        for (const pass of [{ lw: 5, a: 0.16 }, { lw: 1.3, a: 0.9 }]) {
          ctx.strokeStyle = rgba(pass.a > 0.5 ? c.core : c.her, pass.a * cur.alpha);
          ctx.lineWidth = pass.lw;
          ctx.beginPath();
          for (let i = 0; i <= RING_SAMPLES; i += 1) {
            const x = (i / RING_SAMPLES) * w;
            const env = Math.sin((i / RING_SAMPLES) * Math.PI);
            const y = cy + (ringRadius(1, i, 1) - 1) * amp * 6 * env;
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
          }
          ctx.stroke();
        }
        ctx.globalCompositeOperation = 'source-over';
        return;
      }

      const R = Math.min(w, h) * 0.3 * cur.scale * breath * (1 + e * 0.025);
      // Glow at the heart of the sphere.
      ctx.globalCompositeOperation = 'lighter';
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.2);
      g.addColorStop(0, rgba(c.her, (0.16 + e * 0.05) * cur.alpha));
      g.addColorStop(1, rgba(c.her, 0));
      ctx.fillStyle = g;
      ctx.fillRect(cx - R * 1.3, cy - R * 1.3, R * 2.6, R * 2.6);

      // Dots, grouped into a few alpha buckets so the fill style changes 10 times a frame, not 1400.
      for (const b of bucketX) b.length = 0;
      const ca = Math.cos(angle);
      const sa = Math.sin(angle);
      const tilt = 0.38;
      const ct = Math.cos(tilt);
      const st = Math.sin(tilt);
      for (let i = 0; i < N; i += 1) {
        const x0 = pts[i * 4];
        const y0 = pts[i * 4 + 1];
        const z0 = pts[i * 4 + 2];
        const seed = pts[i * 4 + 3];
        let r = 1;
        if (cur.inflow > 0.01 && i % 3 === 0) {
          const ph = (t * 0.55 + seed) % 1;
          r = 1 - cur.inflow * ph * 0.85;
        }
        if (cur.jitter > 0) r += NOISE[(i * 13 + Math.floor(t * 40)) % NOISE.length] * cur.jitter;
        const x1 = x0 * ca + z0 * sa;
        const z1 = -x0 * sa + z0 * ca;
        const y1 = y0 * ct - z1 * st;
        const z2 = y0 * st + z1 * ct;
        const depth = (z2 + 1) / 2;
        const bucket = Math.min(BUCKETS - 1, Math.floor(depth * BUCKETS));
        bucketX[bucket].push(cx + x1 * R * r, cy + y1 * R * r);
      }
      for (let b = 0; b < BUCKETS; b += 1) {
        const depth = (b + 0.5) / BUCKETS;
        const size = 0.8 + depth * 1.7;
        ctx.fillStyle = rgba(depth > 0.82 ? c.core : c.her, (0.06 + depth * depth * 0.72) * cur.alpha * (1 + e * 0.18));
        const list = bucketX[b];
        for (let j = 0; j < list.length; j += 2) ctx.fillRect(list[j] - size / 2, list[j + 1] - size / 2, size, size);
      }

      // Signal ring: a closed scope trace around the sphere.
      const RR = Math.min(w, h) * 0.3 * 1.38 * (0.92 + cur.scale * 0.08);
      const amp = cur.ringAmp + e * 0.045;
      for (const pass of [{ lw: 7, col: c.hot, a: 0.1 }, { lw: 2.2, col: c.her, a: 0.38 }, { lw: 1, col: c.core, a: 0.85 }]) {
        ctx.strokeStyle = rgba(pass.col, pass.a * cur.alpha);
        ctx.lineWidth = pass.lw;
        ctx.beginPath();
        for (let i = 0; i <= RING_SAMPLES; i += 1) {
          const th = (i / RING_SAMPLES) * Math.PI * 2;
          const rr = ringRadius(RR, i % RING_SAMPLES, amp);
          const x = cx + Math.cos(th) * rr;
          const y = cy + Math.sin(th) * rr;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.globalCompositeOperation = 'source-over';
      // Graticule ticks outside the ring, like a scope bezel.
      ctx.strokeStyle = rgba(c.ash, 0.32);
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i < 120; i += 1) {
        const th = (i / 120) * Math.PI * 2;
        const r0 = RR * 1.12;
        const r1 = r0 + (i % 10 === 0 ? 10 : i % 5 === 0 ? 6 : 3);
        ctx.moveTo(cx + Math.cos(th) * r0, cy + Math.sin(th) * r0);
        ctx.lineTo(cx + Math.cos(th) * r1, cy + Math.sin(th) * r1);
      }
      ctx.stroke();
    };

    resize();
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      step(dt);
      draw();
    };
    const still = () => {
      // Reduced motion: jump straight to the state's form and hold it.
      Object.assign(cur, TARGET[live.current.state]);
      draw();
    };
    redraw.current = () => { if (live.current.reduced || document.hidden) still(); };
    const start = () => {
      cancelAnimationFrame(raf);
      if (live.current.reduced || document.hidden) { still(); return; }
      last = performance.now();
      raf = requestAnimationFrame(loop);
    };
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { resize(); draw(); });
    ro?.observe(canvas);
    document.addEventListener('visibilitychange', start);
    start();
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      document.removeEventListener('visibilitychange', start);
      redraw.current = null;
    };
  }, [reduced]);

  // Still frames follow state changes too.
  useEffect(() => { redraw.current?.(); }, [state, tone, mode, emotion, beat]);

  return <canvas ref={ref} className={className} aria-hidden="true" data-core-state={state} />;
}
