import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

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

function vec(point: Point3): THREE.Vector3 {
  return new THREE.Vector3(point.x, point.y, point.z);
}

function makeLabelTexture(text: string, hot: boolean, palette: OrreryPalette): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = hot
      ? '600 44px "Times New Roman", "Songti SC", "Microsoft YaHei", serif'
      : '400 40px "Times New Roman", "Songti SC", "Microsoft YaHei", serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.shadowColor = hot ? palette.lockup : 'rgba(0, 0, 0, 0)';
    ctx.shadowBlur = hot ? 12 : 0;
    ctx.fillStyle = hot ? palette.lockup : palette.lockupSoft;
    ctx.fillText(text, 16, 64);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function lineFromPoints(
  points: Point3[],
  color: number,
  opacity: number,
  dashed: boolean,
): THREE.Line {
  const geometry = new THREE.BufferGeometry().setFromPoints(points.map(vec));
  const material = dashed
    ? new THREE.LineDashedMaterial({
        color,
        transparent: true,
        opacity,
        depthWrite: false,
        dashSize: 0.07,
        gapSize: 0.055,
      })
    : new THREE.LineBasicMaterial({
        color,
        transparent: true,
        opacity,
        depthWrite: false,
      });
  const line = new THREE.Line(geometry, material);
  if (dashed) line.computeLineDistances();
  return line;
}

export class OrreryRuntime {
  supported: boolean;
  private renderer: THREE.WebGLRenderer | null = null;
  private composer: EffectComposer | null = null;
  private bloom: UnrealBloomPass | null = null;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(OVERVIEW_FOV_DEG, 1, 0.08, 40);
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly nodeGroup = new THREE.Group();
  private readonly pathGroup = new THREE.Group();
  private readonly soulGroup = new THREE.Group();
  private readonly memoryBodyGroup = new THREE.Group();
  private readonly ambient = new THREE.AmbientLight(0x1a2430, 0.7);
  private readonly key = new THREE.PointLight(0xb8d4e8, 6, 18, 1.6);
  private readonly rim = new THREE.DirectionalLight(0xd8e6f0, 0.42);
  private readonly nodes = new Map<string, THREE.Object3D>();
  private readonly labels = new Map<string, THREE.Sprite>();
  private readonly pathLines: Array<{ line: THREE.Line; path: GraphPath }> = [];
  private readonly edgeLines = new Map<string, THREE.Line>();
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
  private readonly labelLooks = new Map<string, { text: string; hot: boolean }>();
  private soulHit: THREE.Mesh | null = null;
  private soulCore: THREE.Mesh | null = null;
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
      renderer.toneMappingExposure = 1.04;
      renderer.setClearColor(this.palette.void, 1);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer = renderer;

      this.scene.background = new THREE.Color(this.palette.void);
      this.scene.fog = new THREE.FogExp2(this.palette.fog, 0.04);
      this.key.position.set(-2.8, 2.4, -3.6);
      this.rim.position.set(3.6, 5.0, 2.8);
      this.scene.add(this.ambient, this.key, this.rim);
      this.buildMemoryBody(this.palette);
      this.scene.add(this.memoryBodyGroup);
      this.buildSoul(this.palette);
      this.scene.add(this.soulGroup);
      this.scene.add(this.pathGroup);
      this.scene.add(this.nodeGroup);

