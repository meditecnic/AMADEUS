import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

import { nodeWeight, type EmphasisInput } from './emphasis';
import {
  branchReveal,
  evidenceParentId,
  filamentPrefix,
  soulLockupLines,
  soulMarkOffset,
  visibleOverviewPaths,
  type GraphPath,
} from './graphPaths';
import { worldlinePalette, type OrreryPalette } from './palette';
import type { DisplayNode, MemoryProjection, Point3, Worldline } from './types';

export type OrreryCamera = {
  yaw: number;
  pitch: number;
  distance: number;
  lookAt: Point3;
  mode: '3d' | '2d';
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

function makeSoulTexture(
  primary: string,
  secondary: string,
  palette: OrreryPalette,
): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 1024;
  canvas.height = 1024;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    const [mark] = soulLockupLines(primary, secondary);
    ctx.clearRect(0, 0, 1024, 1024);
    ctx.translate(512, 512);
    ctx.shadowColor = palette.lockup;
    ctx.shadowBlur = 36;
    ctx.fillStyle = palette.lockup;
    ctx.font = '600 220px "Times New Roman", "Songti SC", serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(mark, 0, 8);
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
  private readonly camera = new THREE.PerspectiveCamera(42, 1, 0.08, 40);
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly nodeGroup = new THREE.Group();
  private readonly pathGroup = new THREE.Group();
  private readonly soulGroup = new THREE.Group();
  private readonly ambient = new THREE.AmbientLight(0x1a2430, 0.7);
  private readonly key = new THREE.PointLight(0xb8d4e8, 14, 18, 1.6);
  private readonly rim = new THREE.DirectionalLight(0xd8e6f0, 0.42);
  private readonly nodes = new Map<string, THREE.Object3D>();
  private readonly labels = new Map<string, THREE.Sprite>();
  private readonly pathLines: Array<{ line: THREE.Line; path: GraphPath }> = [];
  private soulHit: THREE.Mesh | null = null;
  private soulWord: THREE.Sprite | null = null;
  private soulCore: THREE.Mesh | null = null;
  private starfield: THREE.Points | null = null;
  private display: DisplayNode[] = [];
  private edges: MemoryProjection['edges'] = [];
  private selectedId: string | null = null;
  private hoverId: string | null = null;
  private query = '';
  private resultIds: string[] = [];
  private expanded = false;
  private branchProgress = new Map<string, number>();
  private soulRadius = 0.5;
  private worldline: Worldline = 'steins_gate';
  private palette = worldlinePalette('steins_gate');
  private reducedMotion = false;
  private width = 1;
  private height = 1;
  private disposed = false;

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
      this.key.position.set(0, 0.1, 0);
      this.rim.position.set(3.6, 5.0, 2.8);
      this.scene.add(this.ambient, this.key, this.rim);
      this.starfield = this.buildStarfield();
      this.scene.add(this.starfield);
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
    if (!this.renderer || !this.composer) return;
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this.width, this.height, false);
    this.composer.setSize(this.width, this.height);
  }

  setWorldline(worldline: Worldline): void {
    if (this.worldline === worldline) return;
    this.worldline = worldline;
    this.palette = worldlinePalette(worldline);
    this.applyPalette();
    this.rebuildGraph();
  }

  setReducedMotion(value: boolean): void {
    this.reducedMotion = value;
  }

  setDecoration(level: 'full' | 'reduced'): void {
    if (this.bloom) this.bloom.strength = level === 'full' ? this.palette.bloom : 0;
    if (this.starfield) this.starfield.visible = level === 'full';
  }

  setGraph(
    display: DisplayNode[],
    edges: MemoryProjection['edges'],
    resultIds: string[] = [],
  ): void {
    this.display = display;
    this.edges = edges;
    this.resultIds = resultIds;
    this.rebuildGraph();
  }

  setSelected(id: string | null): void {
    if (this.selectedId === id) return;
    this.selectedId = id;
    this.syncVisiblePaths();
  }

  setHover(id: string | null): void {
    if (this.hoverId === id) return;
    this.hoverId = id;
    this.syncVisiblePaths();
  }

  setQuery(query: string): void {
    if (this.query === query) return;
    this.query = query;
    this.applyEmphasis();
  }

  visiblePaths(): GraphPath[] {
    return this.pathLines.map((item) => item.path);
  }

  setExpanded(expanded: boolean): void {
    if (this.expanded === expanded) return;
    this.expanded = expanded;
    this.rebuildGraph();
  }

  setBranchProgress(progress: Readonly<Record<string, number>>): void {
    this.branchProgress = new Map(Object.entries(progress));
  }

  render(cam: OrreryCamera): void {
    if (!this.renderer || this.disposed) return;
    const pos = cameraWorld(cam);
    this.camera.position.copy(pos);
    this.camera.lookAt(cam.lookAt.x, cam.lookAt.y, cam.lookAt.z);
    if (this.soulWord) {
      const mark = soulMarkOffset(
        { x: this.camera.position.x, y: this.camera.position.y, z: this.camera.position.z },
        this.soulRadius,
      );
      this.soulWord.position.set(mark.x, mark.y, mark.z);
    }
    this.applyBranchReveal();
    if (this.soulCore && !this.reducedMotion) {
      const material = this.soulCore.material as THREE.MeshStandardMaterial;
      const t = performance.now() * 0.001;
      material.emissiveIntensity = 0.26 + Math.sin(t * 0.65) * 0.05;
    }
    this.soulGroup.scale.setScalar(this.expanded ? 1 : 1.18);
    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
  }

  pick(clientX: number, clientY: number, rect: DOMRect): string | null {
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
    const node = this.display.find((item) => item.id === id);
    if (!node) return null;
    const vector = new THREE.Vector3(
      node.position.x,
      node.position.y + node.radius * 0.2,
      node.position.z,
    ).project(this.camera);
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
    if (this.starfield) {
      const material = this.starfield.material as THREE.PointsMaterial;
      material.color.setHex(this.palette.accent);
    }
    this.soulGroup.traverse((child) => {
      if (child === this.soulWord) return;
      const mesh = child as THREE.Mesh;
      const material = mesh.material as THREE.MeshPhysicalMaterial | THREE.MeshStandardMaterial | THREE.MeshBasicMaterial | undefined;
      if (!material) return;
      if ('transmission' in material) {
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

  private rebuildGraph(): void {
    const hub = this.display.find((node) => node.kind === 'continuity_hub');
    if (this.soulHit && hub) this.soulHit.userData.nodeId = hub.id;
    if (this.soulWord && hub && hub.node.kind === 'continuity_hub') {
      const material = this.soulWord.material;
      material.map?.dispose();
      material.map = makeSoulTexture(hub.node.label_primary, hub.node.label_secondary, this.palette);
      material.visible = true;
      material.needsUpdate = true;
    }
    this.nodeGroup.clear();
    this.nodes.clear();
    this.labels.forEach((sprite) => {
      const material = sprite.material as THREE.SpriteMaterial;
      material.map?.dispose();
      material.dispose();
    });
    this.labels.clear();
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
    const byId = new Map(this.display.map((node) => [node.id, node]));
    for (const [id, object] of this.nodes) {
      const node = byId.get(id);
      if (!node || (node.kind !== 'fact' && node.kind !== 'experience')) continue;
      const parent = evidenceParentId(id, this.edges);
      const topic = parent ? byId.get(parent) : undefined;
      const rest = (object.userData.rest as Point3 | undefined) ?? node.position;
      const progress = parent ? (this.branchProgress.get(parent) ?? 0) : 1;
      const next = topic ? branchReveal(progress, topic.position, rest) : rest;
      object.position.set(next.x, next.y, next.z);
      object.visible = progress > 0.03;
      object.traverse((child) => {
        const mesh = child as THREE.Mesh;
        const material = mesh.material as THREE.MeshStandardMaterial | undefined;
        if (material && 'opacity' in material) material.opacity = Math.max(0.08, progress);
      });
    }
    for (const { line, path } of this.pathLines) {
      if (path.kind !== 'has_topic' || !path.to) continue;
      const progress = this.branchProgress.get(path.to) ?? 0;
      const pts = filamentPrefix(path.points, progress);
      line.geometry.dispose();
      line.geometry = new THREE.BufferGeometry().setFromPoints(
        pts.map((point) => new THREE.Vector3(point.x, point.y, point.z)),
      );
      const material = line.material as THREE.LineBasicMaterial;
      material.opacity = 0.12 + progress * 0.4;
    }
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
        material.map?.dispose();
        const node = this.display.find((item) => item.id === id);
        material.map = makeLabelTexture(node?.label ?? '', hot || weight > 0.8, this.palette);
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
        material.opacity = this.selectedId || this.hoverId ? 0.12 : 0.26;
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
      } else if (soulFocus) {
        material.opacity =
          path.kind === 'hub_to_anchor' ? 0.16 : path.kind === 'has_topic' ? 0.14 : 0.08;
      } else {
        material.opacity = involved ? 0.78 : path.kind === 'hub_to_anchor' ? 0.06 : 0.05;
      }
    }
  }

  private buildSoul(palette: OrreryPalette): void {
    this.soulGroup.clear();
    this.soulGroup.name = 'soul';
    const radius = 0.5;
    const glass = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 72, 56),
      new THREE.MeshPhysicalMaterial({
        color: palette.soulGlass,
        emissive: palette.soulEmissive,
        emissiveIntensity: 0.28,
        metalness: 0.12,
        roughness: 0.14,
        transmission: 0.58,
        thickness: 1.1,
        ior: 1.36,
        transparent: true,
        opacity: 0.9,
        clearcoat: 0.7,
        clearcoatRoughness: 0.16,
      }),
    );
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(radius * 0.42, 32, 24),
      new THREE.MeshStandardMaterial({
        color: palette.soulGlass,
        emissive: palette.soulEmissive,
        emissiveIntensity: 0.28,
        roughness: 0.35,
        metalness: 0.2,
      }),
    );
    const lattice = new THREE.Mesh(
      new THREE.IcosahedronGeometry(radius * 0.8, 2),
      new THREE.MeshBasicMaterial({
        color: palette.soulLattice,
        wireframe: true,
        transparent: true,
        opacity: 0.26,
      }),
    );
    const word = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: makeSoulTexture('AMADEUS', 'SOUL', palette),
        transparent: true,
        depthTest: false,
        depthWrite: false,
      }),
    );
    word.scale.set(1.38, 0.48, 1);
    word.renderOrder = 4;
    const hit = new THREE.Mesh(
      new THREE.SphereGeometry(radius * 1.08, 16, 12),
      new THREE.MeshBasicMaterial({ visible: false }),
    );
    hit.name = 'soul-hit';
    this.soulWord = word;
    this.soulHit = hit;
    this.soulCore = core;
    this.soulGroup.add(glass, core, lattice, word, hit);
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

  private buildStarfield(): THREE.Points {
    const count = 40;
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const r = 10 + Math.random() * 14;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi) * 0.65;
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return new THREE.Points(
      geometry,
      new THREE.PointsMaterial({
        color: this.palette.accent,
        size: 0.018,
        transparent: true,
        opacity: 0.16,
        depthWrite: false,
      }),
    );
  }
}
