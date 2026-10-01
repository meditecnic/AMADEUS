import { evidenceParentId } from './graphPaths';
import {
  cameraFromOrbitPose,
  layoutProjection,
  orbitPoseFromCamera,
  overviewFocalPx,
  OVERVIEW_REST_CAMERA,
  projectToScreen,
  type OrbitPose,
} from './layout';
import type { OverviewDisclosurePlan } from './overviewPresentation';
import type { MemoryProjection, Point3 } from './types';

function freezePoint(point: Point3 | ReadonlyPoint3): ReadonlyPoint3 {
  return Object.freeze({ x: point.x, y: point.y, z: point.z });
}

export function restLayoutFromProjection(projection: MemoryProjection) {
  return new Map(
    layoutProjection(projection).map((node) => [
      node.id,
      Object.freeze({
        position: freezePoint(node.position),
        radius: node.radius,
        kind: node.kind,
      }),
    ]),
  );
}

export type DeepReadonly<T> =
  T extends Map<infer K, infer V> ? ReadonlyMap<DeepReadonly<K>, DeepReadonly<V>> :
  T extends ReadonlyMap<infer K, infer V> ? ReadonlyMap<DeepReadonly<K>, DeepReadonly<V>> :
  T extends Set<infer U> ? ReadonlySet<DeepReadonly<U>> :
  T extends Array<infer U> ? ReadonlyArray<DeepReadonly<U>> :
  T extends (...args: infer A) => infer R ? (...args: A) => R :
  T extends object ? { readonly [K in keyof T]: DeepReadonly<T[K]> } :
  T;

export type Rect = Readonly<{ x: number; y: number; width: number; height: number }>;
export type Insets = Readonly<{ top: number; right: number; bottom: number; left: number }>;
export type ReadonlyPoint3 = Readonly<{ x: number; y: number; z: number }>;

export type LiveNode = Readonly<{
  id: string;
  kind: 'continuity_hub' | 'topic' | 'fact' | 'experience';
  position: ReadonlyPoint3;
  hitRadius: number;
  parentId: string | null;
}>;

export type LiveEdge = Readonly<{
  from: string;
  to: string;
  kind: string;
  points: readonly ReadonlyPoint3[];
}>;

export type ReadonlyOverviewDisclosurePlan = DeepReadonly<OverviewDisclosurePlan>;

export type ReadonlyMotionPresentation = Readonly<{
  scopeKey: string;
  plan: ReadonlyOverviewDisclosurePlan;
  restLayout: ReadonlyMap<string, Readonly<{
    position: ReadonlyPoint3;
    radius: number;
    kind: LiveNode['kind'];
  }>>;
  selectedId: string | null;
  focusedId: string | null;
  expandedTopicIds: readonly string[];
  viewport: Rect;
  insets: Insets;
  renderSurface: '3d' | 'list';
  reducedMotion: boolean;
}>;

export type MotionSeed = ReadonlyMotionPresentation;

export type PresentationChangeCause =
  | 'selection'
  | 'reselect'
  | 'disclosure'
  | 'viewport'
  | 'auxiliary-surface'
  | 'render-surface'
  | 'motion-preference'
  | 'post-reconcile-replacement';

export type MotionEvent =
  | { readonly type: 'tick'; readonly nowMs: number; readonly paused?: boolean }
  | {
      readonly type: 'userCamera';
      readonly source: 'pointer' | 'wheel' | 'keyboard-camera';
      readonly dyaw?: number;
      readonly dpitch?: number;
      readonly zoomMul?: number;
      readonly nowMs?: number;
    }
  | {
      readonly type: 'activityChanged';
      readonly hovered: boolean;
      readonly pointerActive?: boolean;
      readonly nowMs: number;
    }
  | {
      readonly type: 'presentationChanged';
      readonly cause: PresentationChangeCause;
      readonly presentation: ReadonlyMotionPresentation;
    }
  | { readonly type: 'scopeChanged'; readonly seed: MotionSeed };

export type OverviewFrame = Readonly<{
  generation: number;
  ownership: 'rest' | 'system' | 'user';
  camera: Readonly<{ yaw: number; pitch: number; distance: number; lookAt: ReadonlyPoint3 }>;
  nodes: ReadonlyMap<string, LiveNode>;
  edges: readonly LiveEdge[];
  projected: ReadonlyMap<string, Readonly<{ x: number; y: number }>>;
  viewport: Rect;
  safeRect: Rect;
  inFlightIds: readonly string[];
}>;

type _ReadonlyXYZ = { readonly x: number; readonly y: number; readonly z: number };
type _AssertTrue<T extends true> = T;
type _ProofLiveNode = _AssertTrue<LiveNode['position'] extends _ReadonlyXYZ ? true : false>;
type _ProofCameraLookAt = _AssertTrue<OverviewFrame['camera']['lookAt'] extends _ReadonlyXYZ ? true : false>;
type _ProofEdgePoint = _AssertTrue<LiveEdge['points'][number] extends _ReadonlyXYZ ? true : false>;
type _ProofProjected = _AssertTrue<
  NonNullable<ReturnType<OverviewFrame['projected']['get']>> extends { readonly x: number; readonly y: number }
    ? true
    : false
>;
export type OverviewMotionReadonlyProof = _ProofLiveNode | _ProofCameraLookAt | _ProofEdgePoint | _ProofProjected;

export type MotionState = { readonly __brand: 'MotionState' };

type Camera = { yaw: number; pitch: number; distance: number; lookAt: Point3 };
type Flight = { progress: number; target: 0 | 1 };
type Snapshot = {
  readonly id: string;
  readonly kind: LiveNode['kind'];
  readonly parentId: string | null;
  readonly position: ReadonlyPoint3;
  readonly hitRadius: number;
  readonly mergeTarget: string | null;
  readonly generation: number;
  readonly progress: number;
  readonly mode: 'retract' | 'expand';
  readonly edge: Readonly<{ from: string; to: string; kind: string }> | null;
};

type SystemTween = {
  generation: number;
  from: OrbitPose;
  to: OrbitPose;
  startMs: number | null;
  durationMs: number;
  followId: string | null;
};

type Internal = {
  generation: number;
  ownership: 'rest' | 'system' | 'user';
  camera: Camera;
  system: SystemTween | null;
  lastTickMs: number | null;
  presentation: ReadonlyMotionPresentation;
  flights: Map<string, Flight>;
  snapshots: Map<string, Snapshot>;
  hovered: boolean;
  pointerActive: boolean;
  lastActivityMs: number;
  ambientStartedAt: number | null;
};

