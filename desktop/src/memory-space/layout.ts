import { evidenceParentId } from './graphPaths';
import type { DisplayNode, MemoryProjection, Point3, ProjectionNode } from './types';

export function hash01(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i += 1) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

export function nodeLabel(node: ProjectionNode): string {
  if (node.kind === 'continuity_hub') return `${node.label_primary} / ${node.label_secondary}`;
  return node.label;
}

const SHELL_RADIUS = 2.1;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
export const OVERVIEW_REST_CAMERA = { yaw: 0, pitch: 0.25, distance: 7.8 } as const;

function overviewPoint(x: number, y: number, depth: number): Point3 {
  const cy = Math.cos(OVERVIEW_REST_CAMERA.yaw);
  const sy = Math.sin(OVERVIEW_REST_CAMERA.yaw);
  const cp = Math.cos(OVERVIEW_REST_CAMERA.pitch);
  const sp = Math.sin(OVERVIEW_REST_CAMERA.pitch);
  return {
    x: -x * cy - y * sp * sy + depth * cp * sy,
    y: y * cp + depth * sp,
    z: x * sy - y * sp * cy + depth * cp * cy,
  };
}

function topicPosition(index: number, total: number): Point3 {
  return placeOnSphere(index, total, SHELL_RADIUS);
}

export type OrbitRing = {
  radiusX: number;
  radiusY: number;
  rotX: number;
  rotY: number;
  rotZ: number;
};

export function instrumentRings(): OrbitRing[] {
  return [
    { radiusX: 2.38, radiusY: 2.18, rotX: 0.18, rotY: 0.04, rotZ: 0.22 },
    { radiusX: 2.52, radiusY: 1.92, rotX: 0.86, rotY: 0.28, rotZ: -0.38 },
    { radiusX: 2.48, radiusY: 2.28, rotX: -0.46, rotY: 0.16, rotZ: 1.08 },
    { radiusX: 2.2, radiusY: 2.42, rotX: 0.32, rotY: -0.42, rotZ: 0.08 },
    { radiusX: 2.62, radiusY: 2.05, rotX: 1.12, rotY: -0.18, rotZ: 0.55 },
  ];
}

function topicScale(node: Extract<ProjectionNode, { kind: 'topic' }>): number {
  return 0.112 + Math.min(0.12, Math.log1p(node.fact_count + node.experience_count) * 0.046);
}

export function placeOnSphere(index: number, total: number, radius: number): Point3 {
  const count = Math.max(1, total);
  if (count === 1) {
    return { x: radius * 0.55, y: radius * 0.18, z: radius * Math.sqrt(1 - 0.55 ** 2 - 0.18 ** 2) };
  }
  if (count === 2) {
    const sign = index <= 0 ? 1 : -1;
    return { x: sign * radius * 0.82, y: sign * radius * 0.28, z: sign * radius * Math.sqrt(1 - 0.82 ** 2 - 0.28 ** 2) };
  }
  const y = 1 - ((index + 0.5) * 2) / count;
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = GOLDEN_ANGLE * index;
  return {
    x: radius * ring * Math.cos(theta),
    y: radius * y,
    z: radius * ring * Math.sin(theta),
  };
}

function unboundPocket(index: number, total: number, seed: string): Point3 {
  const angle = (Math.PI * 2 * index) / Math.max(1, total) + hash01(`${seed}|u`) * 0.5;
  const r = 0.14 + hash01(`${seed}|ur`) * 0.12;
  return overviewPoint(r * Math.cos(angle), -0.94 + r * Math.sin(angle) * 0.35, 0.2);
}

function parentTopicProjectionId(
  nodeId: string,
  projection: MemoryProjection,
): string | null {
  const parentPid = evidenceParentId(nodeId, projection.edges);
  if (!parentPid) return null;
  const topic = projection.nodes.find((item) => item.kind === 'topic' && item.projection_id === parentPid);
  return topic && topic.kind === 'topic' ? topic.projection_id : null;
}

