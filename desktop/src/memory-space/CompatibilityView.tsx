import { layoutProjection } from './layout';
import type { MemoryProjection } from './types';

const VIEW = 240;

export function CompatibilityView(props: {
  projection: MemoryProjection;
  selectedId: string | null;
  focusedId?: string | null;
  onSelect: (id: string | null) => void;
}) {
  const display = layoutProjection(props.projection);
  const points = display.map((node) => ({
    ...node,
    sx: VIEW / 2 + node.position.x * 38,
    sy: VIEW / 2 - node.position.y * 38,
  }));
  const byId = new Map(points.map((node) => [node.id, node]));
  return (
    <div className="memory-compatibility" data-testid="memory-compatibility" data-webgl="false">
      <p className="memory-compatibility-label">兼容视图 / COMPATIBILITY VIEW</p>
      <svg
        viewBox={`0 0 ${VIEW} ${VIEW}`}
        role="img"
        aria-label="记忆兼容视图"
        className="memory-compatibility-svg"
      >
        {props.projection.edges.map((edge) => {
          const from = byId.get(edge.from);
          const to = byId.get(edge.to);
          if (!from || !to) return null;
          return (
            <line
              key={`${edge.kind}:${edge.from}:${edge.to}`}
              x1={from.sx}
              y1={from.sy}
              x2={to.sx}
              y2={to.sy}
              className={`memory-compatibility-edge is-${edge.kind}`}
            />
          );
        })}
        {points.map((node) => {
          const selected = node.id === props.selectedId;
          const focused = node.id === (props.focusedId ?? props.selectedId);
          const r = node.kind === 'continuity_hub' ? 14 : node.kind === 'topic' ? 7 : 4;
          return (
            <g key={node.id}>
              <circle
                cx={node.sx}
                cy={node.sy}
                r={r}
                className={`memory-compatibility-node is-${node.kind}${selected ? ' is-selected' : ''}${focused ? ' is-focused' : ''}`}
                onClick={() => props.onSelect(node.id)}
              />
              <text x={node.sx + r + 4} y={node.sy + 3} className="memory-compatibility-caption">
                {node.kind === 'continuity_hub' ? 'SOUL' : node.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