// Handles belong to this module instance; ConstellationStage remounts on Fast Refresh.
const STORE = new WeakMap<MotionState, Internal>();
const ORIGIN: Point3 = { x: 0, y: 0, z: 0 };
const REST_CAMERA: Camera = { ...OVERVIEW_REST_CAMERA, lookAt: ORIGIN };
const ZOOM_MIN = 1.15;
const ZOOM_MAX = 9;
const HALO_SCALE = 1.45;
const MIN_HIT_PX = 16;
const BRANCH_MS = 220;
const AMBIENT_DELAY_MS = 4000;
const AMBIENT_SPEED = 0.025;
const SOFT_INSET = 0.22;
const TOPIC_APPARENT = { min: 80, target: 98, max: 132 };
const EVIDENCE_APPARENT = { min: 40, target: 56, max: 72 };
const DT_MAX_MS = 1000;
const MERGE_EPS = 0.02;

function wrap(inner: Internal): MotionState {
  const state: MotionState = { __brand: 'MotionState' };
  STORE.set(state, inner);
  return state;
}

function unwrap(state: MotionState): Internal {
  const inner = STORE.get(state);
  if (!inner) throw new Error('invalid MotionState');
  return inner;
}

function copyPoint(point: Point3 | ReadonlyPoint3): Point3 {
  return { x: point.x, y: point.y, z: point.z };
}

function copyCam(camera: Camera): Camera {
  return {
    yaw: camera.yaw,
    pitch: camera.pitch,
    distance: camera.distance,
    lookAt: copyPoint(camera.lookAt),
  };
}

function copyOrbit(pose: OrbitPose): OrbitPose {
  return {
    pivot: copyPoint(pose.pivot),
    azimuth: pose.azimuth,
    elevation: pose.elevation,
    radius: pose.radius,
    aimPoint: copyPoint(pose.aimPoint),
  };
}

function freezeCam(camera: Camera): OverviewFrame['camera'] {
  return Object.freeze({
    yaw: camera.yaw,
    pitch: camera.pitch,
    distance: camera.distance,
    lookAt: freezePoint(camera.lookAt),
  });
}

function cloneInternal(inner: Internal): Internal {
  return {
    generation: inner.generation,
    ownership: inner.ownership,
    camera: copyCam(inner.camera),
    system: inner.system
      ? {
          generation: inner.system.generation,
          from: copyOrbit(inner.system.from),
          to: copyOrbit(inner.system.to),
          startMs: inner.system.startMs,
          durationMs: inner.system.durationMs,
          followId: inner.system.followId,
        }
      : null,
    lastTickMs: inner.lastTickMs,
    presentation: inner.presentation,
    flights: new Map([...inner.flights].map(([id, flight]) => [id, { ...flight }])),
    snapshots: new Map(inner.snapshots),
    hovered: inner.hovered,
    pointerActive: inner.pointerActive,
    lastActivityMs: inner.lastActivityMs,
    ambientStartedAt: inner.ambientStartedAt,
  };
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpPoint(a: Point3 | ReadonlyPoint3, b: Point3 | ReadonlyPoint3, t: number): Point3 {
  return { x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), z: lerp(a.z, b.z, t) };
}

function ease(t: number): number {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
}

function clampPitch(value: number): number {
  return Math.max(-0.9, Math.min(0.9, value));
}

function clampZoom(value: number, minimum = ZOOM_MIN): number {
  return Math.max(minimum, Math.min(ZOOM_MAX, value));
}

function safeRectOf(viewport: Rect, insets: Insets): Rect {
  return {
    x: viewport.x + insets.left,
    y: viewport.y + insets.top,
    width: Math.max(0, viewport.width - insets.left - insets.right),
    height: Math.max(0, viewport.height - insets.top - insets.bottom),
  };
}

function projectCam(
  camera: Camera,
  point: Point3 | ReadonlyPoint3,
  viewport: Rect,
): { x: number; y: number; depth: number } {
  return projectToScreen(
    copyPoint(point),
    viewport.width,
    viewport.height,
    camera.yaw,
    camera.pitch,
    camera.distance,
    camera.lookAt,
  );
}

function pointInRect(rect: Rect, point: { x: number; y: number }, pad = 8): boolean {
  return point.x >= rect.x + pad
    && point.x <= rect.x + rect.width - pad
    && point.y >= rect.y + pad
    && point.y <= rect.y + rect.height - pad;
}

function apparentPx(
  camera: Camera,
  point: Point3 | ReadonlyPoint3,
  radius: number,
  viewport: Rect,
): number {
  const center = projectCam(camera, point, viewport);
  return radius * overviewFocalPx(viewport.height) / Math.max(0.35, center.depth);
}

function contextIds(
  presentation: ReadonlyMotionPresentation,
  nodes: Map<string, LiveNode>,
  selected: LiveNode | undefined,
): string[] {
  const hub = presentation.plan.envelope.center.projection_id;
  const ids = new Set<string>([hub]);
  if (!selected) return [...ids].filter((id) => nodes.has(id));
  ids.add(selected.id);
  if (selected.kind === 'topic') {
    const group = presentation.plan.topicGroups.find((item) => item.topicProjectionId === selected.id);
    for (const id of group?.representativeIds ?? []) ids.add(id);
  } else if (selected.kind === 'fact' || selected.kind === 'experience') {
    if (selected.parentId) ids.add(selected.parentId);
  }
  return [...ids].filter((id) => nodes.has(id));
}

function inSoftZone(rect: Rect, point: { x: number; y: number }): boolean {
  const padX = rect.width * SOFT_INSET;
  const padY = rect.height * SOFT_INSET;
  return point.x >= rect.x + padX && point.x <= rect.x + rect.width - padX
    && point.y >= rect.y + padY && point.y <= rect.y + rect.height - padY;
}

function shortestAngle(from: number, to: number): number {
  let delta = to - from;
  while (delta > Math.PI) delta -= Math.PI * 2;
  while (delta < -Math.PI) delta += Math.PI * 2;
  return delta;
}

function visualRadiusPx(
  camera: Camera,
  point: Point3 | ReadonlyPoint3,
  radius: number,
  viewport: Rect,
): number {
  const center = projectCam(camera, point, viewport);
  return radius * overviewFocalPx(viewport.height) / Math.max(0.35, center.depth);
}

function diskCovers(
  camera: Camera,
  occluder: LiveNode,
  target: LiveNode,
  viewport: Rect,
): boolean {
  const front = projectCam(camera, occluder.position, viewport);
  const aim = projectCam(camera, target.position, viewport);
  if (front.depth >= aim.depth - 0.02) return false;
  const radius = visualRadiusPx(camera, occluder.position, occluder.hitRadius, viewport);
  const targetRadius = visualRadiusPx(camera, target.position, target.hitRadius, viewport);
  return Math.hypot(front.x - aim.x, front.y - aim.y) < radius + targetRadius * 0.65;
}