function moonOffset(home: Point3, index: number, total: number): Point3 {
  const reach = 0.42 + Math.min(0.2, total * 0.028);
  const local = placeOnSphere(index, total, reach);
  return {
    x: home.x + local.x,
    y: home.y + local.y,
    z: home.z + local.z,
  };
}

export function layoutProjection(projection: MemoryProjection): DisplayNode[] {
  const topics = projection.nodes
    .filter((node) => node.kind === 'topic')
    .slice()
    .sort((a, b) => a.projection_id.localeCompare(b.projection_id));
  const topicHome = new Map<string, Point3>();
  topics.forEach((node, index) => {
    topicHome.set(node.projection_id, topicPosition(index, topics.length));
  });

  const moons = new Map<string, ProjectionNode[]>();
  for (const node of projection.nodes) {
    if (node.kind !== 'fact' && node.kind !== 'experience') continue;
    const key = parentTopicProjectionId(node.projection_id, projection) ?? `unbound:${node.kind}`;
    const list = moons.get(key) ?? [];
    list.push(node);
    moons.set(key, list);
  }

  return projection.nodes.map((node) => {
    if (node.kind === 'continuity_hub') {
      return {
        id: node.projection_id,
        kind: node.kind,
        label: nodeLabel(node),
        radius: 0.5,
        position: { x: 0, y: 0, z: 0 },
        node,
      };
    }
    if (node.kind === 'topic') {
      return {
        id: node.projection_id,
        kind: node.kind,
        label: nodeLabel(node),
        radius: topicScale(node),
        position: topicHome.get(node.projection_id) ?? { x: SHELL_RADIUS, y: 0, z: 0 },
        node,
      };
    }
    const homeKey = parentTopicProjectionId(node.projection_id, projection);
    const packKey = homeKey ?? `unbound:${node.kind}`;
    const pack = moons.get(packKey) ?? [node];
    const index = pack.findIndex((item) => item.projection_id === node.projection_id);
    const home = homeKey ? topicHome.get(homeKey) : undefined;
    const position = home
      ? moonOffset(home, Math.max(0, index), pack.length)
      : unboundPocket(Math.max(0, index), pack.length, node.projection_id);
    return {
      id: node.projection_id,
      kind: node.kind,
      label: nodeLabel(node),
      radius: node.kind === 'fact' ? (node.is_pinned ? 0.068 : 0.056) : 0.05,
      position,
      node,
    };
  });
}

export const OVERVIEW_FOV_DEG = 42;
const OVERVIEW_FOV_RAD = (OVERVIEW_FOV_DEG * Math.PI) / 180;

export function overviewFocalPx(height: number): number {
  return (height / 2) / Math.tan(OVERVIEW_FOV_RAD / 2);
}

export type OrbitPose = {
  pivot: Point3;
  azimuth: number;
  elevation: number;
  radius: number;
  aimPoint: Point3;
};

export type OrbitCamera = {
  yaw: number;
  pitch: number;
  distance: number;
  lookAt: Point3;
};

export function cameraWorldPosition(camera: OrbitCamera): Point3 {
  const cp = Math.cos(camera.pitch);
  const sp = Math.sin(camera.pitch);
  return {
    x: camera.lookAt.x - camera.distance * cp * Math.sin(camera.yaw),
    y: camera.lookAt.y - camera.distance * sp,
    z: camera.lookAt.z - camera.distance * cp * Math.cos(camera.yaw),
  };
}

export function worldFromOrbitPose(pose: OrbitPose): Point3 {
  const cp = Math.cos(pose.elevation);
  const sp = Math.sin(pose.elevation);
  return {
    x: pose.pivot.x - pose.radius * cp * Math.sin(pose.azimuth),
    y: pose.pivot.y - pose.radius * sp,
    z: pose.pivot.z - pose.radius * cp * Math.cos(pose.azimuth),
  };
}

