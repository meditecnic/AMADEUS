import { useEffect, useRef } from 'react';
import { TONE, rgba, type Tone } from './tone';

/**
 * Translucent binary columns (the Amadeus window's data wall, drawn by code). Each column is a fixed-width
 * string of 0/1 rows that drifts down at its own pace and darkens toward the bottom. ~24 fps is plenty;
 * hidden windows and reduced motion draw a single still frame.
 */
interface Props {
  tone: Tone;
  reduced?: boolean;
  /** Columns per 1000 px of width. */
  density?: number;
  className?: string;
}

interface Column { x: number; speed: number; offset: number; rows: string[]; bright: number }

const ROW_H = 17;
const FONT = '500 11px "IBM Plex Mono", ui-monospace, monospace';

function row(len: number) {
  let s = '';
  for (let i = 0; i < len; i += 1) s += Math.random() < 0.5 ? '0' : '1';
  return s;
}

export function BinaryField({ tone, reduced = false, density = 5, className }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const color = TONE[tone].bin;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let cols: Column[] = [];

    const build = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      const count = Math.max(1, Math.round((w / 1000) * density));
      const rowsN = Math.ceil(h / ROW_H) + 2;
      cols = Array.from({ length: count }, (_, i) => ({
        x: (w / count) * i + 6 + Math.random() * 10,
        speed: 6 + Math.random() * 14,
        offset: Math.random() * ROW_H,
        bright: 0.5 + Math.random() * 0.5,
        rows: Array.from({ length: rowsN }, () => row(14 + Math.floor(Math.random() * 6))),
      }));
    };

    const draw = (dt: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.font = FONT;
      ctx.textBaseline = 'top';
      for (const col of cols) {
        col.offset += col.speed * dt;
        while (col.offset >= ROW_H) {
          col.offset -= ROW_H;
          col.rows.pop();
          col.rows.unshift(row(col.rows[0]?.length ?? 16));
          // A single row occasionally flips, like a register being rewritten.
          const k = Math.floor(Math.random() * col.rows.length);
          col.rows[k] = row(col.rows[k].length);
        }
        for (let r = 0; r < col.rows.length; r += 1) {
          const y = r * ROW_H + col.offset - ROW_H;
          const fall = 1 - Math.min(1, Math.max(0, y / h));
          const a = 0.05 + fall * fall * 0.2 * col.bright;
          ctx.fillStyle = rgba(color, a);
          ctx.fillText('/' + col.rows[r], col.x, y);
        }
      }
    };

    build();
    let raf = 0;
    let last = performance.now();
    let acc = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      acc += dt;
      if (acc < 1 / 24) return;
      draw(acc);
      acc = 0;
    };
    const start = () => {
      cancelAnimationFrame(raf);
      if (reduced || document.hidden) { draw(0); return; }
      last = performance.now();
      raf = requestAnimationFrame(loop);
    };
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { build(); draw(0); });
    ro?.observe(canvas);
    const onVis = () => start();
    document.addEventListener('visibilitychange', onVis);
    start();
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [tone, reduced, density]);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
}