function aimFromPivot(pivot: Point3, context: readonly Point3[]): Point3 {
  if (!context.length) return copyPoint(pivot);
  let x = 0;
  let y = 0;
  let z = 0;
  for (const point of context) {
    x += point.x;
    y += point.y;
    z += point.z;
  }
  const count = context.length;
  return {
    x: pivot.x + (x / count - pivot.x) * 0.14,
    y: pivot.y + (y / count - pivot.y) * 0.14,
    z: pivot.z + (z / count - pivot.z) * 0.14,
  };
}

function poseCamera(pose: OrbitPose): Camera {
  const next = cameraFromOrbitPose(pose);
  return {
    yaw: next.yaw,
    pitch: clampPitch(next.pitch),
    distance: clampZoom(next.distance),
    lookAt: copyPoint(next.lookAt),
  };
}

function radiusForApparent(
  base: OrbitPose,
  live: LiveNode,
  viewport: Rect,
  targetPx: number,
): number {
  let lo = ZOOM_MIN;
  let hi = ZOOM_MAX;
  let best = clampZoom(base.radius);
  for (let i = 0; i < 12; i += 1) {
    const mid = (lo + hi) / 2;
    const size = apparentPx(poseCamera({ ...base, radius: mid }), live.position, live.hitRadius, viewport);
    if (size < targetPx) hi = mid;
    else {
      lo = mid;
      best = mid;
    }
  }
  return clampZoom(best);
}

function compositionInterference(
  camera: Camera,
  live: LiveNode,
  nodes: Map<string, LiveNode>,
  ids: readonly string[],
  viewport: Rect,
): number {
  const selectedRadius = visualRadiusPx(camera, live.position, live.hitRadius, viewport);
  const dominanceLimit = Math.max(
    selectedRadius * 2,
    Math.min(150, Math.min(viewport.width, viewport.height) * 0.22),
  );
  const requiredIds = new Set(ids.filter((id) => id !== live.id));
  let penalty = 0;
  for (const [id, node] of nodes) {
    if (id === live.id) continue;
    const nodeAt = projectCam(camera, node.position, viewport);
    const nodeRadius = visualRadiusPx(camera, node.position, node.hitRadius, viewport);
    const visible = nodeAt.depth > 0.2
      && nodeAt.x + nodeRadius >= viewport.x
      && nodeAt.x - nodeRadius <= viewport.x + viewport.width
      && nodeAt.y + nodeRadius >= viewport.y
      && nodeAt.y - nodeRadius <= viewport.y + viewport.height;
    if (visible && nodeRadius > dominanceLimit) {
      penalty += Math.min(220, (nodeRadius - dominanceLimit) * 0.9);
    }
    if (!requiredIds.has(id) || node.kind === 'continuity_hub') continue;
    const contextOccluded = [...nodes.entries()].some(([otherId, other]) => (
      otherId !== id && diskCovers(camera, other, node, viewport)
    ));
    if (contextOccluded) penalty += 90;
  }
  return penalty;
}

function scorePose(
  pose: OrbitPose,
  live: LiveNode,
  nodes: Map<string, LiveNode>,
  ids: readonly string[],
  viewport: Rect,
  safe: Rect,
  travel: number,
): number {
  const camera = poseCamera(pose);
  const projected = projectCam(camera, live.position, viewport);
  const band = live.kind === 'topic' ? TOPIC_APPARENT : EVIDENCE_APPARENT;
  const size = apparentPx(camera, live.position, live.hitRadius, viewport);
  let score = 0;
  if (projected.depth > 0.2 && size >= Math.min(18, band.min * 0.4)) score += 80;
  let occluded = false;
  for (const [id, node] of nodes) {
    if (id === live.id) continue;
    if (diskCovers(camera, node, live, viewport)) {
      occluded = true;
      break;
    }
  }
  score += occluded ? -50 : 110;
  score -= compositionInterference(camera, live, nodes, ids, viewport);
  if (size >= band.min && size <= band.max) score += 42;
  else score += Math.max(0, 36 - Math.abs(size - band.target) * 0.35);
  if (inSoftZone(safe, projected)) score += 28;
  else if (pointInRect(safe, projected, 8)) score += 10;
  const parent = live.parentId ? nodes.get(live.parentId) : undefined;
  if (parent) {
    const parentProjection = projectCam(camera, parent.position, viewport);
    score += parentProjection.depth > 0.2 && pointInRect(viewport, parentProjection, 20) ? 28 : -80;
  }
  for (const id of ids) {
    if (id === live.id) continue;
    const node = nodes.get(id);
    if (node && node.kind !== 'continuity_hub' && pointInRect(safe, projectCam(camera, node.position, viewport), 8)) {
      score += 6;
    }
  }
  const hub = [...nodes.values()].find((node) => node.kind === 'continuity_hub');
  if (hub) {
    const hubProjection = projectCam(camera, hub.position, viewport);
    score += hubProjection.depth > 0.2 && pointInRect(viewport, hubProjection, 20) ? 64 : -140;
  }
  if ((live.kind === 'fact' || live.kind === 'experience') && parent) {
    if (projectCam(camera, live.position, viewport).depth < projectCam(camera, parent.position, viewport).depth) {
      score += 12;
    }
  }
  if (hub && (live.kind === 'fact' || live.kind === 'experience')) {
    if (projectCam(camera, live.position, viewport).depth < projectCam(camera, hub.position, viewport).depth) {
      score += 8;
    }
  }
  if (hub && diskCovers(camera, hub, live, viewport)
    && projectCam(camera, hub.position, viewport).depth < projectCam(camera, live.position, viewport).depth - 0.02) {
    score -= 90;
  }
  score -= travel * 8;
  return score;
}

function lerpOrbit(from: OrbitPose, to: OrbitPose, t: number, pivot: Point3): OrbitPose {
  const k = ease(t);
  // Follow a moving branch gradually instead of dragging the entire camera on frame one.
  const movingPivot = lerpPoint(from.pivot, pivot, k);
  const fromBias = {
    x: from.aimPoint.x - from.pivot.x,
    y: from.aimPoint.y - from.pivot.y,
    z: from.aimPoint.z - from.pivot.z,
  };
  const toBias = {
    x: to.aimPoint.x - to.pivot.x,
    y: to.aimPoint.y - to.pivot.y,
    z: to.aimPoint.z - to.pivot.z,
  };
  return {
    pivot: copyPoint(movingPivot),
    azimuth: from.azimuth + shortestAngle(from.azimuth, to.azimuth) * k,
    elevation: lerp(from.elevation, to.elevation, k),
    radius: lerp(from.radius, to.radius, k),
    aimPoint: {
      x: movingPivot.x + lerp(fromBias.x, toBias.x, k),
      y: movingPivot.y + lerp(fromBias.y, toBias.y, k),
      z: movingPivot.z + lerp(fromBias.z, toBias.z, k),
    },
  };
}

