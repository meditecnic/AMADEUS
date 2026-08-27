import type { DisplayNode, ProjectionEdge } from './types';

export type EmphasisMode = 'rest' | 'search' | 'focus';

export type EmphasisInput = {
  query: string;
  resultIds: readonly string[];
  hoverId: string | null;
  selectedId: string | null;
  edges: readonly ProjectionEdge[];
  nodes: readonly DisplayNode[];
};

export function isRestQuery(query: string | null | undefined): boolean {
  return !query || query.trim() === '';
}

export function searchHitIds(query: string, resultIds: readonly string[]): string[] {
  return isRestQuery(query) ? [] : [...resultIds];
}

export function emphasisMode(input: {
  query: string;
  resultIds?: readonly string[];
  hoverId: string | null;
  selectedId: string | null;
}): EmphasisMode {
  if (input.selectedId || input.hoverId) return 'focus';
  if (!isRestQuery(input.query)) return 'search';
  return 'rest';
}

export function neighborhoodIds(
  focusId: string,
  edges: readonly ProjectionEdge[],
  nodes: readonly DisplayNode[],
): Set<string> {
  const focus = nodes.find((node) => node.id === focusId);
  if (!focus || focus.kind === 'continuity_hub') return new Set([focusId]);
  const ids = new Set<string>([focusId]);
  for (const edge of edges) {
    if (edge.from === focusId) ids.add(edge.to);
    if (edge.to === focusId) ids.add(edge.from);
  }
  return ids;
}

function kindOf(nodeId: string, nodes: readonly DisplayNode[]): DisplayNode['kind'] | undefined {
  return nodes.find((node) => node.id === nodeId)?.kind;
}

export function nodeWeight(nodeId: string, input: EmphasisInput): number {
  const mode = emphasisMode(input);
  if (mode === 'rest') return 0.35;
  if (mode === 'search') {
    const hits = searchHitIds(input.query, input.resultIds);
    return hits.includes(nodeId) ? 1 : 0.12;
  }
  const kind = kindOf(nodeId, input.nodes);
  const selected = input.selectedId;
  const hover = input.hoverId;
  if (hover && !selected) return nodeId === hover ? 1 : 0.35;
  const selectedHood = selected ? neighborhoodIds(selected, input.edges, input.nodes) : new Set<string>();
  if (selected && hover && hover !== selected) {
    if (nodeId === selected) return 1;
    if (nodeId === hover) return 0.92;
    if (selectedHood.has(nodeId)) return 0.78;
    if (kind === 'topic' || kind === 'continuity_hub') return 0.48;
    return 0.28;
  }
  if (selected) {
    const hood = neighborhoodIds(selected, input.edges, input.nodes);
    if (hood.has(nodeId)) return nodeId === selected ? 1 : 0.82;
    if (kind === 'topic' || kind === 'continuity_hub') return 0.48;
    return 0.28;
  }
  return 0.35;
}

export function pathInNeighborhood(
  from: string | undefined,
  to: string | undefined,
  focusId: string | null,
  edges: readonly ProjectionEdge[],
  nodes: readonly DisplayNode[],
): boolean {
  if (!focusId) return false;
  const hood = neighborhoodIds(focusId, edges, nodes);
  return Boolean((from && hood.has(from)) || (to && hood.has(to)));
}
