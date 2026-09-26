import * as THREE from 'three';
import { SoulOpeningPrototype } from './SoulOpeningPrototype';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

import {
  applyCognitionOptics,
  buildCognitionBody,
  cognitionKind,
  cognitionOptics,
  tickCognitionBody,
  topicChildCount,
} from './cognitionMaterials';
import { createFilament, relationCurve, updateFilamentPaths, type Filament } from './filaments';
import { nodeWeight, type EmphasisInput } from './emphasis';
import { visibleOverviewPaths, type GraphPath } from './graphPaths';
import { OVERVIEW_FOV_DEG } from './layout';
import { pickLiveNode, type OverviewFrame } from './overviewMotion';
import { worldlinePalette, type OrreryPalette } from './palette';
import type { DisplayNode, MemoryProjection, Point3, Worldline } from './types';

export type OrreryCamera = {
  yaw: number;
  pitch: number;
  distance: number;
  lookAt: Point3;
  mode: '3d';
};

function canWebGL(canvas: HTMLCanvasElement): boolean {
  try {
    return Boolean(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

function buildMemoryEnvironment(
  renderer: THREE.WebGLRenderer,
  worldline: Worldline,
): THREE.WebGLRenderTarget {
  const sg = worldline === 'steins_gate';
  const environment = new THREE.Scene();
  environment.background = new THREE.Color(sg ? 0x01070c : 0x0a0605);

  const panels = sg
    ? [
        { color: 0x64dcf2, position: [-3.8, 2.2, -3.2], scale: [2.4, 0.32], roll: -0.28, gain: 2.7 },
        { color: 0x9a82ff, position: [3.5, -1.4, -2.1], scale: [1.25, 0.18], roll: 0.42, gain: 2.35 },
        { color: 0x786bff, position: [0.8, 3.7, 2.4], scale: [1.65, 0.12], roll: -0.55, gain: 1.65 },
        { color: 0x245f9d, position: [-1.5, -3.2, 3.1], scale: [2.1, 0.44], roll: 0.18, gain: 1.25 },
      ]
    : [
        { color: 0xf17b4e, position: [-3.7, 2.1, -3.0], scale: [2.2, 0.3], roll: -0.34, gain: 2.45 },
        { color: 0xc46a73, position: [3.6, -1.3, -2.4], scale: [1.35, 0.2], roll: 0.5, gain: 2.05 },
        { color: 0xb94e35, position: [0.7, 3.6, 2.6], scale: [1.55, 0.14], roll: -0.62, gain: 1.7 },
        { color: 0x5a294a, position: [-1.6, -3.1, 3.0], scale: [2.0, 0.46], roll: 0.22, gain: 1.15 },
      ];

  for (const panel of panels) {
    const material = new THREE.MeshBasicMaterial({
      color: new THREE.Color(panel.color).multiplyScalar(panel.gain),
      side: THREE.DoubleSide,
      toneMapped: false,
    });
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), material);
    mesh.position.set(panel.position[0], panel.position[1], panel.position[2]);
    mesh.scale.set(panel.scale[0], panel.scale[1], 1);
    mesh.lookAt(0, 0, 0);
    mesh.rotateZ(panel.roll);
    environment.add(mesh);
  }

  const pmrem = new THREE.PMREMGenerator(renderer);
  const target = pmrem.fromScene(environment, 0.04);
  environment.traverse((object) => {
    const mesh = object as THREE.Mesh;
    mesh.geometry?.dispose?.();
    const material = mesh.material;
    if (Array.isArray(material)) material.forEach((item) => item.dispose());
    else material?.dispose?.();
  });
  pmrem.dispose();
  return target;
}

function cameraWorld(cam: OrreryCamera): THREE.Vector3 {
  const look = cam.lookAt;
  const d = cam.distance;
  const cp = Math.cos(cam.pitch);
  const sp = Math.sin(cam.pitch);
  return new THREE.Vector3(
    look.x - d * cp * Math.sin(cam.yaw),
    look.y - d * sp,
    look.z - d * cp * Math.cos(cam.yaw),
  );
}

function lineFromPoints(points: Point3[], color: number, opacity: number, dashed: boolean): Filament {
  return createFilament([points], {
    color, accent: color, width: dashed ? 0.8 : 1.5, opacity, screenSpace: true,
  });
}

export class OrreryRuntime {
  readonly soulOpening = new SoulOpeningPrototype();
  supported: boolean;
  private renderer: THREE.WebGLRenderer | null = null;
  private composer: EffectComposer | null = null;
  private bloom: UnrealBloomPass | null = null;
  private environmentTarget: THREE.WebGLRenderTarget | null = null;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(OVERVIEW_FOV_DEG, 1, 0.08, 40);
  private readonly soulPickCamera = new THREE.PerspectiveCamera();
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly nodeGroup = new THREE.Group();
  private readonly pathGroup = new THREE.Group();
  private readonly soulGroup = new THREE.Group();
  private readonly memoryBodyGroup = new THREE.Group();
  private readonly ambient = new THREE.AmbientLight(0xd8e6f0, 0.16);
  private readonly hemi = new THREE.HemisphereLight(0xb0c8d4, 0x0a0e12, 0.36);
  private readonly key = new THREE.PointLight(0xb8d4e8, 18, 18, 1.8);
  private readonly phase = new THREE.PointLight(0x9b8df1, 10, 14, 2);
  private readonly rim = new THREE.DirectionalLight(0xd8e6f0, 0.58);
  private readonly viscidGroup = new THREE.Group();
  private readonly nodes = new Map<string, THREE.Object3D>();
  private readonly labels = new Map<string, THREE.Sprite>();
  private readonly pathLines: Array<{ line: Filament; path: GraphPath }> = [];
  private readonly edgeLines = new Map<string, Filament>();
  readonly allocationStats = {
    rebuildGraph: 0,
    nodeBuilds: 0,
    nodeDisposals: 0,
    edgeCreates: 0,
    edgeDisposals: 0,
    labelTextureBuilds: 0,
  };
  private liveDriven = false;
  private emphasisDirty = true;
  private soulHit: THREE.Mesh | null = null;
  private viscidUntilMs = 0;
  private memoryField: THREE.Points | null = null;
  private display: DisplayNode[] = [];
  private edges: MemoryProjection['edges'] = [];
  private selectedId: string | null = null;
  private hoverId: string | null = null;
  private query = '';
  private resultIds: string[] = [];
  private expanded = false;
  private soulRadius = 0.5;
  private worldline: Worldline = 'steins_gate';
  private palette = worldlinePalette('steins_gate');
  private reducedMotion = false;
  private ambientPaused = false;
  private width = 1;
  private height = 1;
  private disposed = false;
  private liveFrame: OverviewFrame | null = null;
  private ambientElapsed = 0;
  private ambientClockMs: number | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.supported = canWebGL(canvas);
    if (!this.supported) return;
    try {
      const renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: false,
        powerPreference: 'high-performance',
      });
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.24;
      renderer.transmissionResolutionScale = 0.65;
      renderer.setClearColor(this.palette.void, 1);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.25));
      this.renderer = renderer;

      this.scene.background = new THREE.Color(this.palette.void);
      this.scene.fog = new THREE.FogExp2(this.palette.fog, 0.012);
      this.refreshEnvironment();
      this.key.position.set(-2.8, 2.4, -3.6);
      this.phase.position.set(3.4, -1.2, 2.6);
      this.rim.position.set(3.6, 5.0, 2.8);
      this.scene.add(this.ambient, this.hemi, this.key, this.phase, this.rim);
      this.buildMemoryBody(this.palette);
      this.scene.add(this.memoryBodyGroup);
      this.buildSoul(this.palette);
      this.scene.add(this.soulGroup);
      this.scene.add(this.pathGroup);
      this.scene.add(this.nodeGroup);
      this.scene.add(this.viscidGroup);

      const composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(this.scene, this.camera));
      this.bloom = new UnrealBloomPass(new THREE.Vector2(960, 540), 0.42, 0.34, 0.96);
      composer.addPass(this.bloom);
      composer.addPass(new OutputPass());
      this.composer = composer;
    } catch {
      this.supported = false;
      this.renderer = null;
      this.composer = null;
    }
  }

  setSize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
    if (!this.renderer || !this.composer) return;
    this.renderer.setSize(this.width, this.height, false);
    this.composer.setSize(this.width, this.height);
  }

  setWorldline(worldline: Worldline): void {
    if (this.worldline === worldline) return;
    this.soulOpening.setOpen(false);
    this.soulOpening.progress = 0;
    this.worldline = worldline;
    this.palette = worldlinePalette(worldline);
    this.applyPalette();
    if (!this.liveDriven) this.rebuildGraph();
    else this.emphasisDirty = true;
  }

  setReducedMotion(value: boolean): void {
    this.reducedMotion = value;
  }

  setAmbientPaused(
    value: boolean,
    nowMs = typeof performance !== 'undefined' ? performance.now() : 0,
  ): void {
    if (value === this.ambientPaused) return;
    this.syncAmbientClock(nowMs);
    this.ambientPaused = value;
  }

  get ambientElapsedMs(): number {
    return this.ambientElapsed;
  }

  syncAmbientClock(nowMs: number): void {
    if (this.ambientPaused || this.reducedMotion) {
      this.ambientClockMs = nowMs;
      return;
    }
    if (this.ambientClockMs !== null) {
      this.ambientElapsed += Math.max(0, nowMs - this.ambientClockMs);
    }
    this.ambientClockMs = nowMs;
  }

  setDecoration(level: 'full' | 'reduced'): void {
    if (this.bloom) this.bloom.strength = level === 'full' ? 0.42 : 0.12;
    if (this.memoryField) this.memoryField.visible = level === 'full';
  }

  setGraph(
    display: DisplayNode[],
    edges: MemoryProjection['edges'],
    resultIds: string[] = [],
  ): void {
    this.display = display;
    this.edges = edges;
    this.resultIds = resultIds;
    if (!this.liveDriven) this.rebuildGraph();
  }

  setSelected(id: string | null): void {
    if (this.selectedId === id) return;
    this.selectedId = id;
    const kind = this.display.find((node) => node.id === id)?.kind;
    if ((kind === 'fact' || kind === 'experience') && !this.reducedMotion) {
      this.viscidUntilMs = (typeof performance !== 'undefined' ? performance.now() : 0) + 520;
    } else {
      this.viscidUntilMs = 0;
    }
    this.emphasisDirty = true;
    if (!this.liveDriven) this.syncVisiblePaths();
    else this.flushEmphasis();
  }

  setHover(id: string | null): void {
    if (this.hoverId === id) return;
    this.hoverId = id;
    this.emphasisDirty = true;
    if (!this.liveDriven) this.syncVisiblePaths();
    else this.flushEmphasis();
  }

  setQuery(query: string): void {
    if (this.query === query) return;
    this.query = query;
    this.emphasisDirty = true;
    this.flushEmphasis();
  }

  visiblePaths(): GraphPath[] {
    return this.pathLines.map((item) => item.path);
  }

  setExpanded(expanded: boolean): void {
    if (this.expanded === expanded) return;
    this.expanded = expanded;
    if (!this.liveDriven) this.rebuildGraph();
  }

  applyFrame(frame: OverviewFrame, display?: DisplayNode[]): void {
    this.liveDriven = true;
    this.liveFrame = frame;
    if (display) this.display = display;
    this.reconcileLiveNodes(frame);
    this.reconcileLiveEdges(frame);
    for (const [id, object] of this.nodes) {
      const live = frame.nodes.get(id);
      if (!live) {
        object.visible = false;
        continue;
      }
      object.visible = true;
      object.position.set(live.position.x, live.position.y, live.position.z);
      const baseRadius = Number(object.userData.baseRadius);
      if (Number.isFinite(baseRadius) && baseRadius > 0) {
        object.scale.setScalar(live.hitRadius / baseRadius);
      }
      const label = this.labels.get(id);
      if (label) {
        const radius = live.hitRadius;
        label.position.set(live.position.x, live.position.y + radius + 0.16, live.position.z);
      }
    }
    this.syncCameraFromFrame(frame);
    this.flushEmphasis();
  }

  render(cam: OrreryCamera): void {
    if (!this.renderer || this.disposed) return;
    this.soulOpening.restore();
    if (this.liveFrame) this.syncCameraFromFrame(this.liveFrame);
    else {
      const pos = cameraWorld(cam);
      this.camera.position.copy(pos);
      this.camera.lookAt(cam.lookAt.x, cam.lookAt.y, cam.lookAt.z);
    }
    // P1R-I8: SOUL identity is carried by the core geometry itself; there is
    // no detached or camera-facing text object to reposition each frame.
    if (!this.liveFrame) this.applyBranchReveal();
    this.syncAmbientClock(typeof performance !== 'undefined' ? performance.now() : 0);
    const t = this.ambientElapsed * 0.001;
    const life = this.reducedMotion ? 0 : 1;
    const selectedKind = this.display.find(node => node.id === this.selectedId)?.kind;
    const readingRecord = selectedKind === 'fact' || selectedKind === 'experience';
    const soulOptics = cognitionOptics(this.worldline, 'soul', readingRecord && !this.display.some(node => node.id === this.hoverId && node.kind === 'continuity_hub') ? 0.12 : this.selectedId ? 0.55 : 1, 0,
      this.display.some(node => node.id === this.hoverId && node.kind === 'continuity_hub'));
    applyCognitionOptics(this.soulGroup, soulOptics, t, life);
    for (const object of this.nodes.values()) tickCognitionBody(object, t, life);
    for (const { line } of this.pathLines) {
      line.material.uniforms.uTime.value = t;
      line.material.uniforms.uLife.value = life;
      line.material.uniforms.uResolution.value.set(this.width, this.height);
    }
    this.syncViscid(typeof performance !== 'undefined' ? performance.now() : 0);
    const hubId = this.display.find((node) => node.kind === 'continuity_hub')?.id;
    const hubRadius = hubId && this.liveFrame?.nodes.get(hubId)?.hitRadius;
    const contextualScale = hubRadius ? hubRadius / this.soulRadius : 1;
    this.soulGroup.scale.setScalar(contextualScale);
    this.soulOpening.update(this.soulGroup, this.camera, cam, [this.nodeGroup, this.pathGroup, this.memoryBodyGroup, this.viscidGroup], this.reducedMotion);
    this.camera.updateMatrixWorld();
    this.soulPickCamera.copy(this.camera);
    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
  }

  pick(clientX: number, clientY: number, rect: DOMRect): string | null {
    const frame = this.liveFrame;
    if (frame && this.width >= 64 && rect.width >= 64) {
      this.syncCameraFromFrame(frame);
      const viewport = frame.viewport ?? { x: 0, y: 0, width: this.width, height: this.height };
      const x = ((clientX - rect.left) / Math.max(1, rect.width)) * Math.max(1, viewport.width);
      const y = ((clientY - rect.top) / Math.max(1, rect.height)) * Math.max(1, viewport.height);
      return pickLiveNode(frame, x, y);
    }
    if (!this.supported) return null;
    this.pointer.x = ((clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
    this.pointer.y = -((clientY - rect.top) / Math.max(1, rect.height)) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets: THREE.Object3D[] = [...this.nodes.values()];
    if (this.soulHit) targets.push(this.soulHit);
    const hits = this.raycaster.intersectObjects(targets, true);
    for (const hit of hits) {
      const id = hit.object.userData.nodeId as string | undefined;
      if (id) return id;
    }
    return null;
  }

  pickSoulCore(clientX: number, clientY: number, rect: DOMRect): boolean {
    this.pointer.set((clientX - rect.left) / rect.width * 2 - 1, -(clientY - rect.top) / rect.height * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.soulPickCamera);
    const center = this.soulGroup.getWorldPosition(new THREE.Vector3());
    return this.raycaster.ray.intersectsSphere(new THREE.Sphere(center, 0.32 * this.soulGroup.scale.x));
  }

  project(id: string): { x: number; y: number } | null {
    const frame = this.liveFrame;
    if (frame) {
      this.syncCameraFromFrame(frame);
      const projected = frame.projected.get(id);
      if (projected) {
        const viewport = frame.viewport ?? { x: 0, y: 0, width: this.width, height: this.height };
        return {
          x: projected.x * this.width / Math.max(1, viewport.width),
          y: projected.y * this.height / Math.max(1, viewport.height),
        };
      }
    }
    const live = frame?.nodes.get(id)?.position
      ?? this.nodes.get(id)?.position
      ?? this.display.find((item) => item.id === id)?.position;
    if (!live) return null;
    const vector = new THREE.Vector3(live.x, live.y, live.z).project(this.camera);
    return {
      x: (vector.x * 0.5 + 0.5) * this.width,
      y: (-vector.y * 0.5 + 0.5) * this.height,
    };
  }

  dispose(): void {
    this.disposed = true;
    this.setGraph([], []);
    this.scene.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      mesh.geometry?.dispose?.();
      const material = mesh.material;
      if (Array.isArray(material)) material.forEach((item) => item.dispose());
      else material?.dispose?.();
    });
    this.composer?.dispose();
    this.scene.environment = null;
    this.environmentTarget?.dispose();
    this.environmentTarget = null;
    this.renderer?.dispose();
  }

  private applyPalette(): void {
    if (!this.renderer) return;
    this.renderer.setClearColor(this.palette.void, 1);
    this.scene.background = new THREE.Color(this.palette.void);
    this.scene.fog = new THREE.FogExp2(this.palette.fog, 0.012);
    this.ambient.color.setHex(this.palette.ambient);
    this.hemi.color.setHex(this.worldline === 'steins_gate' ? 0xb0c8d4 : 0xd0bea8);
    this.hemi.groundColor.setHex(this.worldline === 'steins_gate' ? 0x0a0e12 : 0x100c0a);
    this.key.color.setHex(this.palette.key);
    this.phase.color.setHex(this.worldline === 'steins_gate' ? 0x9b8df1 : 0xc66859);
    this.rim.color.setHex(this.palette.rim);
    this.refreshEnvironment();
    if (this.bloom) this.bloom.strength = 0.42;
    if (this.memoryField) {
      const material = this.memoryField.material as THREE.PointsMaterial;
      material.color.setHex(this.palette.accent);
    }
    this.memoryBodyGroup.traverse((child) => {
      const points = child as THREE.Points;
      if (points.name === 'memory-body-shell') {
        const material = points.material as THREE.PointsMaterial;
        material.color.setHex(this.palette.belt);
      } else if (points.name === 'memory-body-shell-inner') {
        const material = points.material as THREE.PointsMaterial;
        material.color.setHex(this.palette.accent);
      }
    });
    applyCognitionOptics(this.soulGroup, cognitionOptics(this.worldline, 'soul', 1), 0, this.reducedMotion ? 0 : 1);
    this.emphasisDirty = true;
  }

  private refreshEnvironment(): void {
    if (!this.renderer) return;
    this.environmentTarget?.dispose();
    this.environmentTarget = buildMemoryEnvironment(this.renderer, this.worldline);
    this.scene.environment = this.environmentTarget.texture;
    this.scene.environmentIntensity = 0.9;
  }

  private disposeObject(object: THREE.Object3D): void {
    object.traverse((child) => {
      const mesh = child as THREE.Mesh;
      mesh.geometry?.dispose?.();
      const material = mesh.material;
      if (Array.isArray(material)) material.forEach((item) => item.dispose());
      else if (material) {
        const sprite = material as THREE.SpriteMaterial;
        sprite.map?.dispose?.();
        material.dispose();
      }
    });
  }

  private syncCameraFromFrame(frame: OverviewFrame): void {
    this.camera.aspect = Math.max(1, this.width) / Math.max(1, this.height);
    this.camera.updateProjectionMatrix();
    const pos = cameraWorld({
      yaw: frame.camera.yaw,
      pitch: frame.camera.pitch,
      distance: frame.camera.distance,
      lookAt: { x: frame.camera.lookAt.x, y: frame.camera.lookAt.y, z: frame.camera.lookAt.z },
      mode: '3d',
    });
    this.camera.position.copy(pos);
    this.camera.lookAt(frame.camera.lookAt.x, frame.camera.lookAt.y, frame.camera.lookAt.z);
    this.camera.updateMatrixWorld(true);
  }

  private flushEmphasis(): void {
    if (!this.emphasisDirty) return;
    this.applyEmphasis();
    this.emphasisDirty = false;
  }

  private reconcileLiveNodes(frame: OverviewFrame): void {
    const keep = new Set(frame.nodes.keys());
    for (const [id, object] of [...this.nodes]) {
      if (keep.has(id)) continue;
      this.nodeGroup.remove(object);
      this.disposeObject(object);
      this.nodes.delete(id);
      const label = this.labels.get(id);
      if (label) {
        this.nodeGroup.remove(label);
        this.disposeObject(label);
        this.labels.delete(id);
      }
      this.allocationStats.nodeDisposals += 1;
      this.emphasisDirty = true;
    }
    const byDisplay = new Map(this.display.map((node) => [node.id, node]));
    for (const [id, live] of frame.nodes) {
      if (live.kind === 'continuity_hub' || this.nodes.has(id)) continue;
      const display = byDisplay.get(id) ?? {
        id,
        kind: live.kind,
        label: id,
        radius: live.hitRadius,
        position: { x: live.position.x, y: live.position.y, z: live.position.z },
        node: { kind: live.kind, projection_id: id, label: id } as DisplayNode['node'],
      };
      const mesh = this.buildNode({ ...display, position: { ...live.position } });
      this.nodeGroup.add(mesh);
      this.nodes.set(id, mesh);
      this.allocationStats.nodeBuilds += 1;
      this.emphasisDirty = true;
    }
  }

  private edgeKey(from: string, to: string, kind: string): string {
    return `${kind}:${from}:${to}`;
  }

  private updateLinePoints(line: Filament, points: readonly Point3[], fromRadius: number, toRadius: number): void {
    const from = points[0];
    const to = points[points.length - 1];
    if (!from || !to) return;
    const stamp = [from.x, from.y, from.z, to.x, to.y, to.z, fromRadius, toRadius].join('|');
    if (line.userData.pathStamp === stamp) return;
    line.userData.pathStamp = stamp;
    updateFilamentPaths(line, [relationCurve(from, to, fromRadius, toRadius)]);
  }

  private reconcileLiveEdges(frame: OverviewFrame): void {
    const keep = new Set(frame.edges.map((edge) => this.edgeKey(edge.from, edge.to, edge.kind)));
    for (const [key, line] of [...this.edgeLines]) {
      if (keep.has(key)) continue;
      this.pathGroup.remove(line);
      this.disposeObject(line);
      this.edgeLines.delete(key);
      this.allocationStats.edgeDisposals += 1;
      this.emphasisDirty = true;
    }
    this.pathLines.length = 0;
    for (const edge of frame.edges) {
      const key = this.edgeKey(edge.from, edge.to, edge.kind);
      let line = this.edgeLines.get(key);
      if (!line) {
        const color = edge.kind === 'has_topic' ? this.palette.signal : this.palette.accent;
        line = lineFromPoints([...edge.points], color, edge.kind === 'has_topic' ? 0.34 : 0.38, false);
        line.userData.class = 'typed_relation';
        line.userData.kind = edge.kind;
        this.pathGroup.add(line);
        this.edgeLines.set(key, line);
        this.allocationStats.edgeCreates += 1;
        this.emphasisDirty = true;
      }
      this.updateLinePoints(line, edge.points,
        frame.nodes.get(edge.from)?.hitRadius ?? 0,
        frame.nodes.get(edge.to)?.hitRadius ?? 0);
      this.pathLines.push({
        line,
        path: {
          class: 'typed_relation',
          kind: edge.kind as GraphPath['kind'],
          from: edge.from,
          to: edge.to,
          points: [...edge.points],
        },
      });
    }
  }

  private rebuildGraph(): void {
    this.allocationStats.rebuildGraph += 1;
    const hub = this.display.find((node) => node.kind === 'continuity_hub');
    if (this.soulHit && hub) this.soulHit.userData.nodeId = hub.id;
    this.nodeGroup.clear();
    this.nodes.clear();
    this.labels.forEach((sprite) => {
      const material = sprite.material as THREE.SpriteMaterial;
      material.map?.dispose();
      material.dispose();
    });
    this.labels.clear();
    this.emphasisDirty = true;
    this.syncVisiblePaths();

    if (!this.expanded) {
      return;
    }

    for (const node of this.display) {
      if (node.kind === 'continuity_hub') continue;
      const mesh = this.buildNode(node);
      this.nodeGroup.add(mesh);
      this.nodes.set(node.id, mesh);
    }
    this.applyEmphasis();
  }

  private syncVisiblePaths(): void {
    for (const { line } of this.pathLines) {
      line.geometry.dispose();
      const material = line.material;
      if (Array.isArray(material)) material.forEach((item) => item.dispose());
      else material.dispose();
    }
    this.pathGroup.clear();
    this.pathLines.length = 0;
    const paths = visibleOverviewPaths({
      display: this.display,
      edges: this.edges,
      query: this.query,
      hoverId: this.hoverId,
      selectedId: this.selectedId,
      expanded: this.expanded,
    });
    for (const path of paths) {
      const dashed = path.class === 'field_belt';
      const color =
        path.class === 'soul_instrument'
          ? this.palette.soulLattice
          : path.class === 'field_belt'
            ? this.palette.belt
            : path.kind === 'has_topic'
              ? this.palette.signal
              : this.palette.accent;
      const opacity =
        path.class === 'soul_instrument'
          ? 0.22
          : path.class === 'field_belt'
            ? 0.28
            : path.kind === 'has_topic'
              ? 0.34
              : path.kind === 'hub_to_evidence'
                ? 0.12
                : 0.38;
      const line = lineFromPoints(path.points, color, opacity, dashed);
      line.userData.class = path.class;
      line.userData.kind = path.kind;
      this.pathGroup.add(line);
      this.pathLines.push({ line, path });
    }
    this.applyEmphasis();
  }

  private applyBranchReveal(): void {
    /* Live geometry is owned by OverviewFrame; rest-polyline prefix is retired. */
  }

  private emphasisInput(): EmphasisInput {
    return {
      query: this.query,
      resultIds: this.resultIds,
      hoverId: this.hoverId,
      selectedId: this.selectedId,
      edges: this.edges,
      nodes: this.display,
    };
  }

  private applyEmphasis(): void {
    const input = this.emphasisInput();
    const selected = this.display.find((node) => node.id === this.selectedId);
    const t = this.ambientElapsed * 0.001;
    const life = this.reducedMotion ? 0 : 1;
    const parentOfRecord = selected && (selected.kind === 'fact' || selected.kind === 'experience')
      ? this.edges.find((edge) => (
        edge.kind === 'has_topic'
        && (edge.from === selected.id || edge.to === selected.id)
      ))
      : undefined;
    const parentTopicId = parentOfRecord
      ? (parentOfRecord.from === selected?.id ? parentOfRecord.to : parentOfRecord.from)
      : null;
    for (const [id, object] of this.nodes) {
      const node = this.display.find((item) => item.id === id);
      if (!node) continue;
      let weight = nodeWeight(id, input);
      if (this.hoverId && !this.selectedId) {
        weight = id === this.hoverId ? 1 : 0.22;
      } else if (selected && (selected.kind === 'fact' || selected.kind === 'experience')) {
        if (id === selected.id) weight = 1;
        else if (id === this.hoverId) weight = Math.max(weight, 0.9);
        else if (node.kind === 'topic' && id === parentTopicId) weight = Math.min(weight, 0.16);
        else if (node.kind === 'topic') weight = Math.min(weight, 0.2);
        else if (id !== selected.id) weight = Math.min(weight, 0.18);
      }
      const kind = cognitionKind(node.kind);
      const optics = cognitionOptics(this.worldline, kind, weight, topicChildCount(node), id === this.hoverId);
      applyCognitionOptics(object, optics, t, life);
    }
    for (const { line, path } of this.pathLines) {
      const material = line.material;
      if (path.class === 'soul_instrument') {
        material.opacity = 0.2;
        continue;
      }
      if (path.class === 'field_belt') {
        material.opacity = this.selectedId ? 0.12 : this.hoverId ? 0.22 : 0.26;
        continue;
      }
      const involved =
        Boolean(this.selectedId || this.hoverId) &&
        (path.from === this.selectedId ||
          path.to === this.selectedId ||
          path.from === this.hoverId ||
          path.to === this.hoverId);
      const selected = this.display.find((node) => node.id === this.selectedId);
      const soulFocus = selected?.kind === 'continuity_hub';
      const parentContext = Boolean(
        parentTopicId
        && path.kind === 'hub_to_anchor'
        && (path.from === parentTopicId || path.to === parentTopicId),
      );
      if (!this.selectedId && !this.hoverId) {
        material.opacity =
          path.kind === 'has_topic' ? 0.05 : path.kind === 'hub_to_evidence' ? 0.018 : 0.035;
      } else if (!this.selectedId && this.hoverId) {
        material.opacity = involved
          ? 0.16
          : 0.025;
      } else if (soulFocus) {
        material.opacity =
          path.kind === 'hub_to_anchor' ? 0.055 : 0.022;
      } else {
        material.opacity = selected?.kind === 'topic'
          ? involved
            ? path.kind === 'has_topic' ? 0.22 : 0.16
            : 0.018
          : involved
            ? 0.16
            : parentContext ? 0.05 : 0.012;
      }
    }
  }

  private buildSoul(palette: OrreryPalette): void {
    void palette;
    this.soulGroup.clear();
    this.soulGroup.name = 'soul';
    const body = buildCognitionBody({
      id: 'soul',
      kind: 'soul',
      radius: this.soulRadius,
      optics: cognitionOptics(this.worldline, 'soul', 1),
      names: { membrane: 'soul-glass', hit: 'soul-hit' },
    });
    while (body.children.length) this.soulGroup.add(body.children[0]);
    this.soulHit = (this.soulGroup.getObjectByName('soul-hit') as THREE.Mesh | undefined) ?? null;
    if (this.soulHit) this.soulHit.userData.nodeId = this.display.find((node) => node.kind === 'continuity_hub')?.id ?? 'soul';
  }

  private buildNode(node: DisplayNode): THREE.Group {
    const kind = cognitionKind(node.kind);
    const group = buildCognitionBody({
      id: node.id,
      kind,
      radius: node.radius,
      optics: cognitionOptics(this.worldline, kind, 0.7, topicChildCount(node)),
    });
    group.position.set(node.position.x, node.position.y, node.position.z);
    group.userData.rest = node.position;
    group.userData.nodeId = node.id;
    group.userData.kind = node.kind;
    group.userData.baseRadius = node.radius;
    return group;
  }

  private syncViscid(nowMs: number): void {
    for (const child of [...this.viscidGroup.children]) this.disposeObject(child);
    this.viscidGroup.clear();
    if (this.reducedMotion || nowMs > this.viscidUntilMs) return;
    const selected = this.display.find((node) => node.id === this.selectedId);
    if (!selected || (selected.kind !== 'fact' && selected.kind !== 'experience')) return;
    const parentEdge = this.edges.find(
      (edge) =>
        edge.kind === 'has_topic'
        && (edge.from === selected.id || edge.to === selected.id),
    );
    if (!parentEdge) return;
    const parentId = parentEdge.from === selected.id ? parentEdge.to : parentEdge.from;
    const parent = this.nodes.get(parentId);
    const child = this.nodes.get(selected.id);
    if (!parent || !child) return;
    const a = parent.position;
    const b = child.position;
    const count = 12;
    const positions = new Float32Array(count * 3);
    const remain = Math.max(0, (this.viscidUntilMs - nowMs) / 520);
    for (let i = 0; i < count; i += 1) {
      const u = i / (count - 1);
      const wobble = Math.sin(u * Math.PI) * 0.05 * remain;
      positions[i * 3] = a.x + (b.x - a.x) * u;
      positions[i * 3 + 1] = a.y + (b.y - a.y) * u + wobble;
      positions[i * 3 + 2] = a.z + (b.z - a.z) * u;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    this.viscidGroup.add(
      new THREE.Points(
        geometry,
        new THREE.PointsMaterial({
          color: this.worldline === 'steins_gate' ? 0xd4eef6 : 0xf0d4b4,
          size: 0.024,
          transparent: true,
          opacity: 0.45 * remain,
          depthWrite: false,
        }),
      ),
    );
  }

  private buildMemoryBody(palette: OrreryPalette): void {
    for (const child of [...this.memoryBodyGroup.children]) this.disposeObject(child);
    this.memoryBodyGroup.clear();
    this.memoryBodyGroup.name = 'memory-body';
    this.memoryBodyGroup.rotation.set(0.04, -0.06, 0.02);

    const shell = new THREE.Points(
      new THREE.IcosahedronGeometry(2.5, 3),
      new THREE.PointsMaterial({
        color: palette.belt,
        size: 0.007,
        transparent: true,
        opacity: 0.065,
        depthWrite: false,
      }),
    );
    shell.name = 'memory-body-shell';
    shell.position.set(0.12, -0.08, 0.02);
    shell.rotation.set(0.12, -0.18, 0.08);
    shell.scale.set(1.18, 0.76, 1.02);
    shell.renderOrder = -3;

    const innerShell = new THREE.Points(
      new THREE.IcosahedronGeometry(2.2, 2),
      new THREE.PointsMaterial({
        color: palette.accent,
        size: 0.005,
        transparent: true,
        opacity: 0.028,
        depthWrite: false,
      }),
    );
    innerShell.name = 'memory-body-shell-inner';
    innerShell.position.set(-0.08, 0.06, -0.1);
    innerShell.rotation.set(-0.14, 0.22, -0.06);
    innerShell.scale.set(1.02, 0.72, 1.08);
    innerShell.renderOrder = -2;

    const count = 420;
    const positions = new Float32Array(count * 3);
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < count; i += 1) {
      const y = 1 - ((i + 0.5) / count) * 2;
      const ring = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = i * goldenAngle;
      const hash = Math.sin((i + 1) * 12.9898) * 43758.5453;
      const radius = 0.38 + (hash - Math.floor(hash)) * 0.58;
      positions[i * 3] = Math.cos(theta) * ring * radius * 2.85 + (1 - y * y) * 0.05;
      positions[i * 3 + 1] = y * radius * 1.75 - 0.04;
      positions[i * 3 + 2] = Math.sin(theta) * ring * radius * 2.65;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const field = new THREE.Points(
      geometry,
      new THREE.PointsMaterial({
        color: palette.accent,
        size: 0.009,
        transparent: true,
        opacity: 0.038,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    field.name = 'memory-body-field';
    field.renderOrder = -1;
    this.memoryField = field;
    this.memoryBodyGroup.add(shell, innerShell, field);
  }
}
