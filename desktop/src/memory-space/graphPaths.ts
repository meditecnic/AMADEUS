import type { DisplayNode, Point3, ProjectionEdge } from './types';

export type PathClass = 'typed_relation' | 'field_belt' | 'soul_instrument';

export type GraphPath = {
  class: PathClass;
  kind?: ProjectionEdge['kind'];
  from?: string;
  to?: string;
  points: Point3[];
};

export const SOUL_LOCKUP = ['SOUL'] as const;

export function soulLockupLines(_primary?: string, secondary?: string): readonly [string] {
  const raw = (secondary || 'SOUL').trim().toUpperCase();
  if (!raw || raw === 'AMADEUS' || raw === 'A M A D E U S') return ['SOUL'];
  return [raw];
}

export function soulActivation(phase: 'soul' | 'overview'): 'expand' | 'inspect' {
  return phase === 'soul' ? 'expand' : 'inspect';
}

export type RevealState = {
  overviewOpen: boolean;
  expandedTopicIds?: readonly string[];
  query?: string;
  resultIds?: readonly string[];
};

export function visibleDisplayNodes(
  display: readonly DisplayNode[],
  edges: readonly ProjectionEdge[],
  state: RevealState,
): DisplayNode[] {
  if (!state.overviewOpen) return display.filter((node) => node.kind === 'continuity_hub');
  const expanded = new Set(state.expandedTopicIds ?? []);
  const hits = new Set((state.query || '').trim() ? (state.resultIds ?? []) : []);
  return display.filter((node) => {
    if (node.kind === 'continuity_hub' || node.kind === 'topic') return true;
    if (hits.has(node.id)) return true;
    const parent = evidenceParentId(node.id, edges);
    return Boolean(parent && expanded.has(parent));
  });
}

export function openTopicBranch(topicId: string, current: readonly string[] = []): string[] {
  if (current.length === 1 && current[0] === topicId) return [...current];
  return [topicId];
}

export const SOUL_MARK_LIFT = 0.1;

export function soulMarkOffset(camera: Point3, radius: number): Point3 {
  const len = Math.hypot(camera.x, camera.y, camera.z);
  const reach = radius + SOUL_MARK_LIFT;
  if (len < 0.001) return { x: 0, y: 0, z: reach };
  const scale = reach / len;
  return { x: camera.x * scale, y: camera.y * scale, z: camera.z * scale };
}

export function branchReveal(progress: number, topicPos: Point3, restPos: Point3): Point3 {
  const t = Math.max(0, Math.min(1, progress));
  const eased = t * t * (3 - 2 * t);
  return {
    x: topicPos.x + (restPos.x - topicPos.x) * eased,
    y: topicPos.y + (restPos.y - topicPos.y) * eased,
    z: topicPos.z + (restPos.z - topicPos.z) * eased,
  };
}

export function filamentPrefix(points: readonly Point3[], progress: number): Point3[] {
  if (points.length === 0) return [];
  const t = Math.max(0, Math.min(1, progress));
  if (t <= 0) return [points[0]];
  const last = Math.max(1, Math.ceil((points.length - 1) * t));
  return points.slice(0, last + 1);
}

export function evidenceParentId(
  nodeId: string,
  edges: readonly ProjectionEdge[],
): string | null {
  const hit = edges.find((edge) => edge.kind === 'has_topic' && edge.from === nodeId);
  return hit?.to ?? null;
}

function lerp(a: Point3, b: Point3, t: number): Point3 {
  return {
    x: a.x + (b.x - a.x) * t,
    y: a.y + (b.y - a.y) * t,
    z: a.z + (b.z - a.z) * t,
  };
}

function length(p: Point3): number {
  return Math.hypot(p.x, p.y, p.z);
}

function scale(p: Point3, s: number): Point3 {
  return { x: p.x * s, y: p.y * s, z: p.z * s };
}

function add(a: Point3, b: Point3): Point3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

function sub(a: Point3, b: Point3): Point3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function normalize(p: Point3): Point3 {
  const len = length(p);
  return len < 0.001 ? { x: 0, y: 1, z: 0 } : scale(p, 1 / len);
}

function cross(a: Point3, b: Point3): Point3 {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

function nearestOnPolyline(point: Point3, line: Point3[]): number {
  let best = Infinity;
  for (const sample of line) {
    const d = Math.hypot(point.x - sample.x, point.y - sample.y, point.z - sample.z);
    if (d < best) best = d;
  }
  return best;
}

function quadratic(a: Point3, c: Point3, b: Point3, steps: number): Point3[] {
  const points: Point3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const ab = lerp(a, c, t);
    const bc = lerp(c, b, t);
    points.push(lerp(ab, bc, t));
  }
  return points;
}

export function azimuthOf(point: Point3): number {
  return Math.atan2(point.z, point.x);
}

function outOfPlaneControl(a: Point3, b: Point3, index: number): Point3 {
  const mid = scale(add(a, b), 0.5);
  const chord = sub(b, a);
  let normal = cross(chord, mid);
  if (length(normal) < 0.001) normal = cross(chord, { x: 0, y: 1, z: 0 });
  if (length(normal) < 0.001) normal = { x: 0, y: 1, z: 0 };
  normal = normalize(normal);
  const sign = index % 2 === 0 ? 1 : -1;
  const lift = 0.48 + (index % 3) * 0.14;
  const outward = 0.16 + (index % 2) * 0.14;
  const radial = length(mid) < 0.001 ? { x: 0, y: 0, z: 1 } : normalize(mid);
  return add(add(mid, scale(normal, sign * lift)), scale(radial, outward));
}

