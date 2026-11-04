import { memo, useRef, type KeyboardEvent } from 'react';

import type { ChartStar } from './chartCoordinates';
import { ymd } from './chartFormat';

const KIND_NAME = { fact: '认知', loose: '认知（未归组）', fragment: '片段' } as const;
export const starName = (s: ChartStar) => `${KIND_NAME[s.kind]}：${s.label}，${ymd(s.createdAt)} 记下${s.pinned ? '，置顶' : ''}`;
const STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

export type PlateStarsProps = {
  /** No. order: the keyboard walks this. */
  stars: ChartStar[];
  /** Drawing order (inside out), which is also the entrance order. */
  order: ChartStar[];
  pos: ReadonlyMap<string, { x: number; y: number }>;
  cx: number;
  cy: number;
  focusTopic: string | null;
  /** A collapsed plate focuses a group (a topic, or the ungrouped / fragment group) rather than a topic. */
  collapsed?: boolean;
  selectedId: string | null;
  rovingId: string | null;
  playing: boolean;
  stagger: number;
  onSelectStar: (id: string) => void;
  onHover: (id: string | null) => void;
  onActive: (id: string) => void;
};

// The static star layer. Its props do not change while the pointer sweeps the plate,
// so hovering redraws only the hover note, not every star.
export const PlateStars = memo(function PlateStars(props: PlateStarsProps) {
  const { stars, order, pos, cx, cy, focusTopic, collapsed, selectedId, rovingId, playing, stagger } = props;
  const layerRef = useRef<SVGGElement | null>(null);
  const onKey = (event: KeyboardEvent) => {
    const id = (event.target as Element).closest('[data-hit="star"]')?.getAttribute('data-id');
    if (!id) return;
    const index = stars.findIndex((star) => star.id === id);
    let next = -1;
    if (event.key in STEP) next = Math.min(stars.length - 1, Math.max(0, index + STEP[event.key]));
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = stars.length - 1;
    else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      event.stopPropagation();
      props.onSelectStar(id);
      return;
    } else return;
    event.preventDefault();
    const target = stars[next];
    props.onActive(target.id);
    layerRef.current?.querySelector<SVGGElement>(`[data-id="${CSS.escape(target.id)}"]`)?.focus({ preventScroll: true });
  };
  return (
    <g ref={layerRef} className="p-stars" role="group" aria-label="记忆星点（方向键切换，Enter 打开）" onKeyDown={onKey}>
      {order.map((s, i) => {
        const p = pos.get(s.id)!;
        const toward = Math.hypot(cx - p.x, cy - p.y) || 1;
        const ux = (cx - p.x) / toward;
        const uy = (cy - p.y) / toward;
        return (
          <g key={s.id} data-hit="star" data-id={s.id} data-kind={s.kind}
            role="button" tabIndex={s.id === rovingId ? 0 : -1} aria-label={starName(s)}
            aria-pressed={s.id === selectedId}
            onFocus={() => props.onActive(s.id)}
            className={`p-star is-${s.kind}${focusTopic && (collapsed ? s.group : s.topicId) === focusTopic ? ' lit' : ''}`}
            transform={`translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`}
            style={playing ? { animationDelay: `${Math.round(260 + i * stagger + (s.isNew ? 900 : 0))}ms` } : undefined}
            onClick={(event) => { event.stopPropagation(); props.onSelectStar(s.id); }}
            onPointerEnter={() => props.onHover(s.id)} onPointerLeave={() => props.onHover(null)}>
            {s.kind === 'fragment' && !s.pinned ? <path className="p-tail" d={`M${ux * 4} ${uy * 4}L${ux * 16} ${uy * 16}`} /> : null}
            {s.pinned ? <circle className="halo" r={s.radius + 3.4} /> : null}
            <circle className="dot" r={s.radius} />
            <circle className="hit" r={9} />
            <circle className="focus" r={s.radius + 6} />
          </g>
        );
      })}
    </g>
  );
});