function composeAim(pose: OrbitPose, live: LiveNode, viewport: Rect, safe: Rect): OrbitPose {
  let aim = copyPoint(pose.aimPoint);
  const maxBias = Math.max(0.12, pose.radius * (safe.width < 360 ? 0.72 : 0.42));
  for (let i = 0; i < 12; i += 1) {
    const camera = poseCamera({ ...pose, aimPoint: aim });
    const projected = projectCam(camera, live.position, viewport);
    const inSafe = pointInRect(safe, projected, 8);
    const inSoft = inSoftZone(safe, projected);
    if (inSafe && (safe.width < 280 || inSoft)) break;
    const targetX = safe.x + safe.width / 2;
    const targetY = safe.y + safe.height / 2;
    const shiftX = Math.max(
      safe.x + 8 - projected.x,
      Math.min(safe.x + safe.width - 8 - projected.x, (targetX - projected.x) * 0.5),
    );
    const shiftY = Math.max(
      safe.y + 8 - projected.y,
      Math.min(safe.y + safe.height - 8 - projected.y, (targetY - projected.y) * 0.5),
    );
    const worldPerPx = Math.max(0.35, projected.depth) / overviewFocalPx(viewport.height);
    const dx = -shiftX * worldPerPx;
    const dy = -shiftY * worldPerPx;
    const cy = Math.cos(camera.yaw);
    const sy = Math.sin(camera.yaw);
    const cp = Math.cos(camera.pitch);
    const sp = Math.sin(camera.pitch);
    aim = {
      x: aim.x - dx * cy,
      y: aim.y - dy * cp,
      z: aim.z + dx * sy + dy * sp,
    };
    const bias = Math.hypot(aim.x - pose.pivot.x, aim.y - pose.pivot.y, aim.z - pose.pivot.z);
    if (bias > maxBias) {
      const scale = maxBias / bias;
      aim = {
        x: pose.pivot.x + (aim.x - pose.pivot.x) * scale,
        y: pose.pivot.y + (aim.y - pose.pivot.y) * scale,
        z: pose.pivot.z + (aim.z - pose.pivot.z) * scale,
      };
    }
  }
  return { ...pose, aimPoint: aim };
}

function tweenDurationMs(from: OrbitPose, to: OrbitPose): number {
  const ang = Math.hypot(shortestAngle(from.azimuth, to.azimuth), to.elevation - from.elevation);
  const dolly = Math.abs(Math.log(Math.max(0.2, to.radius) / Math.max(0.2, from.radius)));
  const pan = Math.hypot(to.aimPoint.x - from.aimPoint.x, to.aimPoint.y - from.aimPoint.y,
    to.aimPoint.z - from.aimPoint.z) / Math.max(1, from.radius);
  if (ang < 0.035 && dolly < 0.04 && pan < 0.02) return 280;
  return Math.max(380, Math.min(680, 340 + ang * 180 + dolly * 140 + pan * 200));
}