export function constellationFieldBelt(
  display: readonly DisplayNode[],
  samples = 96,
): Point3[][] {
  const topics = display
    .filter((node) => node.kind === 'topic')
    .slice()
    .sort((a, b) => azimuthOf(a.position) - azimuthOf(b.position));
  if (topics.length === 0) return [];
  if (topics.length === 1) {
    const p = topics[0].position;
    const radial = normalize(p);
    let tangent = normalize(cross(radial, { x: 0, y: 1, z: 0 }));
    if (length(tangent) < 0.2) tangent = normalize(cross(radial, { x: 1, y: 0, z: 0 }));
    const binormal = normalize(cross(radial, tangent));
    const arc: Point3[] = [];
    const span = 0.62;
    const steps = Math.max(12, Math.floor(samples / 4));
    for (let i = 0; i <= steps; i += 1) {
      const t = -span + (2 * span * i) / steps;
      arc.push(
        add(add(p, scale(tangent, t)), scale(binormal, Math.sin(t * 1.4) * 0.22)),
      );
    }
    return [arc];
  }
  const segments: Point3[][] = [];
  const steps = Math.max(10, Math.floor(samples / topics.length));
  for (let i = 0; i < topics.length - 1; i += 1) {
    const a = topics[i].position;
    const b = topics[i + 1].position;
    segments.push(quadratic(a, outOfPlaneControl(a, b, i), b, steps));
  }
  return segments;
}

export function topicBeltPoints(display: readonly DisplayNode[], samples = 96): Point3[] {
  return constellationFieldBelt(display, samples).flat();
}

export function distanceToTopicBelt(point: Point3, display: readonly DisplayNode[]): number {
  const segments = constellationFieldBelt(display, 128);
  if (segments.length === 0) return Number.POSITIVE_INFINITY;
  return Math.min(...segments.map((segment) => nearestOnPolyline(point, segment)));
}

export function isClosedPlanarRing(segments: Point3[][]): boolean {
  const points = segments.flat();
  if (points.length < 8) return false;
  const first = points[0];
  const last = points[points.length - 1];
  const closed = Math.hypot(first.x - last.x, first.y - last.y, first.z - last.z) < 0.08;
  const ys = points.map((point) => point.y);
  const yRange = Math.max(...ys) - Math.min(...ys);
  return closed && yRange < 0.12;
}

export function hasSpokeWheel(paths: readonly GraphPath[], display: readonly DisplayNode[]): boolean {
  const topics = display.filter((node) => node.kind === 'topic');
  if (topics.length < 2) return false;
  const spokeTargets = new Set(
    paths
      .filter((path) => path.class === 'typed_relation' && path.kind === 'hub_to_anchor')
      .map((path) => path.to),
  );
  return topics.every((topic) => spokeTargets.has(topic.id));
}

function nearestTopic(point: Point3, display: readonly DisplayNode[]): DisplayNode | null {
  let best: DisplayNode | null = null;
  let bestD = Infinity;
  for (const node of display) {
    if (node.kind !== 'topic') continue;
    const d = length(sub(point, node.position));
    if (d < bestD) {
      bestD = d;
      best = node;
    }
  }
  return best;
}

export function semanticFilaments(
  display: readonly DisplayNode[],
  edges: readonly ProjectionEdge[],
): GraphPath[] {
  const byId = new Map(display.map((node) => [node.id, node]));
  const paths: GraphPath[] = [];
  for (const edge of edges) {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    if (!from || !to) continue;
    const start =
      from.kind === 'continuity_hub'
        ? scale(to.position, from.radius / Math.max(0.001, length(to.position)))
        : from.position;
    const end = to.position;
    if (edge.kind === 'hub_to_anchor') {
      paths.push({
        class: 'typed_relation',
        kind: edge.kind,
        from: edge.from,
        to: edge.to,
        points: [start, end],
      });
      continue;
    }
    let control: Point3;
    if (edge.kind === 'has_topic') {
      const mid = scale(add(start, end), 0.5);
      const lift = 0.05;
      control = add(mid, { x: 0, y: lift, z: 0 });
    } else {
      const mid = scale(add(start, end), 0.5);
      const topic = nearestTopic(end, display);
      const away = topic ? normalize(sub(end, topic.position)) : { x: 0, y: 1, z: 0 };
      control = add(mid, scale(away, 0.22));
      control = add(control, { x: 0, y: 0.12, z: 0 });
    }
    const steps = edge.kind === 'has_topic' ? 12 : 24;
    paths.push({
      class: 'typed_relation',
      kind: edge.kind,
      from: edge.from,
      to: edge.to,
      points: quadratic(start, control, end, steps),
    });
  }
  return paths;
}

export function visibleOverviewPaths(input: {
  display: readonly DisplayNode[];
  edges: readonly ProjectionEdge[];
  query?: string;
  hoverId?: string | null;
  selectedId?: string | null;
  expanded?: boolean;
}): GraphPath[] {
  if (!(input.expanded ?? true)) return [];
  return semanticFilaments(input.display, input.edges);
}
