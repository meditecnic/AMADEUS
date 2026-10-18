import { useEffect, useId, useRef } from 'react';
import { SignGate } from './SignGate';

interface RainColumn {
  x: number;
  phase: number;
  speed: number;
  bits: string;
}

/** Original binary strips. A small seeded generator keeps resize/static frames reproducible. */
export function createRainColumns(width: number, seed = 1048596): RainColumn[] {
  let state = seed >>> 0;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
  return Array.from({ length: Math.max(0, Math.floor(width / 108)) }, (_, index) => ({
    x: index * 108 + random() * 8,
    phase: random(),
    speed: .6 + random() * .8,
    bits: Array.from({ length: 72 }, () => random() > .5 ? '1' : '0').join(''),
  }));
}

export function LoginRain({ reducedMotion, paused }: {
  reducedMotion: boolean;
  paused: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const elapsedRef = useRef(0);
  const grainId = useId();

  useEffect(() => {
    const canvas = ref.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    let frame: number | undefined;
    let width = 0;
    let height = 0;
    let columns: RainColumn[] = [];
    const style = getComputedStyle(canvas);
    // CSS resolves the motion token's calc() into this otherwise unused duration.
    const duration = style.animationDuration;
    const cycleMs = parseFloat(duration) * (duration.endsWith('ms') ? 1 : 1000);
    const started = performance.now() - elapsedRef.current;

    const draw = (elapsed: number) => {
      elapsedRef.current = elapsed;
      context.clearRect(0, 0, width, height);
      context.fillStyle = style.color;
      context.font = `11px ${style.fontFamily}`;
      const travel = height + 12 * 22;
      for (const column of columns) {
        const progress = column.phase + (cycleMs > 0 ? elapsed / cycleMs : 0) * column.speed;
        const head = (progress % 1) * travel;
        for (let row = 0; row < 12; row++) {
          context.globalAlpha = (1 - row / 12) * .44;
          const offset = row * 5 % column.bits.length;
          context.fillText(column.bits.slice(offset, offset + 12), column.x, head - row * 22);
        }
      }
      context.globalAlpha = 1;
    };

    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      width = bounds.width;
      height = bounds.height;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      columns = createRainColumns(width);
      draw(elapsedRef.current);
    };
    resize();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : undefined;
    observer?.observe(canvas);
    window.addEventListener('resize', resize);

    const tick = (now: number) => {
      draw(now - started);
      frame = requestAnimationFrame(tick);
    };
    if (!reducedMotion && !paused) frame = requestAnimationFrame(tick);
    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('resize', resize);
    };
  }, [reducedMotion, paused]);

  return (
    <>
      <SignGate className="login-gate" paused={paused || reducedMotion} />
      {!reducedMotion && <canvas ref={ref} className="login-rain" aria-hidden="true" />}
      <svg className="login-material-grain" width="100%" height="100%" aria-hidden="true">
        <defs>
          <filter id={grainId}>
            <feTurbulence type="fractalNoise" baseFrequency=".86" numOctaves="3" seed="17" stitchTiles="stitch" />
            <feColorMatrix type="saturate" values="0" />
          </filter>
        </defs>
        <rect width="100%" height="100%" filter={`url(#${grainId})`} />
      </svg>
    </>
  );
}
