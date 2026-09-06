import * as THREE from 'three';
import { describe, expect, it, vi } from 'vitest';

import { layoutProjection } from './layout';
import { tickCognitionBody } from './cognitionMaterials';
import { createOverviewMotion, pickLiveNode, restLayoutFromProjection, step } from './overviewMotion';
import { deriveOverviewPresentation } from './overviewPresentation';
import { OrreryRuntime } from './orreryRenderer';
import type { MemoryProjection } from './types';

vi.mock('./graphPaths', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./graphPaths')>();
  return {
    ...actual,
    filamentPrefix: vi.fn(actual.filamentPrefix),
  };
});

const { filamentPrefix } = await import('./graphPaths');

const projection: MemoryProjection = {
  scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
  view: 'overview',
  projection_version: 'memory-projection-v1',
  generated_at: '2026-08-19T00:00:00Z',
  criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
  center: { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
  composition: {
    active_facts: 1,
    active_experiences: 0,
    eligible_topics: 1,
    latest_memory_change_at: null,
    person_anchors_supported: false,
  },
  budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
  eligible: { nodes: 3, edges: 2, records: 1, results: 1 },
  shown: { nodes: 3, edges: 2, records: 1, results: 1 },
  truncated: { nodes: false, edges: false, records: false, results: false },
  empty: false,
  result_ids: ['fact:1'],
  nodes: [
    { kind: 'continuity_hub', projection_id: 'hub', label_primary: 'AMADEUS', label_secondary: 'SOUL' },
    { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 1, experience_count: 0 },
    {
      kind: 'fact',
      projection_id: 'fact:1',
      fact_id: '1',
      label: '喜欢黑咖啡',
      is_pinned: false,
      updated_at: '2026-08-19T00:00:00Z',
      topic_id: 't1',
    },
  ],
  edges: [
    { kind: 'hub_to_anchor', from: 'hub', to: 'topic:t1' },
    { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
  ],
};

function frameOf(expanded: readonly string[] = ['topic:t1'], selectedId: string | null = 'topic:t1') {
  const plan = deriveOverviewPresentation(projection, { expandedTopicIds: expanded, selectedId });
  const seed = {
    scopeKey: 's|steins_gate|okabe',
    plan,
    restLayout: restLayoutFromProjection(projection),
    selectedId,
    focusedId: selectedId,
    expandedTopicIds: expanded,
    viewport: { x: 0, y: 0, width: 1280, height: 800 },
    insets: { top: 64, right: 16, bottom: 16, left: 16 },
    renderSurface: '3d' as const,
    reducedMotion: true,
  };
  return {
    display: layoutProjection(projection),
    ...step(createOverviewMotion(seed), {
      type: 'presentationChanged',
      cause: selectedId ? 'selection' : 'disclosure',
      presentation: seed,
    }),
  };
}

function clientRect(width = 1280, height = 800): DOMRect {
  return {
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON() {
      return {};
    },
  };
}

function projectWithRenderedCamera(
  frame: ReturnType<typeof frameOf>['frame'],
  id: string,
): { x: number; y: number } {
  const viewport = frame.viewport;
  const camera = new THREE.PerspectiveCamera(42, viewport.width / viewport.height, 0.08, 40);
  const { yaw, pitch, distance, lookAt } = frame.camera;
  const cp = Math.cos(pitch);
  camera.position.set(
    lookAt.x - distance * cp * Math.sin(yaw),
    lookAt.y - distance * Math.sin(pitch),
    lookAt.z - distance * cp * Math.cos(yaw),
  );
  camera.lookAt(lookAt.x, lookAt.y, lookAt.z);
  camera.updateMatrixWorld(true);
  const node = frame.nodes.get(id);
  if (!node) throw new Error(`missing live node ${id}`);
  const projected = new THREE.Vector3(node.position.x, node.position.y, node.position.z).project(camera);
  return {
    x: (projected.x * 0.5 + 0.5) * viewport.width,
    y: (-projected.y * 0.5 + 0.5) * viewport.height,
  };
}

describe('OrreryRuntime live frame adapter', () => {
  it('P1R-I8: SOUL is a quiet nested core without text skin or decorative orbit lines', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const internal = runtime as unknown as {
      supported: boolean;
      soulGroup: THREE.Group;
      palette: unknown;
      buildSoul(palette: unknown): void;
    };
    // jsdom has no WebGL context; build the soul structure directly.
    // Real-Chromium occlusion / readability stays a Gate 2 runtime cell.
    if (!internal.supported) internal.buildSoul(internal.palette);
    const group = internal.soulGroup;
    const sprites = group.children.filter((child) => Boolean((child as THREE.Sprite).isSprite));
    expect(sprites).toHaveLength(0);
    expect(group.getObjectByName('soul-mark')).toBeUndefined();
    expect(group.getObjectByName('soul-continuity-rings')).toBeUndefined();
    expect(group.getObjectByName('soul-glass')).toBeTruthy();
    expect(group.getObjectByName('soul-kernel')).toBeUndefined();
    expect(group.getObjectByName('soul-hit')).toBeTruthy();
    runtime.dispose();
  });

  it('builds a deterministic bounded memory body instead of a distant starfield', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const internal = runtime as unknown as {
      supported: boolean;
      memoryBodyGroup: THREE.Group;
      palette: unknown;
      buildMemoryBody(palette: unknown): void;
    };
    if (!internal.supported) internal.buildMemoryBody(internal.palette);

    const shell = internal.memoryBodyGroup.getObjectByName('memory-body-shell');
    const field = internal.memoryBodyGroup.getObjectByName('memory-body-field') as THREE.Points;
    expect(shell).toBeTruthy();
    expect(field?.isPoints).toBe(true);

    const positions = field.geometry.getAttribute('position') as THREE.BufferAttribute;
    expect(positions.count).toBeGreaterThanOrEqual(120);
    expect(positions.count).toBeLessThanOrEqual(640);
    for (let index = 0; index < positions.count; index += 1) {
      const normalized = Math.hypot(
        positions.getX(index) / 3.35,
        positions.getY(index) / 2.55,
        positions.getZ(index) / 3.05,
      );
      expect(normalized).toBeLessThanOrEqual(1.01);
    }

    const firstBuild = Array.from(positions.array).slice(0, 18);
    internal.buildMemoryBody(internal.palette);
    const rebuilt = internal.memoryBodyGroup.getObjectByName('memory-body-field') as THREE.Points;
    expect(Array.from(rebuilt.geometry.getAttribute('position').array).slice(0, 18)).toEqual(firstBuild);
    runtime.dispose();
  });

  it('projects off-center nodes where the rendered Three camera draws them', () => {
    const { frame } = frameOf([], null);
    const rendered = projectWithRenderedCamera(frame, 'topic:t1');
    const interactive = frame.projected.get('topic:t1');
    expect(interactive).toBeTruthy();
    expect(interactive!.x).toBeCloseTo(rendered.x, 5);
    expect(interactive!.y).toBeCloseTo(rendered.y, 5);
  });

  it('25. applyFrame does not use rest filamentPrefix', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const { frame, display } = frameOf();
    runtime.applyFrame(frame, display);
    runtime.applyFrame(frame, display);
    expect(vi.mocked(filamentPrefix)).not.toHaveBeenCalled();
    runtime.dispose();
  });

  it('uses hover for the material response while keeping click selection independent', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const { frame, display } = frameOf();
    runtime.applyFrame(frame, display);
    runtime.setSelected('topic:t1');
    const internal = runtime as unknown as { nodes: Map<string, THREE.Group> };
    const flow = internal.nodes.get('topic:t1')!.getObjectByName('inner-flow') as THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>;
    const normalColor = flow.material.uniforms.uColor.value.getHex();
    expect(flow.userData.hovered).toBe(false);
    runtime.setHover('topic:t1');
    expect(flow.userData.hovered).toBe(true);
    tickCognitionBody(flow, 1, 1);
    tickCognitionBody(flow, 1.1, 1);
    const hoverStrength = flow.material.uniforms.uHoverStrength.value;
    expect(hoverStrength).toBeGreaterThan(0);
    expect(flow.scale.toArray()).toEqual([1, 1, 1]);
    expect(flow.material.uniforms.uColor.value.getHex()).not.toBe(normalColor);
    runtime.setHover(null);
    tickCognitionBody(flow, 1.2, 1);
    expect(flow.material.uniforms.uHoverStrength.value).toBeLessThan(hoverStrength);
    expect(flow.scale.toArray()).toEqual([1, 1, 1]);
    expect(flow.userData.hovered).toBe(false);
    expect(flow.material.uniforms.uColor.value.getHex()).toBe(normalColor);
    runtime.dispose();
  });

  it('does not rebuild graph or allocate replacement objects on unchanged topology', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const { frame, display } = frameOf();
    runtime.setSize(1280, 800);
    runtime.applyFrame(frame, display);
    const afterFirst = { ...runtime.allocationStats };
    runtime.applyFrame(frame, display);
    runtime.applyFrame(frame, display);
    expect(runtime.allocationStats.rebuildGraph).toBe(0);
    expect(runtime.allocationStats.nodeBuilds).toBe(afterFirst.nodeBuilds);
    expect(runtime.allocationStats.edgeCreates).toBe(afterFirst.edgeCreates);
    expect(runtime.allocationStats.labelTextureBuilds).toBe(afterFirst.labelTextureBuilds);
    runtime.dispose();
  });

  it('disposes only membership that left the frame', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const open = frameOf(['topic:t1']);
    runtime.applyFrame(open.frame, open.display);
    const built = runtime.allocationStats.nodeBuilds;
    const closed = frameOf([]);
    runtime.applyFrame(closed.frame, closed.display);
    expect(runtime.allocationStats.nodeDisposals).toBeGreaterThan(0);
    expect(runtime.allocationStats.nodeBuilds).toBe(built);
    runtime.dispose();
  });

  it('26. hit/pick consume frame.nodes position and hitRadius', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    runtime.setSize(1280, 800);
    const { frame, display } = frameOf();
    const movedNodes = new Map(frame.nodes);
    const topic = frame.nodes.get('topic:t1')!;
    movedNodes.set('topic:t1', {
      ...topic,
      position: { x: 2.4, y: 0.1, z: -0.6 },
    });
    const moved = { ...frame, nodes: movedNodes };
    runtime.applyFrame(moved, display);
    const at = runtime.project('topic:t1');
    expect(at).toBeTruthy();
    expect(runtime.pick(at!.x, at!.y, clientRect())).toBe('topic:t1');
    expect(runtime.pick(8, 8, clientRect())).not.toBe('topic:t1');
    const localX = at!.x;
    const localY = at!.y;
    expect(runtime.pick(at!.x, at!.y, clientRect())).toBe(
      pickLiveNode(moved, localX, localY),
    );
    runtime.dispose();
  });

  it('pick after a new user-camera frame does not use the previous liveFrame', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    runtime.setSize(1280, 800);
    const rest = frameOf([], null);
    runtime.applyFrame(rest.frame, rest.display);
    const at = runtime.project('topic:t1');
    expect(at).toBeTruthy();
    const moved = step(rest.state, { type: 'userCamera', source: 'pointer', dyaw: 0.55 });
    runtime.applyFrame(moved.frame, rest.display);
    const later = runtime.project('topic:t1');
    expect(later).toBeTruthy();
    expect(Math.hypot((later!.x - at!.x), (later!.y - at!.y))).toBeGreaterThan(2);
    expect(runtime.pick(at!.x, at!.y, clientRect())).toBe(
      pickLiveNode(moved.frame, at!.x, at!.y),
    );
    runtime.dispose();
  });

  it('27. screen anchors use the same real-camera helper as pick', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    runtime.setSize(1280, 800);
    const { frame, display } = frameOf();
    runtime.applyFrame(frame, display);
    const at = runtime.project('topic:t1');
    expect(at).toBeTruthy();
    expect(runtime.pick(at!.x, at!.y, clientRect())).toBe('topic:t1');
    runtime.dispose();
  });

  it('ambient phase freezes while paused and does not jump after a hidden wall-clock gap', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    runtime.syncAmbientClock(0);
    runtime.syncAmbientClock(1000);
    expect(runtime.ambientElapsedMs).toBe(1000);
    runtime.setAmbientPaused(true, 1000);
    expect(runtime.ambientElapsedMs).toBe(1000);
    runtime.setAmbientPaused(false, 9000);
    expect(runtime.ambientElapsedMs).toBe(1000);
    runtime.syncAmbientClock(9160);
    expect(runtime.ambientElapsedMs).toBe(1160);
    runtime.setReducedMotion(true);
    runtime.syncAmbientClock(4000);
    expect(runtime.ambientElapsedMs).toBe(1160);
    runtime.dispose();
  });

  it('live edge endpoints stay aligned with node positions', () => {
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    const { frame, display } = frameOf();
    runtime.applyFrame(frame, display);
    const child = frame.nodes.get('fact:1');
    const parent = frame.nodes.get('topic:t1');
    const edge = frame.edges.find((item) => item.kind === 'has_topic');
    expect(child).toBeTruthy();
    expect(parent).toBeTruthy();
    expect(edge?.points[0]).toEqual(child?.position);
    expect(edge?.points[edge.points.length - 1]).toEqual(parent?.position);
    runtime.dispose();
  });
});
