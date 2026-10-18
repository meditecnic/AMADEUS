import { useEffect, useId, useRef } from 'react';

// A folded signal surface, drawn from analytic contours rather than portrait art.
// Dense contour edges catch the light; the open middle keeps it a signal, not a solid object.
const SIGNAL_CONTOURS = Array.from({ length: 142 }, (_, row) => {
  const t = row / 141;
  const spread = Math.pow(Math.sin(Math.PI * t), .6) * (110 + 40 * Math.sin(t * 9));
  const center = 268 + 46 * Math.sin(t * 6 - 1) + 18 * Math.cos(t * 17);
  return Array.from({ length: 81 }, (_, sample) => {
    const u = sample / 40 - 1;
    const fold = Math.sin(u * 3.4 + t * 8);
    const x = center + u * spread + 24 * fold * Math.sin(Math.PI * t);
    const y = 74 + t * 744 + 32 * Math.sin(u * Math.PI) * Math.sin(t * 7) + fold * 12;
    return `${sample === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join('');
});

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
  const gradientId = useId();
  const catchId = useId();
  const maskId = useId();
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
      <svg className="login-signal-relief" data-paused={paused || reducedMotion} viewBox="0 0 560 900" aria-hidden="true">
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2=".25">
            <stop offset="0" stopColor="var(--ink-primary)" stopOpacity=".08" />
            <stop offset=".28" stopColor="var(--ink-primary)" stopOpacity=".95" />
            <stop offset=".48" stopColor="var(--ink-primary)" stopOpacity=".14" />
            <stop offset=".68" stopColor="var(--ink-primary)" stopOpacity=".95" />
            <stop offset=".9" stopColor="var(--accent-primary)" stopOpacity=".4" />
            <stop offset="1" stopColor="var(--ink-primary)" stopOpacity=".1" />
          </linearGradient>
          <linearGradient id={catchId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="white" stopOpacity="0" />
            <stop offset=".5" stopColor="white" stopOpacity="1" />
            <stop offset="1" stopColor="white" stopOpacity="0" />
          </linearGradient>
          <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="560" height="900">
            <rect className="login-signal-sweep" x="0" y="-220" width="560" height="220" fill={`url(#${catchId})`} />
          </mask>
        </defs>
        <g className="login-signal-axis">
          <path d="M84 76V824M72 194H462M72 706H462M460 76V824" />
          <path d="M78 76H90M78 824H90M454 76H466M454 824H466M84 450H112M432 450H460" />
        </g>
        <g className="login-signal-contours" stroke={`url(#${gradientId})`}>
          {SIGNAL_CONTOURS.map((path, index) => <path key={index} d={path} />)}
        </g>
        <g className="login-signal-catch" mask={`url(#${maskId})`}>
          {SIGNAL_CONTOURS.map((path, index) => <path key={index} d={path} />)}
        </g>
      </svg>
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