function framingTarget(inner: Internal, presentation: ReadonlyMotionPresentation): OrbitPose {
  const probe: Internal = { ...inner, presentation };
  const geometry = liveGeometry(probe);
  const live = geometry.nodes.get(presentation.selectedId ?? '');
  const rest = live ? presentation.restLayout.get(live.id) : undefined;
  const focus = live && rest
    ? { ...live, position: freezePoint(rest.position), hitRadius: rest.radius }
    : live;
  const pivot = !focus || focus.kind === 'continuity_hub' ? copyPoint(ORIGIN) : copyPoint(focus.position);
  const current = orbitPoseFromCamera(inner.camera, pivot, inner.camera.lookAt);
  if (!focus || focus.kind === 'continuity_hub') {
    return {
      ...current,
      pivot: copyPoint(ORIGIN),
      aimPoint: copyPoint(ORIGIN),
      radius: clampZoom(Math.max(current.radius, REST_CAMERA.distance * 0.92)),
    };
  }
  const safe = safeRectOf(presentation.viewport, presentation.insets);
  const viewport = presentation.viewport;
  const ids = contextIds(presentation, geometry.nodes, focus);
  const contextPts = ids
    .map((id) => geometry.nodes.get(id)?.position)
    .filter((point): point is ReadonlyPoint3 => Boolean(point))
    .map((point) => copyPoint(point));
  const band = focus.kind === 'topic' ? TOPIC_APPARENT : EVIDENCE_APPARENT;
  const reachable = apparentPx(
    poseCamera({ ...current, radius: ZOOM_MIN, aimPoint: copyPoint(pivot) }),
    focus.position,
    focus.hitRadius,
    viewport,
  );
  const targetPx = Math.min(band.target, Math.max(12, reachable * 0.92));
  const recordFocus = focus.kind === 'fact' || focus.kind === 'experience';
  const aim = recordFocus ? copyPoint(pivot) : aimFromPivot(pivot, contextPts);
  const sized: OrbitPose = {
    ...current,
    pivot: copyPoint(pivot),
    aimPoint: aim,
    radius: radiusForApparent({ ...current, pivot: copyPoint(pivot), aimPoint: aim }, focus, viewport, targetPx),
  };
  if (recordFocus) {
    // A record is the next step inward, not a new overview of its ancestors.
    sized.radius = Math.min(current.radius, sized.radius);
    let best = composeAim(sized, focus, viewport, safe);
    let bestCost = Infinity;
    for (const turn of [0, 0.18, -0.18, 0.38, -0.38, 0.65, -0.65]) {
      for (const tilt of [0, 0.14, -0.14]) {
        const pose = composeAim({ ...sized, azimuth: current.azimuth + turn,
          elevation: clampPitch(current.elevation + tilt) }, focus, viewport, safe);
        const camera = poseCamera(pose);
        const blocked = [...geometry.nodes.values()].some(node =>
          node.id !== focus.id && diskCovers(camera, node, focus, viewport));
        const edgeInterference = compositionInterference(camera, focus, geometry.nodes, [], viewport);
        const cost = (blocked ? 1000 : 0) + edgeInterference * 0.5
          + Math.abs(turn) * 12 + Math.abs(tilt) * 18;
        if (cost < bestCost) { best = pose; bestCost = cost; }
      }
    }
    return best;
  }
  const sizedCam = poseCamera(sized);
  const sizedProj = projectCam(sizedCam, focus.position, viewport);
  const sizedOccluded = [...geometry.nodes.values()].some((node) => (
    node.id !== focus.id && diskCovers(sizedCam, node, focus, viewport)
  ));
  const sizedInterference = compositionInterference(
    sizedCam,
    focus,
    geometry.nodes,
    ids,
    viewport,
  );
  const sizedContextVisible = ids.every((id) => {
    if (id === focus.id) return true;
    const node = geometry.nodes.get(id);
    if (!node) return true;
    const projection = projectCam(sizedCam, node.position, viewport);
    return projection.depth > 0.2 && pointInRect(viewport, projection, 20);
  });
  if (
    apparentPx(sizedCam, focus.position, focus.hitRadius, viewport) >= targetPx * 0.96
    && pointInRect(safe, sizedProj, 8)
    && (safe.width < 280 || inSoftZone(safe, sizedProj))
    && !sizedOccluded
    && sizedInterference === 0
    && sizedContextVisible
  ) {
    return composeAim(sized, focus, viewport, safe);
  }
  const az0 = current.azimuth;
  const el0 = clampPitch(current.elevation);
  let best = sized;
  let bestScore = -Infinity;
  const hubNode = [...geometry.nodes.values()].find((node) => node.kind === 'continuity_hub');
  const contextAzimuth = hubNode
    ? Math.atan2(hubNode.position.x - pivot.x, hubNode.position.z - pivot.z)
    : az0;
  const contextElevation = hubNode
    ? Math.atan2(
      hubNode.position.y - pivot.y,
      Math.hypot(hubNode.position.x - pivot.x, hubNode.position.z - pivot.z),
    )
    : el0;
  const azimuths = [
    az0,
    az0 + 0.32,
    az0 - 0.32,
    az0 + 0.7,
    az0 - 0.7,
    az0 + 1.15,
    az0 - 1.15,
    az0 + 1.65,
    az0 - 1.65,
    az0 + 2.2,
    az0 - 2.2,
    az0 + Math.PI,
    contextAzimuth,
    contextAzimuth + 0.34,
    contextAzimuth - 0.34,
    contextAzimuth + 0.7,
    contextAzimuth - 0.7,
  ];
  const pitches = [
    el0,
    clampPitch(el0 + 0.14),
    clampPitch(el0 - 0.1),
    0.36,
    0.52,
    clampPitch(contextElevation),
    clampPitch(contextElevation + 0.16),
    clampPitch(contextElevation - 0.16),
  ];
  const radii = [1, 0.9, 1.08]
    .map((factor) => clampZoom(sized.radius * factor));
  for (const azimuth of azimuths) {
    for (const elevation of pitches) {
      for (const radius of radii) {
        const pose: OrbitPose = {
          pivot: copyPoint(pivot),
          azimuth,
          elevation,
          radius,
          aimPoint: aim,
        };
        const travel = Math.hypot(shortestAngle(az0, azimuth), elevation - el0);
        let score = scorePose(pose, focus, geometry.nodes, ids, viewport, safe, travel);
        if (score > bestScore) {
          bestScore = score;
          best = pose;
        }
      }
    }
  }
  let framed = composeAim(best, focus, viewport, safe);
  if (hubNode) {
    const cam = poseCamera(framed);
    const liveP = projectCam(cam, focus.position, viewport);
    const hubP = projectCam(cam, hubNode.position, viewport);
    const hubR = visualRadiusPx(cam, hubNode.position, hubNode.hitRadius, viewport);
    if (hubP.depth < liveP.depth - 0.02 && Math.hypot(liveP.x - hubP.x, liveP.y - hubP.y) < hubR) {
      framed = composeAim({ ...framed, azimuth: framed.azimuth + Math.PI }, focus, viewport, safe);
    }
  }
  return framed;
}

function visualEvidence(presentation: ReadonlyMotionPresentation): Set<string> {
  const ids = new Set<string>(presentation.plan.visualPlan.visibleEvidenceIds);
  if (presentation.selectedId) ids.add(presentation.selectedId);
  if (presentation.focusedId) ids.add(presentation.focusedId);
  return ids;
}

function parentOf(id: string, presentation: ReadonlyMotionPresentation): string | null {
  const edges = presentation.plan.envelope.edges as OverviewDisclosurePlan['envelope']['edges'];
  return evidenceParentId(id, edges);
}

function seedFlights(presentation: ReadonlyMotionPresentation): Map<string, Flight> {
  const flights = new Map<string, Flight>();
  for (const id of presentation.expandedTopicIds) {
    flights.set(id, { progress: 1, target: 1 });
  }
  return flights;
}

function captureSnapshots(inner: Internal, next: ReadonlyMotionPresentation): void {
  const keep = visualEvidence(next);
  const { nodes, edges } = liveGeometry(inner);
  for (const [id, node] of nodes) {
    if (node.kind !== 'fact' && node.kind !== 'experience') continue;
    if (keep.has(id) && next.restLayout.has(id) && next.plan.envelope.edges.some((edge) => (
      (edge.from === id || edge.to === id) && edge.kind === 'has_topic'
    ))) continue;
    const progress = node.parentId ? (inner.flights.get(node.parentId)?.progress ?? 1) : 1;
    const edge = edges.find((item) => item.from === id || item.to === id) ?? null;
    inner.snapshots.set(id, {
      id,
      kind: node.kind,
      parentId: node.parentId,
      position: freezePoint(node.position),
      hitRadius: node.hitRadius,
      mergeTarget: node.parentId,
      generation: inner.generation,
      progress,
      mode: 'retract',
      edge: edge ? Object.freeze({ from: edge.from, to: edge.to, kind: edge.kind }) : (
        node.parentId ? Object.freeze({ from: id, to: node.parentId, kind: 'has_topic' }) : null
      ),
    });
  }
}

function recaptureForExpand(inner: Internal, topicId: string): void {
  const { nodes } = liveGeometry(inner);
  const progress = inner.flights.get(topicId)?.progress ?? 0;
  for (const [id, node] of nodes) {
    if (node.parentId !== topicId) continue;
    inner.snapshots.set(id, {
      id,
      kind: node.kind,
      parentId: node.parentId,
      position: freezePoint(node.position),
      hitRadius: node.hitRadius,
      mergeTarget: node.parentId,
      generation: inner.generation,
      progress,
      mode: 'expand',
      edge: node.parentId ? Object.freeze({ from: id, to: node.parentId, kind: 'has_topic' }) : null,
    });
  }
}

