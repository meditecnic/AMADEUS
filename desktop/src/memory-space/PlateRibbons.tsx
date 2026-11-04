import { memo, useLayoutEffect, useRef } from 'react';

import type { TickSegment } from './plateRibbons';

export type Ribbon = { id: string; kind: 'topic' | 'loose' | 'fragment'; d: string; spine: string };

export type PlateRibbonsProps = {
  ribbons: readonly Ribbon[];
  /** The group that is open (or easing open or shut): its ticks are drawn by the unfurl, not here. */
  openId: string | null;
  playing: boolean;
  delayMs: number;
  onMove: (id: string, event: { clientX: number; clientY: number }) => void;
  onLeave: () => void;
  onPick: (id: string, event: { clientX: number; clientY: number }) => void;
};

// One <path> of ticks and one wide transparent <path> to hit per group, however many records
// there are: a 2000-record plate has a few dozen elements here, not 2000.
export const PlateRibbons = memo(function PlateRibbons({ ribbons, openId, playing, delayMs, onMove, onLeave, onPick }: PlateRibbonsProps) {
  const closed = ribbons.filter((ribbon) => ribbon.id !== openId);
  return (
    <g className="p-ribbons" data-testid="plate-ribbons">
      {closed.map((ribbon) => (
        <path key={`t-${ribbon.id}`} className={`p-ribbon is-${ribbon.kind}`} data-group={ribbon.id} d={ribbon.d}
          style={playing ? { animationDelay: `${delayMs}ms` } : undefined} />
      ))}
      {closed.map((ribbon) => (
        <path key={`h-${ribbon.id}`} className="p-ribbon-hit" data-hit="band" data-group={ribbon.id} d={ribbon.spine}
          onPointerMove={(event) => onMove(ribbon.id, event)} onPointerLeave={onLeave}
          onClick={(event) => { event.stopPropagation(); onPick(ribbon.id, event); }} />
      ))}
    </g>
  );
});

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export type PlateUnfurlProps = {
  ticks: readonly TickSegment[];
  pos: ReadonlyMap<string, { x: number; y: number }>;
  /** 'out': the ticks draw back into their stars. 'in': the stars draw out into ticks again. */
  dir: 'out' | 'in';
  ms: number;
  onDone: () => void;
};

// Moves only the end points of each tick, straight to (or from) its star, writing the path by hand
// so a 200-record group is one attribute per frame and no React render.
export function PlateUnfurl({ ticks, pos, dir, ms, onDone }: PlateUnfurlProps) {
  const ref = useRef<SVGPathElement | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useLayoutEffect(() => {
    const path = ref.current;
    if (!path) return undefined;
    const draw = (shrink: number) => {
      let d = '';
      for (const t of ticks) {
        const star = pos.get(t.id);
        if (!star) continue;
        const k = shrink;
        d += `M${(t.ax + (star.x - t.ax) * k).toFixed(1)} ${(t.ay + (star.y - t.ay) * k).toFixed(1)}`
          + `L${(t.bx + (star.x - t.bx) * k).toFixed(1)} ${(t.by + (star.y - t.by) * k).toFixed(1)}`;
      }
      path.setAttribute('d', d);
    };
    const started = performance.now();
    let frame = 0;
    draw(dir === 'out' ? 0 : 1);
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / ms);
      draw(dir === 'out' ? ease(t) : 1 - ease(t));
      if (t < 1) frame = requestAnimationFrame(step); else done.current();
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [ticks, pos, dir, ms]);
  return <path ref={ref} className="p-ribbon p-unfurl" data-testid="plate-unfurl" data-dir={dir} />;
}
