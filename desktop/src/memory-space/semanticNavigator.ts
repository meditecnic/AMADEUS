import type { MemoryProjection, ProjectionNode } from './types';

const KIND_RANK: Record<ProjectionNode['kind'], number> = {
  continuity_hub: 0,
  topic: 1,
  fact: 2,
  experience: 3,
};

export function semanticOrder(projection: MemoryProjection): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  const push = (id: string | null | undefined) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    ids.push(id);
  };
  push(projection.center.projection_id);
  for (const id of projection.result_ids) push(id);
  const rest = projection.nodes
    .slice()
    .sort((a, b) => {
      const rank = KIND_RANK[a.kind] - KIND_RANK[b.kind];
      if (rank !== 0) return rank;
      return a.projection_id.localeCompare(b.projection_id);
    });
  for (const node of rest) push(node.projection_id);
  return ids;
}

export function neighborId(order: readonly string[], current: string | null, delta: number): string | null {
  if (order.length === 0) return null;
  if (!current) return delta >= 0 ? order[0] : order[order.length - 1];
  const index = order.indexOf(current);
  if (index < 0) return order[0];
  const next = Math.max(0, Math.min(order.length - 1, index + delta));
  return order[next] ?? null;
}

export type SelectionReconcile = {
  selectedId: string | null;
  announced: string | null;
  fellBack: boolean;
};

export function reconcileSelection(
  selectedId: string | null,
  projection: MemoryProjection | null,
): SelectionReconcile {
  if (!projection) {
    return { selectedId: null, announced: selectedId ? '选择已清除。' : null, fellBack: Boolean(selectedId) };
  }
  const order = semanticOrder(projection);
  if (selectedId && order.includes(selectedId)) {
    return { selectedId, announced: null, fellBack: false };
  }
  const soul = projection.center.projection_id;
  if (selectedId) {
    return { selectedId: soul, announced: '原先选中的记忆已不在当前投影中，已回到 SOUL。', fellBack: true };
  }
  return { selectedId: null, announced: null, fellBack: false };
}

export function announceNode(
  projection: MemoryProjection,
  id: string | null,
): string {
  if (!id) return '未选择记忆。';
  const node = projection.nodes.find((item) => item.projection_id === id);
  if (!node) return '未选择记忆。';
  const order = semanticOrder(projection);
  const position = order.indexOf(id) + 1;
  const total = order.length;
  const hops = projection.edges.filter((edge) => edge.from === id || edge.to === id).length;
  if (node.kind === 'continuity_hub') {
    return `SOUL，投影中心，第 ${position} 项，共 ${total} 项，关系 ${hops}。`;
  }
  const kindName = node.kind === 'topic' ? '主题' : node.kind === 'fact' ? '事实' : '经历';
  const status = node.kind === 'fact' || node.kind === 'experience'
    ? (node.is_pinned ? '已置顶' : '普通')
    : `${node.fact_count} 条事实`;
  return `${kindName} ${node.label}，${status}，第 ${position} 项，共 ${total} 项，关系 ${hops}。`;
}