function syncFlights(inner: Internal, presentation: ReadonlyMotionPresentation): void {
  const want = new Set(presentation.expandedTopicIds);
  for (const id of want) {
    const existing = inner.flights.get(id);
    if (!existing) inner.flights.set(id, { progress: presentation.reducedMotion ? 1 : 0, target: 1 });
    else if (existing.target === 0) {
      recaptureForExpand(inner, id);
      existing.target = 1;
    } else existing.target = 1;
  }
  for (const [id, flight] of inner.flights) {
    if (!want.has(id)) flight.target = 0;
  }
}

function advanceFlights(inner: Internal, dt: number): void {
  const delta = inner.presentation.reducedMotion ? 1 : dt / BRANCH_MS;
  for (const [id, flight] of inner.flights) {
    if (flight.progress < flight.target) flight.progress = Math.min(flight.target, flight.progress + delta);
    else if (flight.progress > flight.target) flight.progress = Math.max(flight.target, flight.progress - delta);
    if (flight.target === 0 && flight.progress <= MERGE_EPS) inner.flights.delete(id);
  }
  for (const [id, snap] of [...inner.snapshots]) {
    const parentId = snap.parentId;
    const progress = parentId ? (inner.flights.get(parentId)?.progress ?? 0) : 0;
    if (snap.mode === 'retract' && progress <= MERGE_EPS) inner.snapshots.delete(id);
    if (snap.mode === 'expand' && progress >= 1 - MERGE_EPS) inner.snapshots.delete(id);
  }
}

function liveGeometry(inner: Internal): {
  nodes: Map<string, LiveNode>;
  edges: LiveEdge[];
  inFlightIds: string[];
} {
  const { presentation } = inner;
  const visible = visualEvidence(presentation);
  const nodes = new Map<string, LiveNode>();
  const inFlight: string[] = [];
  for (const [id, rest] of presentation.restLayout) {
    if (rest.kind === 'continuity_hub' || rest.kind === 'topic') {
      nodes.set(id, {
        id,
        kind: rest.kind,
        position: freezePoint(rest.position),
        hitRadius: rest.radius,
        parentId: rest.kind === 'topic' ? presentation.plan.envelope.center.projection_id : null,
      });
    }
  }
  const consider = new Set([...visible, ...inner.snapshots.keys()]);
  for (const id of consider) {
    const rest = presentation.restLayout.get(id);
    const snap = inner.snapshots.get(id);
    const kind = rest?.kind ?? snap?.kind;
    if (kind !== 'fact' && kind !== 'experience') continue;
    const parentId = rest ? parentOf(id, presentation) : (snap?.parentId ?? null);
    const flight = parentId ? inner.flights.get(parentId) : undefined;
    const progress = parentId ? (flight?.progress ?? (presentation.expandedTopicIds.includes(parentId) ? 1 : 0)) : 1;
    if (progress <= MERGE_EPS && !visible.has(id) && (!snap || snap.mode === 'retract')) continue;
    const parentPos = parentId
      ? (nodes.get(parentId)?.position ?? presentation.restLayout.get(parentId)?.position)
      : undefined;
    let used: Point3;
    if (snap && snap.mode === 'retract') {
      const t = ease(1 - progress / Math.max(MERGE_EPS, snap.progress));
      used = lerpPoint(snap.position, parentPos ?? copyPoint(ORIGIN), t);
    } else if (snap && snap.mode === 'expand') {
      const dest = rest?.position ?? snap.position;
      const span = Math.max(MERGE_EPS, 1 - snap.progress);
      const t = ease(Math.max(0, Math.min(1, (progress - snap.progress) / span)));
      used = lerpPoint(snap.position, dest, t);
    } else {
      used = lerpPoint(parentPos ?? (rest?.position ?? copyPoint(ORIGIN)), rest?.position ?? copyPoint(ORIGIN), ease(progress));
    }
    nodes.set(id, {
      id,
      kind,
      position: freezePoint(used),
      hitRadius: rest?.radius ?? snap?.hitRadius ?? 0.05,
      parentId,
    });
    if (progress > MERGE_EPS && progress < 1 - MERGE_EPS) inFlight.push(id);
    if (snap && progress > MERGE_EPS && progress < 1 - MERGE_EPS) inFlight.push(id);
  }
  for (const [id, flight] of inner.flights) {
    if (flight.progress > MERGE_EPS && flight.progress < 1 - MERGE_EPS && !inFlight.includes(id)) {
      inFlight.push(id);
    }
  }
  const edges: LiveEdge[] = [];
  for (const edge of presentation.plan.envelope.edges) {
    const from = nodes.get(edge.from);
    const to = nodes.get(edge.to);
    if (!from || !to) continue;
    if (edge.kind === 'has_topic') {
      const child = from.kind === 'fact' || from.kind === 'experience' ? from : to;
      const progress = child.parentId ? (inner.flights.get(child.parentId)?.progress ?? 1) : 1;
      if (progress <= MERGE_EPS) continue;
    }
    edges.push({
      from: edge.from,
      to: edge.to,
      kind: edge.kind,
      points: [freezePoint(from.position), freezePoint(to.position)],
    });
  }
  const edgeKeys = new Set(edges.map((item) => `${item.kind}:${item.from}:${item.to}`));
  for (const snap of inner.snapshots.values()) {
    if (!snap.edge || !nodes.has(snap.id)) continue;
    const key = `${snap.edge.kind}:${snap.edge.from}:${snap.edge.to}`;
    if (edgeKeys.has(key)) continue;
    const from = nodes.get(snap.edge.from);
    const to = nodes.get(snap.edge.to);
    if (!from || !to) continue;
    edges.push({
      from: snap.edge.from,
      to: snap.edge.to,
      kind: snap.edge.kind,
      points: [freezePoint(from.position), freezePoint(to.position)],
    });
    edgeKeys.add(key);
  }
  return { nodes, edges, inFlightIds: [...new Set(inFlight)] };
}

function emit(inner: Internal): OverviewFrame {
  const { nodes, edges, inFlightIds } = liveGeometry(inner);
  const projected = new Map<string, { x: number; y: number }>();
  const vp = inner.presentation.viewport;
  const camera = copyCam(inner.camera);
  camera.yaw += ambientOffset(inner, inner.lastTickMs ?? inner.lastActivityMs);
  for (const [id, node] of nodes) {
    const screen = projectToScreen(
      node.position,
      vp.width,
      vp.height,
      camera.yaw,
      camera.pitch,
      camera.distance,
      camera.lookAt,
    );
    projected.set(id, Object.freeze({ x: screen.x, y: screen.y }));
  }
  return {
    generation: inner.generation,
    ownership: inner.ownership,
    camera: freezeCam(camera),
    nodes,
    edges,
    projected,
    viewport: Object.freeze({ ...vp }),
    safeRect: Object.freeze(safeRectOf(vp, inner.presentation.insets)),
    inFlightIds,
  };
}