      const composer = new EffectComposer(renderer);
      composer.addPass(new RenderPass(this.scene, this.camera));
      this.bloom = new UnrealBloomPass(new THREE.Vector2(1024, 576), this.palette.bloom, 0.42, 0.22);
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
    if (this.bloom) this.bloom.strength = level === 'full' ? this.palette.bloom : 0;
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
      const label = this.labels.get(id);
      if (label) label.position.set(live.position.x, live.position.y + 0.14, live.position.z);
    }
    this.syncCameraFromFrame(frame);
    this.flushEmphasis();
  }

  render(cam: OrreryCamera): void {
    if (!this.renderer || this.disposed) return;
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
    if (this.soulCore && !this.reducedMotion && !this.ambientPaused) {
      const material = this.soulCore.material as THREE.MeshStandardMaterial;
      material.emissiveIntensity = 0.52 + Math.sin(t * 0.31) * 0.025;
    }
    if (!this.reducedMotion && !this.ambientPaused) {
      this.memoryBodyGroup.rotation.x = 0.04 + Math.sin(t * 0.31) * 0.006;
      this.memoryBodyGroup.rotation.y = -0.06 + Math.sin(t * 0.35) * 0.008;
      if (this.memoryField) {
        const material = this.memoryField.material as THREE.PointsMaterial;
        material.opacity = 0.105 + Math.sin(t * 0.35) * 0.008;
      }
      this.pathLines.forEach(({ line }, index) => {
        const material = line.material as THREE.LineBasicMaterial;
        if (!material || Array.isArray(material)) return;
        const base = typeof material.userData.baseOpacity === 'number' ? material.userData.baseOpacity : material.opacity;
        material.userData.baseOpacity = base;
        const pulse = 1 + Math.sin(t * 0.17 + index * 0.9) * 0.018;
        material.opacity = Math.max(0.04, Math.min(0.9, base * pulse));
      });
    }
    this.soulGroup.scale.setScalar(this.expanded ? 1 : 1.18);
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
    this.renderer?.dispose();
  }

  private applyPalette(): void {
    if (!this.renderer) return;
    this.renderer.setClearColor(this.palette.void, 1);
    this.scene.background = new THREE.Color(this.palette.void);
    this.scene.fog = new THREE.FogExp2(this.palette.fog, 0.04);
    this.ambient.color.setHex(this.palette.ambient);
    this.key.color.setHex(this.palette.key);
    this.rim.color.setHex(this.palette.rim);
    if (this.bloom) this.bloom.strength = this.palette.bloom;
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
    this.soulGroup.traverse((child) => {
      const mesh = child as THREE.Mesh;
      const material = mesh.material as THREE.MeshPhysicalMaterial | THREE.MeshStandardMaterial | THREE.MeshBasicMaterial | undefined;
      if (!material) return;
      if (mesh.name === 'soul-glass') {
        material.color.setHex(this.palette.soulGlass);
        (material as THREE.MeshPhysicalMaterial).emissive?.setHex(this.palette.soulEmissive);
      } else if ('emissive' in material && mesh === this.soulCore) {
        material.color.setHex(this.palette.soulGlass);
        material.emissive.setHex(this.palette.soulEmissive);
      } else if (mesh !== this.soulHit && 'color' in material) {
        material.color.setHex(this.palette.soulLattice);
      }
    });
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
      this.labelLooks.delete(id);
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
      const mesh = this.buildNode({ ...display, position: { ...live.position }, radius: live.hitRadius });
      this.nodeGroup.add(mesh);
      this.nodes.set(id, mesh);
      if (live.kind === 'topic') {
        const label = this.buildLabel(display.label, false);
        label.position.set(live.position.x, live.position.y + 0.14, live.position.z);
        this.nodeGroup.add(label);
        this.labels.set(id, label);
        this.labelLooks.set(id, { text: display.label, hot: false });
        this.allocationStats.labelTextureBuilds += 1;
      }
      this.allocationStats.nodeBuilds += 1;
      this.emphasisDirty = true;
    }
  }

  private edgeKey(from: string, to: string, kind: string): string {
    return `${kind}:${from}:${to}`;
  }

  private updateLinePoints(line: THREE.Line, points: readonly { x: number; y: number; z: number }[]): void {
    const positions = line.geometry.getAttribute('position');
    if (!positions || positions.count !== points.length) {
      line.geometry.dispose();
      line.geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(point.x, point.y, point.z)));
      return;
    }
    for (let i = 0; i < points.length; i += 1) {
      positions.setXYZ(i, points[i].x, points[i].y, points[i].z);
    }
    positions.needsUpdate = true;
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
      } else {
        this.updateLinePoints(line, edge.points);
      }
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
    this.labelLooks.clear();
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
      if (node.kind === 'topic') {
        const label = this.buildLabel(node.label, false);
        label.position.set(node.position.x, node.position.y + node.radius + 0.14, node.position.z);
        this.nodeGroup.add(label);
        this.labels.set(node.id, label);
      }
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
    for (const [id, object] of this.nodes) {
      const hot = id === this.selectedId;
      const weight = nodeWeight(id, input);
      object.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (mesh.userData.wire) {
          mesh.visible = hot;
          return;
        }
        const material = mesh.material as THREE.MeshStandardMaterial | undefined;
        if (material && 'emissiveIntensity' in material) {
          material.emissiveIntensity = hot ? 1.6 : 0.35 + weight * 0.7;
          material.opacity = 0.22 + weight * 0.74;
        }
      });
      const label = this.labels.get(id);
      if (label) {
        const material = label.material as THREE.SpriteMaterial;
        const node = this.display.find((item) => item.id === id);
        const look = { text: node?.label ?? '', hot: hot || weight > 0.8 };
        const prev = this.labelLooks.get(id);
        if (!prev || prev.text !== look.text || prev.hot !== look.hot) {
          material.map?.dispose();
          material.map = makeLabelTexture(look.text, look.hot, this.palette);
          this.labelLooks.set(id, look);
          this.allocationStats.labelTextureBuilds += 1;
        }
        material.opacity = 0.16 + weight * 0.8;
        material.needsUpdate = true;
      }
    }
    for (const { line, path } of this.pathLines) {
      const material = line.material as THREE.LineBasicMaterial;
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
      if (!this.selectedId && !this.hoverId) {
        material.opacity =
          path.kind === 'has_topic' ? 0.32 : path.kind === 'hub_to_evidence' ? 0.12 : 0.2;
      } else if (!this.selectedId && this.hoverId) {
        material.opacity = involved
          ? 0.42
          : path.kind === 'has_topic' ? 0.28 : path.kind === 'hub_to_evidence' ? 0.12 : 0.18;
      } else if (soulFocus) {
        material.opacity =
          path.kind === 'hub_to_anchor' ? 0.16 : path.kind === 'has_topic' ? 0.14 : 0.08;
      } else {
        material.opacity = involved ? 0.78 : path.kind === 'hub_to_anchor' ? 0.12 : 0.1;
      }
    }
  }

  private buildSoul(palette: OrreryPalette): void {
    this.soulGroup.clear();
    this.soulGroup.name = 'soul';
    const radius = this.soulRadius;
    const glass = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 72, 56),
      new THREE.MeshPhysicalMaterial({
        color: palette.soulGlass,
        emissive: palette.soulEmissive,
        emissiveIntensity: 0.12,
        metalness: 0.05,
        roughness: 0.24,
        transmission: 0.82,
        thickness: 0.8,
        ior: 1.22,
        transparent: true,
        opacity: 0.58,
        clearcoat: 0.85,
        clearcoatRoughness: 0.2,
      }),
    );
    glass.name = 'soul-glass';
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(radius * 0.42, 32, 24),
      new THREE.MeshStandardMaterial({
        color: palette.soulGlass,
        emissive: palette.soulEmissive,
        emissiveIntensity: 0.12,
        roughness: 0.32,
        metalness: 0.04,
      }),
    );
    core.name = 'soul-kernel';
    const hit = new THREE.Mesh(
      new THREE.SphereGeometry(radius * 1.08, 16, 12),
      new THREE.MeshBasicMaterial({ visible: false }),
    );
    hit.name = 'soul-hit';
    this.soulHit = hit;
    this.soulCore = core;
    this.soulGroup.add(glass, core, hit);
  }

  private buildNode(node: DisplayNode): THREE.Group {
    const group = new THREE.Group();
    group.position.set(node.position.x, node.position.y, node.position.z);
    group.userData.rest = node.position;
    group.userData.nodeId = node.id;
    group.userData.kind = node.kind;
    const color =
      node.kind === 'topic'
        ? this.palette.topic
        : node.kind === 'experience'
          ? this.palette.evidence
          : this.palette.signal;
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(node.radius, 32, 24),
      new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: node.kind === 'topic' ? 0.9 : 0.4,
        roughness: 0.28,
        metalness: 0.16,
        transparent: true,
        opacity: 0.92,
      }),
    );
    core.userData.nodeId = node.id;
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(node.radius * 1.45, 20, 14),
      new THREE.MeshBasicMaterial({
        color: this.palette.accent,
        transparent: true,
        opacity: node.kind === 'topic' ? 0.12 : 0.05,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    halo.userData.nodeId = node.id;
    const wire = new THREE.Mesh(
      new THREE.IcosahedronGeometry(node.radius * 1.1, 1),
      new THREE.MeshBasicMaterial({
        color: this.palette.lockup ? this.palette.soulLattice : this.palette.accent,
        wireframe: true,
        transparent: true,
        opacity: 0.62,
      }),
    );
    wire.userData.nodeId = node.id;
    wire.userData.wire = true;
    wire.visible = false;
    group.add(core, halo, wire);
    return group;
  }

  private buildLabel(text: string, hot: boolean): THREE.Sprite {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: makeLabelTexture(text, hot, this.palette),
        transparent: true,
        depthWrite: false,
      }),
    );
    sprite.scale.set(0.95, 0.24, 1);
    sprite.center.set(0, 0.5);
    return sprite;
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
        opacity: 0.095,
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