export function orbitPoseFromWorld(world: Point3, pivot: Point3, aimPoint: Point3): OrbitPose {
  const rx = pivot.x - world.x;
  const ry = pivot.y - world.y;
  const rz = pivot.z - world.z;
  const radius = Math.max(1e-4, Math.hypot(rx, ry, rz));
  return {
    pivot: { x: pivot.x, y: pivot.y, z: pivot.z },
    azimuth: Math.atan2(rx, rz),
    elevation: Math.asin(Math.max(-0.999, Math.min(0.999, ry / radius))),
    radius,
    aimPoint: { x: aimPoint.x, y: aimPoint.y, z: aimPoint.z },
  };
}

export function orbitPoseFromCamera(camera: OrbitCamera, pivot: Point3, aimPoint: Point3): OrbitPose {
  return orbitPoseFromWorld(cameraWorldPosition(camera), pivot, aimPoint);
}

export function cameraFromOrbitPose(pose: OrbitPose): OrbitCamera {
  const world = worldFromOrbitPose(pose);
  const lookAt = pose.aimPoint;
  const relX = lookAt.x - world.x;
  const relY = lookAt.y - world.y;
  const relZ = lookAt.z - world.z;
  const distance = Math.max(1e-4, Math.hypot(relX, relY, relZ));
  return {
    yaw: Math.atan2(relX, relZ),
    pitch: Math.asin(Math.max(-0.999, Math.min(0.999, relY / distance))),
    distance,
    lookAt: { x: lookAt.x, y: lookAt.y, z: lookAt.z },
  };
}

export function projectToScreen(
  point: Point3,
  width: number,
  height: number,
  yaw: number,
  pitch: number,
  distance: number,
  lookAt: Point3,
): { x: number; y: number; depth: number } {
  const cy = Math.cos(yaw);
  const sy = Math.sin(yaw);
  const cp = Math.cos(pitch);
  const sp = Math.sin(pitch);
  const px = point.x - lookAt.x + distance * cp * sy;
  const py = point.y - lookAt.y + distance * sp;
  const pz = point.z - lookAt.z + distance * cp * cy;
  const xCam = -px * cy + pz * sy;
  const yCam = -px * sp * sy + py * cp - pz * sp * cy;
  const zCam = -px * cp * sy - py * sp - pz * cp * cy;
  const depth = -zCam;
  const focal = 1 / Math.tan(OVERVIEW_FOV_RAD / 2);
  const aspect = Math.max(1e-6, width / Math.max(1e-6, height));
  const denom = Math.max(1e-6, depth);
  const xNdc = (focal * xCam) / (aspect * denom);
  const yNdc = (focal * yCam) / denom;
  return {
    x: (xNdc * 0.5 + 0.5) * width,
    y: (0.5 - yNdc * 0.5) * height,
    depth,
  };
}

export function pickDisplayNode(
  nodes: readonly DisplayNode[],
  screenX: number,
  screenY: number,
  width: number,
  height: number,
  yaw: number,
  pitch: number,
  distance: number,
  lookAt: Point3,
): DisplayNode | null {
  const projected = nodes
    .map((node) => ({
      node,
      screen: projectToScreen(node.position, width, height, yaw, pitch, distance, lookAt),
    }))
    .filter((item) => item.screen.depth > 0)
    .sort((a, b) => a.screen.depth - b.screen.depth);
  for (const item of projected) {
    const hit = Math.max(
      10,
      item.node.radius * (overviewFocalPx(height) / Math.max(0.35, item.screen.depth)),
    );
    const dx = screenX - item.screen.x;
    const dy = screenY - item.screen.y;
    if (dx * dx + dy * dy <= hit * hit) return item.node;
  }
  return null;
}

export function focusLookAt(selected: DisplayNode | null): Point3 {
  if (!selected || selected.kind === 'continuity_hub') return { x: 0, y: 0, z: 0 };
  return {
    x: selected.position.x * 0.35,
    y: selected.position.y * 0.35,
    z: selected.position.z * 0.35,
  };
}

export function readingSurfaceMode(args: {
  width: number;
  preferList: boolean;
  reducedMotion: boolean;
}): 'scene' | 'dom' {
  if (args.preferList || args.reducedMotion || args.width < 900) return 'dom';
  return 'scene';
}