function livePivot(inner: Internal, followId: string | null): Point3 {
  if (!followId) return copyPoint(ORIGIN);
  const live = liveGeometry(inner).nodes.get(followId);
  return live ? copyPoint(live.position) : copyPoint(ORIGIN);
}

function startSystem(inner: Internal, presentation: ReadonlyMotionPresentation, resetOverview = false): void {
  bakeAmbient(inner, inner.lastTickMs ?? inner.lastActivityMs);
  inner.generation += 1;
  inner.presentation = presentation;
  const to = resetOverview
    ? orbitPoseFromCamera(REST_CAMERA, ORIGIN, ORIGIN)
    : framingTarget(inner, presentation);
  const followId = presentation.selectedId
    && liveGeometry(inner).nodes.get(presentation.selectedId)?.kind !== 'continuity_hub'
    ? presentation.selectedId
    : null;
  const pivot = livePivot(inner, followId);
  const from = orbitPoseFromCamera(inner.camera, pivot, inner.camera.lookAt);
  inner.system = {
    generation: inner.generation,
    from: copyOrbit(from),
    to: copyOrbit(to),
    startMs: null,
    durationMs: tweenDurationMs(from, to),
    followId,
  };
  inner.ownership = 'system';
  inner.ambientStartedAt = null;
  if (presentation.reducedMotion) completeSystem(inner);
}

function completeSystem(inner: Internal): void {
  if (inner.system) {
    const pivot = livePivot(inner, inner.system.followId);
    inner.camera = poseCamera(lerpOrbit(inner.system.from, inner.system.to, 1, pivot));
  }
  inner.system = null;
  inner.ownership = inner.ownership === 'user' ? 'user' : 'rest';
  inner.lastActivityMs = inner.lastTickMs ?? inner.lastActivityMs;
}

function completeReduced(inner: Internal): void {
  for (const flight of inner.flights.values()) flight.progress = flight.target;
  for (const [id, flight] of [...inner.flights]) {
    if (flight.target === 0 && flight.progress <= MERGE_EPS) inner.flights.delete(id);
  }
  inner.snapshots.clear();
  if (inner.system) completeSystem(inner);
  else if (inner.ownership !== 'user') inner.ownership = 'rest';
}

function advanceSystem(inner: Internal, nowMs: number): void {
  if (!inner.system || inner.ownership !== 'system') return;
  if (inner.system.startMs === null) inner.system.startMs = nowMs;
  const pivot = livePivot(inner, inner.system.followId);
  inner.system.to = {
    ...copyOrbit(inner.system.to),
    pivot: copyPoint(pivot),
    aimPoint: {
      x: pivot.x + (inner.system.to.aimPoint.x - inner.system.to.pivot.x),
      y: pivot.y + (inner.system.to.aimPoint.y - inner.system.to.pivot.y),
      z: pivot.z + (inner.system.to.aimPoint.z - inner.system.to.pivot.z),
    },
  };
  const elapsed = Math.max(0, nowMs - inner.system.startMs);
  const t = elapsed / Math.max(1, inner.system.durationMs);
  if (t >= 1) {
    completeSystem(inner);
    return;
  }
  inner.camera = poseCamera(lerpOrbit(inner.system.from, inner.system.to, t, pivot));
}

function ambientOffset(inner: Internal, nowMs: number): number {
  if (inner.ambientStartedAt == null) return 0;
  const t = Math.max(0, (nowMs - inner.ambientStartedAt) / 1000);
  // Ease into a continuous orbit; a tiny reversing wobble cannot reveal volume.
  return AMBIENT_SPEED * (t - 2 * (1 - Math.exp(-t / 2)));
}

function bakeAmbient(inner: Internal, nowMs: number): void {
  if (inner.ambientStartedAt == null) return;
  inner.camera.yaw += ambientOffset(inner, nowMs);
  inner.ambientStartedAt = null;
}

function canDrift(inner: Internal): boolean {
  return inner.ownership !== 'system'
    && !inner.hovered
    && !inner.pointerActive
    && !inner.presentation.reducedMotion
    && !inner.presentation.selectedId;
}

function advanceAmbient(inner: Internal, nowMs: number): void {
  if (!canDrift(inner)) {
    bakeAmbient(inner, nowMs);
    return;
  }
  if (inner.ownership === 'user') {
    if (nowMs - inner.lastActivityMs < AMBIENT_DELAY_MS) {
      bakeAmbient(inner, nowMs);
      return;
    }
    inner.ownership = 'rest';
  }
  if (nowMs - inner.lastActivityMs < AMBIENT_DELAY_MS) {
    bakeAmbient(inner, nowMs);
    return;
  }
  if (inner.ambientStartedAt == null) inner.ambientStartedAt = inner.lastActivityMs + AMBIENT_DELAY_MS;
}

function rebaseClocks(inner: Internal, nowMs: number): void {
  const prev = inner.lastTickMs;
  if (prev !== null) {
    const gap = nowMs - prev;
    if (inner.system?.startMs !== null && inner.system) inner.system.startMs += gap;
    if (inner.ambientStartedAt !== null) inner.ambientStartedAt += gap;
    inner.lastActivityMs += gap;
  } else {
    inner.lastActivityMs = nowMs;
  }
  inner.lastTickMs = nowMs;
}

export function createOverviewMotion(seed: MotionSeed): MotionState {
  return wrap({
    generation: 1,
    ownership: 'rest',
    camera: copyCam(REST_CAMERA),
    system: null,
    lastTickMs: null,
    presentation: seed,
    flights: seedFlights(seed),
    snapshots: new Map(),
    hovered: false,
    pointerActive: false,
    lastActivityMs: 0,
    ambientStartedAt: null,
  });
}

export function step(state: MotionState, event: MotionEvent): { state: MotionState; frame: Readonly<OverviewFrame> } {
  const inner = cloneInternal(unwrap(state));
  if (event.type === 'tick') {
    if (event.paused) {
      rebaseClocks(inner, event.nowMs);
    } else {
      const prev = inner.lastTickMs;
      const dt = prev === null ? 0 : Math.max(0, Math.min(DT_MAX_MS, event.nowMs - prev));
      inner.lastTickMs = event.nowMs;
      advanceFlights(inner, dt);
      advanceSystem(inner, event.nowMs);
      advanceAmbient(inner, event.nowMs);
    }
  } else if (event.type === 'userCamera') {
    const nowMs = event.nowMs ?? inner.lastTickMs ?? 0;
    bakeAmbient(inner, nowMs);
    inner.ownership = 'user';
    inner.system = null;
    inner.hovered = false;
    inner.lastActivityMs = nowMs;
    inner.ambientStartedAt = null;
    inner.camera.yaw += event.dyaw ?? 0;
    inner.camera.pitch = clampPitch(inner.camera.pitch + (event.dpitch ?? 0));
    if (event.zoomMul) {
      const focus = inner.presentation.restLayout.get(inner.presentation.selectedId ?? '');
      // Small records need a closer manual view than automatic framing allows.
      const minimum = focus?.kind === 'fact' || focus?.kind === 'experience' ? 0.12 : ZOOM_MIN;
      inner.camera.distance = clampZoom(inner.camera.distance * event.zoomMul, minimum);
    }
  } else if (event.type === 'activityChanged') {
    bakeAmbient(inner, inner.lastTickMs ?? event.nowMs);
    inner.hovered = event.hovered;
    if (event.pointerActive !== undefined) inner.pointerActive = event.pointerActive;
    inner.lastActivityMs = event.nowMs;
    inner.ambientStartedAt = null;
    inner.lastTickMs = event.nowMs;
  } else if (event.type === 'scopeChanged') {
    inner.generation += 1;
    inner.ownership = 'rest';
    inner.camera = copyCam(REST_CAMERA);
    inner.system = null;
    inner.lastTickMs = null;
    inner.presentation = event.seed;
    inner.flights = seedFlights(event.seed);
    inner.snapshots = new Map();
    inner.hovered = false;
    inner.pointerActive = false;
    inner.lastActivityMs = 0;
    inner.ambientStartedAt = null;
  } else if (event.type === 'presentationChanged') {
    if (event.presentation.scopeKey !== inner.presentation.scopeKey) {
      return { state: wrap(inner), frame: emit(inner) };
    }
    const expandedChanged = inner.presentation.expandedTopicIds.join('|')
      !== event.presentation.expandedTopicIds.join('|');
    const needsFlights = expandedChanged
      || event.cause === 'disclosure'
      || event.cause === 'selection'
      || event.cause === 'reselect'
      || event.cause === 'post-reconcile-replacement';
    if (needsFlights) {
      captureSnapshots(inner, event.presentation);
      syncFlights(inner, event.presentation);
    }
    inner.presentation = event.presentation;
    if (event.cause === 'viewport') {
      if (inner.ownership === 'system' && inner.system) {
        inner.system.to = copyOrbit(framingTarget(inner, event.presentation));
      }
    } else if (event.cause === 'disclosure') {
      /* flights already synced; user ownership keeps the camera */
    } else if (
      event.cause === 'selection'
      || event.cause === 'reselect'
      || event.cause === 'post-reconcile-replacement'
      || event.cause === 'render-surface'
      || event.cause === 'auxiliary-surface'
    ) {
      startSystem(inner, event.presentation, event.cause === 'reselect' && !event.presentation.selectedId);
    }
    if (event.presentation.reducedMotion) completeReduced(inner);
  }
  return { state: wrap(inner), frame: emit(inner) };
}

export function screenHitRadiusPx(
  node: { position: ReadonlyPoint3; hitRadius: number },
  camera: OverviewFrame['camera'],
  viewport: Rect,
): number {
  return Math.max(
    MIN_HIT_PX,
    apparentPx(camera, node.position, node.hitRadius * HALO_SCALE, viewport),
  );
}

export function screenVisualRadiusPx(
  node: { position: ReadonlyPoint3; hitRadius: number },
  camera: OverviewFrame['camera'],
  viewport: Rect,
): number {
  return apparentPx(camera, node.position, node.hitRadius, viewport);
}

export function pickLiveNode(frame: OverviewFrame, screenX: number, screenY: number): string | null {
  const cam = frame.camera;
  const viewport = frame.viewport ?? { x: 0, y: 0, width: 1280, height: 800 };
  type Sample = {
    id: string;
    depth: number;
    hit: number;
    visual: number;
    dx: number;
    dy: number;
  };
  const samples: Sample[] = [];
  for (const [id, node] of frame.nodes) {
    const projected = frame.projected.get(id);
    if (!projected) continue;
    const dx = screenX - projected.x;
    const dy = screenY - projected.y;
    const depth = projectToScreen(
      { x: node.position.x, y: node.position.y, z: node.position.z },
      viewport.width,
      viewport.height,
      cam.yaw,
      cam.pitch,
      cam.distance,
      { x: cam.lookAt.x, y: cam.lookAt.y, z: cam.lookAt.z },
    ).depth;
    if (depth <= 0.02) continue;
    samples.push({
      id,
      depth,
      hit: screenHitRadiusPx(node, cam, viewport),
      visual: screenVisualRadiusPx(node, cam, viewport),
      dx,
      dy,
    });
  }
  const visualHits = samples.filter((sample) => (
    sample.dx * sample.dx + sample.dy * sample.dy <= sample.visual * sample.visual
  ));
  if (visualHits.length) {
    visualHits.sort((a, b) => a.depth - b.depth || a.id.localeCompare(b.id));
    return visualHits[0].id;
  }
  let best: { id: string; normalized: number; depth: number } | null = null;
  for (const sample of samples) {
    const d = sample.dx * sample.dx + sample.dy * sample.dy;
    if (d > sample.hit * sample.hit) continue;
    const occluded = samples.some((other) => (
      other.id !== sample.id
      && other.depth < sample.depth - 0.02
      && (other.dx * other.dx + other.dy * other.dy) <= other.visual * other.visual
    ));
    if (occluded) continue;
    const normalized = d / Math.max(1, sample.hit * sample.hit);
    if (
      !best
      || normalized < best.normalized - 0.02
      || (Math.abs(normalized - best.normalized) <= 0.02 && sample.depth < best.depth)
    ) {
      best = { id: sample.id, normalized, depth: sample.depth };
    }
  }
  return best?.id ?? null;
}

export function projectLivePoint(
  camera: OverviewFrame['camera'],
  point: Point3 | ReadonlyPoint3,
  viewport: Rect,
): { x: number; y: number } {
  const screen = projectToScreen(
    point,
    viewport.width,
    viewport.height,
    camera.yaw,
    camera.pitch,
    camera.distance,
    camera.lookAt,
  );
  return { x: screen.x, y: screen.y };
}
