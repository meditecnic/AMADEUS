// The light cone's WebGL shell (DIRECTION 15 and 16). `three` is only the draw-call plumbing: the geometry comes
// from coneBuild, the maths from coneFrame, the picking from coneHit and the words from coneLabels, so those
// are testable without a context. Nothing draws while nothing moves (dirty flag), and nothing here animates
// for the sake of it: motion is the entrance, the camera, a group opening and the idle turn.
import {
  BufferAttribute, BufferGeometry, Color, DoubleSide, InstancedBufferAttribute, InstancedBufferGeometry, Mesh, PerspectiveCamera,
  PlaneGeometry, Scene, ShaderMaterial, Vector3, WebGLRenderer, type IUniform,
} from 'three';

import type { ChartModel } from '../chartCoordinates';
import { annotSegments, buildLayers, chartKindOf, groupIndexOf, monthTheta, packSegs, starInstances, type ChartKind, type Packed, type StarInstances } from './coneBuild';
import { cellSegments, drawnStarIds, pickAt, type Hit } from './coneHit';
import { ConeLabelLayer, layoutConeLabels } from './coneLabels';
import {
  DIST_MAX, DIST_MIN, EL_MAX, EL_MIN, PLATE_POSE, POSE_KEYS, Spring, clamp, coneFrame, easeInOut, easeOut, homePose, lerp, nearestAngle, placeCamera, projector, rad,
  type ConeFrame, type Pose, type Screen, type Vec3, type Viewport,
} from './coneFrame';
import { CONE_THEMES, type ConeTheme, type ConeWorldline } from './coneTheme';
import {
  BEZEL_FRAG, BEZEL_VERT, LINE_FRAG, LINE_VERT, SKIN_FRAG, SKIN_RINGS, SKIN_VERT, STAR_FRAG, STAR_VERT,
} from './coneShaders';
import type { ChartSelection } from '../MemoryPlate';

export type ConeView = {
  selection: ChartSelection;
  focusTopic: string | null;
  marks: readonly string[];
  reducedMotion: boolean;
};

export type ConeHover =
  | { kind: 'star'; id: string; x: number; y: number }
  | { kind: 'cell'; key: string; group: string; x: number; y: number }
  | null;

export type ConeCallbacks = {
  onSelect: (selection: ChartSelection) => void;
  onOverview: () => void;
  onHover: (hover: ConeHover) => void;
  /** A frame was drawn and the one before it was drawn too; `ms` is the gap between the two. */
  onDrawn: (sample: { at: number; ms: number }) => void;
  onEntering: (entering: boolean) => void;
  onContextLost: () => void;
};

export type ConeStats = {
  gpu: string;
  drawCalls: number;
  triangles: number;
  pixelRatio: number;
  tier: 1 | 2;
  /** Frames actually drawn since creation. */
  drawn: number;
  /** Median and slowest of the drawn-frame gaps in the last 1.5 s / given window. */
  median: number;
  idle: boolean;
};

export type ConeHandle = {
  readonly element: HTMLElement;
  readonly mode: '3d' | 'plate';
  setModel: (model: ChartModel | null, size: { w: number; h: number; cx: number; cy: number; R: number }) => void;
  setWorldline: (worldline: ConeWorldline) => void;
  setView: (view: ConeView) => void;
  resize: (size: { w: number; h: number; cx: number; cy: number; R: number }) => void;
  setTier: (tier: 1 | 2) => void;
  entrance: () => void;
  /** Lift the camera over the cone and flatten it onto the disc; `done` once it is the plate. */
  toPlate: (done: () => void) => void;
  /** The way back; `done` once the camera is home. */
  fromPlate: (done: () => void) => void;
  /** Dev only: hold the main thread this long on every drawn frame. */
  setSlow: (ms: number) => void;
  kick: () => void;
  project: (p: Vec3) => Screen;
  stats: () => ConeStats;
  frames: () => Array<{ at: number; ms: number }>;
  dispose: () => void;
};

export type ConeCreation = { ok: true; handle: ConeHandle } | { ok: false; reason: 'nogl'; error: string };

type Size = { w: number; h: number; cx: number; cy: number; R: number };
type Tween = { t0: number; k: number; from: number; to: number; dur: number };
type Transition = { t0: number; dur: number; from: Pose; to: Pose; done: () => void };
type Drag = { x: number; y: number; x0: number; y0: number; t: number; vx: number; moved: boolean; id: number };

const IDLE_MS = 6000;
const IDLE_TURN = rad(2.4);

function lineUniforms(shared: Record<string, IUniform>, extra: Record<string, IUniform> = {}): Record<string, IUniform> {
  return {
    ...shared,
    uReveal: { value: 2 }, uFocus: { value: -1 }, uFocus2: { value: -1 }, uDim: { value: 0.18 },
    uHiCol: { value: new Color('#fff') }, uHiMix: { value: 0 }, uDash: { value: 420 }, uOpacity: { value: 1 },
    uMonthLo: { value: 0 }, uMonthHi: { value: 0 },
    ...extra,
  };
}

function quad(): InstancedBufferGeometry {
  const g = new InstancedBufferGeometry();
  g.setAttribute('position', new BufferAttribute(new Float32Array([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0]), 3));
  g.setIndex([0, 1, 2, 0, 2, 3]);
  return g;
}

function lineMesh(packed: Packed, uniforms: Record<string, IUniform>): Mesh {
  const g = quad();
  g.setAttribute('iA', new InstancedBufferAttribute(packed.iA, 3));
  g.setAttribute('iB', new InstancedBufferAttribute(packed.iB, 3));
  g.setAttribute('iX', new InstancedBufferAttribute(packed.iX, 3));
  g.setAttribute('iP', new InstancedBufferAttribute(packed.iP, 4));
  g.setAttribute('iR', new InstancedBufferAttribute(packed.iR, 2));
  g.setAttribute('iG', new InstancedBufferAttribute(packed.iG, 2));
  g.setAttribute('iC', new InstancedBufferAttribute(packed.iC, 3));
  g.instanceCount = packed.count;
  const mesh = new Mesh(g, new ShaderMaterial({
    vertexShader: LINE_VERT, fragmentShader: LINE_FRAG, transparent: true, depthTest: false, depthWrite: false, uniforms,
  }));
  mesh.frustumCulled = false;
  return mesh;
}

function disposeMesh(mesh: Mesh | undefined) {
  if (!mesh) return;
  mesh.geometry.dispose();
  (mesh.material as ShaderMaterial).dispose();
}

class ConeScene implements ConeHandle {
  readonly element: HTMLElement;
  private canvas: HTMLCanvasElement;
  private svg: SVGSVGElement;
  private labels: ConeLabelLayer;
  private renderer: WebGLRenderer;
  private scene = new Scene();
  private camera = new PerspectiveCamera(34, 1, 0.05, 200);
  private gpu: string;

  private theme: ConeTheme = CONE_THEMES.steins_gate;
  private model: ChartModel | null = null;
  private frame: ConeFrame | null = null;
  private kind: ChartKind = 'empty';
  private stars: StarInstances | null = null;
  private starIndex = new Map<string, number>();
  private meshes: Partial<Record<'struct' | 'skin' | 'rim' | 'plan' | 'world' | 'strokes' | 'stars' | 'annot' | 'paper', Mesh>> = {};

  private view: ConeView = { selection: null, focusTopic: null, marks: [], reducedMotion: false };
  private size: Size = { w: 1, h: 1, cx: 0, cy: 0, R: 300 };
  private tier: 1 | 2 = 2;
  private px = 1;

  private shared: Record<string, IUniform>;
  private reveal = {
    struct: 2, stars: 2, world: 2, rim: 2,
  };
  private sp: Record<keyof Pose, Spring> = {
    yaw: new Spring(rad(24)), el: new Spring(rad(17)), dist: new Spring(1), fov: new Spring(34),
    tx: new Spring(0), ty: new Spring(-0.4), tz: new Spring(0), flat: new Spring(0),
  };
  private away = 1;
  private expandG = -1;
  private expandT = 1;
  private expandAnim: Tween | null = null;

  private entranceAnim: { t0: number; k: number; dur: number; target: Pose } | null = null;
  private transition: Transition | null = null;
  private drag: Drag | null = null;
  private inertia = 0;
  private lastInput = performance.now();
  private annotAt = 0;
  private mode_: '3d' | 'plate' = '3d';

  private dirty = true;
  private raf = 0;
  private lastTick = performance.now();
  private lastDrew = false;
  private running = true;
  private slowMs = 0;
  private drawn = 0;
  private samples: Array<{ at: number; ms: number }> = [];
  private idle = false;
  private calls = 0;
  private tris = 0;

  private hoverStar = -1;
  private hoverKey: string | null = null;
  private segCache: ReturnType<typeof cellSegments> | null = null;
  private drawnCache: string[] | null = null;
  private readonly onLost: (event: Event) => void;
  private readonly cleanup: Array<() => void> = [];

  constructor(host: HTMLElement, private cb: ConeCallbacks, renderer: WebGLRenderer, gpu: string, canvas: HTMLCanvasElement, svg: SVGSVGElement) {
    this.element = host;
    this.renderer = renderer;
    this.gpu = gpu;
    this.canvas = canvas;
    this.svg = svg;
    this.renderer.autoClear = true;
    this.renderer.info.autoReset = false;
    this.shared = {
      uRes: { value: [1, 1] }, uPx: { value: 1 }, uFlat: { value: 0 },
      uFogNear: { value: 3 }, uFogFar: { value: 6 }, uFogMin: { value: 0.45 },
      uLight: { value: new Vector3(-0.55, 0.75, 0.45) }, uCam: { value: new Vector3() },
      uHc: { value: 1 }, uSlope: { value: 1 },
      uExpand: { value: -1 }, uExpandT: { value: 1 }, uCollapsed: { value: 0 },
    };
    this.labels = new ConeLabelLayer(svg, {
      onTopic: (id) => this.cb.onSelect({ kind: 'topic', id }),
      onMonth: (key) => this.cb.onSelect({ kind: 'month', key }),
      onSoul: () => this.cb.onSelect({ kind: 'soul' }),
    });
    this.onLost = (event) => { event.preventDefault(); this.cb.onContextLost(); };
    canvas.addEventListener('webglcontextlost', this.onLost);
    this.bindPointer();
    this.setWorldline('steins_gate');
    this.raf = requestAnimationFrame(this.tick);
  }

  get mode() { return this.mode_; }

  // ---- building -----------------------------------------------------------------------------------------

  private clearMeshes() {
    for (const mesh of Object.values(this.meshes)) { if (mesh) { this.scene.remove(mesh); disposeMesh(mesh); } }
    this.meshes = {};
  }

  private build() {
    this.clearMeshes();
    const model = this.model;
    this.stars = null;
    this.starIndex.clear();
    this.segCache = null;
    this.drawnCache = null;
    this.labels.build(null, 'none');
    if (!model) { this.frame = null; this.dirty = true; return; }
    const frame = coneFrame(model);
    this.frame = frame;
    this.kind = chartKindOf(model);
    const theme = this.theme;
    const layers = buildLayers(model, frame, theme, this.kind, this.size.R);
    const collapsed = model.collapsed && this.kind === 'normal';
    this.shared.uHc.value = frame.hc;
    this.shared.uSlope.value = frame.rToday / frame.hc;
    this.shared.uCollapsed.value = collapsed ? 1 : 0;
    if (!collapsed || this.expandG >= model.groups.length) {
      this.shared.uExpand.value = -1; this.shared.uExpandT.value = 1; this.expandG = -1; this.expandT = 1; this.expandAnim = null;
    }
    const hasChart = model.origin !== null && this.kind !== 'empty';

    // Bezel: the matte band round the disc. It draws over the cone seen from above, under it seen from below.
    const bezelGeo = new PlaneGeometry(frame.rim * 2.2, frame.rim * 2.2, 1, 1);
    bezelGeo.rotateX(-Math.PI / 2);
    this.meshes.paper = new Mesh(bezelGeo, new ShaderMaterial({
      vertexShader: BEZEL_VERT, fragmentShader: BEZEL_FRAG, transparent: true, depthTest: false, depthWrite: false, side: DoubleSide, premultipliedAlpha: true,
      uniforms: {
        uFlat: this.shared.uFlat, uPaper: { value: new Color(theme.paper) }, uInk: { value: new Color(theme.ink) },
        uHole: { value: frame.rToday }, uDisk: { value: frame.rim },
      },
    }));
    this.meshes.paper.frustumCulled = false;

    if (hasChart) this.meshes.skin = this.skin(model, frame, theme);

    const struct = lineMesh(packSegs(layers.struct), lineUniforms(this.shared, { uDash: { value: 520 } }));
    const rim = lineMesh(packSegs(layers.rim), lineUniforms(this.shared));
    const plan = lineMesh(packSegs(layers.plan), lineUniforms(this.shared));
    const world = lineMesh(packSegs(layers.world), lineUniforms(this.shared, { uHiCol: { value: new Color(theme.acc) } }));
    this.meshes.struct = struct; this.meshes.rim = rim; this.meshes.plan = plan; this.meshes.world = world;
    if (collapsed) this.meshes.strokes = lineMesh(packSegs(layers.strokes), lineUniforms(this.shared));

    if (hasChart) {
      this.stars = starInstances(model, frame);
      model.stars.forEach((star, i) => this.starIndex.set(star.id, i));
      this.meshes.stars = this.starMesh(this.stars, theme);
    }
    this.meshes.annot = lineMesh(packSegs([]), lineUniforms(this.shared));

    const order: Array<keyof typeof this.meshes> = ['struct', 'world', 'stars', 'plan', 'rim', 'annot'];
    order.forEach((name, i) => { const mesh = this.meshes[name]; if (mesh) mesh.renderOrder = 10 + i; });
    if (this.meshes.strokes) this.meshes.strokes.renderOrder = 10.5;
    if (this.meshes.skin) this.meshes.skin.renderOrder = 5;
    if (this.meshes.annot) this.meshes.annot.renderOrder = 16;
    for (const mesh of Object.values(this.meshes)) if (mesh) this.scene.add(mesh);

    this.labels.build(model, !hasChart ? 'none' : this.kind === 'normal' ? 'full' : 'basic');
    this.applyReveal();
    this.syncView(true);
  }

  private skin(model: ChartModel, frame: ConeFrame, theme: ConeTheme): Mesh {
    const rows = 48;
    const cols = 120;
    const slope = frame.rToday / frame.hc;
    const he = Math.hypot(1, slope);
    const pos: number[] = [];
    const nrm: number[] = [];
    const idx: number[] = [];
    for (let i = 0; i <= rows; i += 1) {
      const theta = (i / rows) * frame.thToday;
      for (let j = 0; j <= cols; j += 1) {
        const phi = (j / cols) * 360;
        const p = frame.pos(theta, phi);
        pos.push(p[0], p[1], p[2]);
        nrm.push(Math.sin(rad(phi)) / he, -slope / he, Math.cos(rad(phi)) / he);
      }
    }
    for (let i = 0; i < rows; i += 1) {
      for (let j = 0; j < cols; j += 1) {
        const a = i * (cols + 1) + j;
        const b = a + 1;
        const c = a + cols + 1;
        idx.push(a, c, b, b, c, c + 1);
      }
    }
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(new Float32Array(pos), 3));
    g.setAttribute('normal', new BufferAttribute(new Float32Array(nrm), 3));
    g.setIndex(idx);
    const thetas = model.rings.map((ring) => ring.theta);
    const kept = thetas.slice(-SKIN_RINGS);
    const rings = new Array<number>(SKIN_RINGS).fill(1e9);
    kept.forEach((t, i) => { rings[i] = t; });
    const mesh = new Mesh(g, new ShaderMaterial({
      vertexShader: SKIN_VERT, fragmentShader: SKIN_FRAG, transparent: true, depthTest: false, depthWrite: false, side: DoubleSide, premultipliedAlpha: true,
      uniforms: {
        uFlat: this.shared.uFlat, uCam: this.shared.uCam, uLight: this.shared.uLight,
        uInk: { value: new Color(theme.dots ? theme.struct : theme.ink) }, uAcc: { value: new Color(theme.acc) },
        uRings: { value: rings }, uNR: { value: kept.length }, uRingBase: { value: thetas.length - kept.length },
        uThToday: { value: frame.thToday }, uHc: { value: frame.hc }, uA: { value: theme.dots ? 0.05 : 0.032 },
        uMonth: { value: [0, 0] }, uTopicPhi: { value: 0 }, uTopicOn: { value: 0 },
      },
    }));
    mesh.frustumCulled = false;
    return mesh;
  }

  private starMesh(stars: StarInstances, theme: ConeTheme): Mesh {
    const g = new InstancedBufferGeometry();
    g.setAttribute('position', new BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0]), 3));
    g.setIndex([0, 1, 2, 0, 2, 3]);
    g.setAttribute('iPos', new InstancedBufferAttribute(stars.pos, 3));
    g.setAttribute('iS', new InstancedBufferAttribute(stars.s, 4));
    g.setAttribute('iT', new InstancedBufferAttribute(stars.t, 2));
    g.setAttribute('iK', new InstancedBufferAttribute(stars.k, 1));
    g.instanceCount = stars.ids.length;
    const mesh = new Mesh(g, new ShaderMaterial({
      vertexShader: STAR_VERT, fragmentShader: STAR_FRAG, transparent: true, depthTest: false, depthWrite: false,
      uniforms: {
        ...this.shared, uFocus: { value: -1 }, uFocus2: { value: -1 }, uDim: { value: 0.2 }, uSel: { value: -1 }, uHover: { value: -1 },
        uAppear: { value: 2 }, uMonthLo: { value: 0 }, uMonthHi: { value: 0 },
        uInk: { value: new Color(theme.ink) }, uPaper: { value: new Color(theme.paper) },
      },
    }));
    mesh.frustumCulled = false;
    return mesh;
  }

  private uniformsOf(name: keyof typeof this.meshes): Record<string, IUniform> | null {
    const mesh = this.meshes[name];
    return mesh ? (mesh.material as ShaderMaterial).uniforms : null;
  }

  private applyReveal() {
    const r = this.reveal;
    for (const [name, value] of [['struct', r.struct], ['strokes', r.struct], ['world', r.world], ['rim', r.rim]] as const) {
      const u = this.uniformsOf(name);
      if (u) u.uReveal.value = value;
    }
    const s = this.uniformsOf('stars');
    if (s) s.uAppear.value = r.stars;
  }

  // ---- state from outside -------------------------------------------------------------------------------

  setWorldline(worldline: ConeWorldline) {
    this.theme = CONE_THEMES[worldline];
    this.renderer.setClearColor(new Color(this.theme.paper), 0);
    if (this.model) this.build();
    this.dirty = true;
  }

  setModel(model: ChartModel | null, size: Size) {
    this.model = model;
    this.size = size;
    this.applySize();
    this.build();
    if (this.mode_ === '3d' && !this.entranceAnim && !this.transition) this.aim(true);
    this.dirty = true;
  }

  resize(size: Size) {
    const rebuilt = Math.abs(size.R - this.size.R) > 0.5;
    this.size = size;
    this.applySize();
    if (rebuilt && this.model) this.build();
    this.dirty = true;
  }

  private applySize() {
    const { w, h } = this.size;
    this.px = this.tier >= 2 ? Math.min(2, window.devicePixelRatio || 1) : 1;
    this.renderer.setPixelRatio(this.px);
    this.renderer.setSize(w, h, false);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.svg.setAttribute('width', String(w));
    this.svg.setAttribute('height', String(h));
    this.shared.uRes.value = [w * this.px, h * this.px];
    this.shared.uPx.value = this.px;
  }

  setTier(tier: 1 | 2) {
    this.tier = tier;
    this.applySize();
    this.dirty = true;
  }

  setView(view: ConeView) {
    const before = this.view;
    this.view = view;
    this.syncView(false);
    const moved = before.selection !== view.selection || before.focusTopic !== view.focusTopic;
    if (moved) this.aim(view.reducedMotion);
  }

  /** Push selection, focus, marks and the month highlight into the uniforms and rebuild the measuring layer. */
  private syncView(force: boolean) {
    const model = this.model;
    const frame = this.frame;
    if (!model || !frame) return;
    const index = groupIndexOf(model);
    const focus = this.view.focusTopic !== null && index.has(this.view.focusTopic) ? index.get(this.view.focusTopic)! : -1;
    const sel = this.view.selection;
    const collapsed = this.shared.uCollapsed.value > 0.5;

    for (const name of ['struct', 'world', 'rim', 'strokes', 'plan'] as const) {
      const u = this.uniformsOf(name);
      if (!u) continue;
      u.uFocus.value = name === 'struct' ? -1 : focus;
      u.uHiMix.value = name === 'world' || name === 'rim' ? (focus >= 0 ? 0.85 : 0) : 0;
    }
    const stars = this.uniformsOf('stars');
    if (stars) {
      stars.uFocus.value = focus;
      stars.uSel.value = sel?.kind === 'star' ? this.starIndex.get(sel.id) ?? -1 : -1;
    }

    let span: [number, number] | null = null;
    if (sel?.kind === 'month') span = monthTheta(model, sel.key);
    else if (sel?.kind === 'cell') { const cell = model.cells.get(sel.key); if (cell) span = [cell.band.theta0, cell.band.theta1]; }
    const lo = span ? frame.y(span[0]) - 0.004 : 0;
    const hi = span ? frame.y(span[1]) + 0.004 : 0;
    for (const name of ['stars', 'strokes'] as const) {
      const u = this.uniformsOf(name);
      if (u) { u.uMonthLo.value = lo; u.uMonthHi.value = hi; }
    }
    const skin = this.uniformsOf('skin');
    if (skin) {
      skin.uMonth.value = sel?.kind === 'month' && span ? [span[0], span[1]] : [0, 0];
      const topic = focus >= 0 ? model.groups[focus] : null;
      skin.uTopicOn.value = topic ? 1 : 0;
      if (topic) skin.uTopicPhi.value = rad(topic.phi);
    }

    if (collapsed) this.setOpenGroup(focus);
    this.applyMarks();
    this.drawnCache = null;

    const annot = this.meshes.annot;
    if (annot) {
      this.scene.remove(annot);
      disposeMesh(annot);
      const next = lineMesh(packSegs(annotSegments(model, frame, this.theme, {
        selectedId: sel?.kind === 'star' ? sel.id : null, soul: sel?.kind === 'soul', monthKey: sel?.kind === 'month' ? sel.key : null,
        marks: this.view.marks, state: this.kind,
      })), lineUniforms(this.shared));
      next.renderOrder = 16;
      (next.material as ShaderMaterial).uniforms.uReveal.value = force || this.view.reducedMotion ? 2 : 0;
      this.meshes.annot = next;
      this.scene.add(next);
      this.annotAt = performance.now();
    }
    this.dirty = true;
  }

  /** A mark must show as a star even if its record is collapsed. */
  private applyMarks() {
    const model = this.model;
    const stars = this.stars;
    const mesh = this.meshes.stars;
    if (!model || !stars || !mesh || !model.collapsed) return;
    const marked = new Set(this.view.marks);
    model.stars.forEach((star, i) => { stars.k[i] = !star.single && !marked.has(star.id) ? 1 : 0; });
    (mesh.geometry.getAttribute('iK') as InstancedBufferAttribute).needsUpdate = true;
  }

  private setOpenGroup(idx: number) {
    const slow = this.theme.k;
    const now = performance.now();
    if (idx >= 0 && idx !== this.expandG) {
      this.expandG = idx;
      this.shared.uExpand.value = idx;
      if (this.view.reducedMotion) { this.expandT = 1; this.expandAnim = null; }
      else { this.expandT = 0; this.expandAnim = { t0: now, k: slow, from: 0, to: 1, dur: 420 * slow }; }
    } else if (idx < 0 && this.expandG >= 0) {
      if (this.view.reducedMotion) { this.expandG = -1; this.expandT = 1; this.expandAnim = null; this.shared.uExpand.value = -1; }
      else this.expandAnim = { t0: now, k: slow, from: this.expandT, to: 0, dur: 260 * slow };
    } else if (idx >= 0 && idx === this.expandG && this.expandAnim && this.expandAnim.to === 0) {
      this.expandAnim = { t0: now, k: slow, from: this.expandT, to: 1, dur: 420 * slow * (1 - this.expandT) + 1 };
    }
    this.shared.uExpandT.value = this.expandT;
  }

  // ---- camera --------------------------------------------------------------------------------------------

  private pose(): Pose {
    const p = {} as Pose;
    for (const key of POSE_KEYS) p[key] = this.sp[key].v;
    return p;
  }

  private setTargets(pose: Pose, snap: boolean) {
    for (const key of POSE_KEYS) { if (snap) this.sp[key].snap(pose[key]); else this.sp[key].g = pose[key]; }
    this.dirty = true;
  }

  /** The camera that suits what is selected. */
  private aim(snap: boolean) {
    const model = this.model;
    const frame = this.frame;
    if (!model || !frame || this.mode_ !== '3d' || this.entranceAnim || this.transition) return;
    const base = homePose(frame, this.sp.yaw.g);
    const sel = this.view.selection;
    let pose = base;
    const star = sel?.kind === 'star' ? model.byId.get(sel.id) : null;
    const cell = sel?.kind === 'cell' ? model.cells.get(sel.key) : null;
    const group = this.view.focusTopic ? model.groups.find((g) => g.id === this.view.focusTopic) : null;
    if (star) {
      const p = frame.pos(star.theta, star.phi);
      pose = { ...base, yaw: nearestAngle(this.sp.yaw.g, rad(star.phi + 28)), el: rad(21), dist: base.dist * 1.06, tx: p[0] * 0.3, ty: lerp(base.ty, p[1], 0.35), tz: p[2] * 0.3 };
    } else if (cell) {
      const p = frame.pos(cell.theta, cell.phi);
      pose = { ...base, yaw: nearestAngle(this.sp.yaw.g, rad(cell.phi + 24)), el: rad(21), dist: base.dist * 1.04, tx: p[0] * 0.3, ty: lerp(base.ty, p[1], 0.35), tz: p[2] * 0.3 };
    } else if (group && group.starIds.length) {
      const mean = group.starIds.reduce((sum, id) => sum + model.byId.get(id)!.theta, 0) / group.starIds.length;
      const p = frame.pos(mean, group.phi);
      pose = { ...base, yaw: nearestAngle(this.sp.yaw.g, rad(group.phi + 12)), el: rad(22), dist: base.dist * 1.02, tx: p[0] * 0.22, ty: lerp(base.ty, p[1], 0.25), tz: p[2] * 0.22 };
    } else if (sel?.kind === 'soul') {
      pose = { ...base, el: rad(-12), ty: -frame.hc * 0.55 };
    } else if (sel?.kind === 'month') {
      const span = monthTheta(model, sel.key);
      pose = { ...base, el: rad(9), ty: lerp(base.ty, span ? frame.y(span[0]) : -frame.hc * 0.4, 0.5) };
    }
    this.setTargets(pose, snap || this.view.reducedMotion);
  }

  private viewport(): Viewport { return { w: this.size.w, h: this.size.h, cx: this.size.cx, cy: this.size.cy, R: this.size.R }; }

  private placeCamera() {
    const pose = this.pose();
    this.away = placeCamera(this.camera, pose, this.viewport());
    (this.shared.uCam.value as Vector3).copy(this.camera.position);
    this.shared.uFogNear.value = this.away - 0.9;
    this.shared.uFogFar.value = this.away + 1.4;
    this.shared.uFlat.value = pose.flat;
    return pose;
  }

  project = (p: Vec3): Screen => projector(this.camera, this.viewport(), this.sp.flat.v)(p);

  // ---- entrance and the plate switch ------------------------------------------------------------------------

  entrance() {
    const frame = this.frame;
    if (!frame) return;
    const k = this.theme.k;
    const world = this.layersReveal();
    const target = homePose(frame, rad(24));
    this.entranceAnim = { t0: performance.now(), k, dur: Math.max(2600, 1500 + world * 1000 + 100), target };
    this.transition = null;
    for (const key of POSE_KEYS) this.sp[key].vel = 0;
    this.setTargets(PLATE_POSE, true);
    this.reveal = { struct: 0, stars: 0, world: 0, rim: 0 };
    this.applyReveal();
    this.svg.style.opacity = '0';
    this.cb.onEntering(true);
  }

  /** How far the world layer's reveal runs (the "new" crosses come last). */
  private layersReveal(): number {
    const model = this.model;
    if (!model) return 1.7;
    const fresh = model.stars.filter((star) => star.isNew).length;
    if (!fresh) return 1.1;
    const step = Math.min(0.42, 2.4 / fresh);
    return 1.2 + (fresh - 1) * step + step * 0.98 + 0.05;
  }

  private stepEntrance(now: number) {
    const e = this.entranceAnim!;
    const t = (now - e.t0) / e.k;
    const span = (a: number, b: number) => clamp((t - a) / (b - a), 0, 1);
    const flat = easeInOut(span(650, 1900));
    const target = e.target;
    this.sp.flat.snap(1 - flat);
    this.sp.el.snap(lerp(PLATE_POSE.el, target.el, flat));
    this.sp.yaw.snap(lerp(0, target.yaw, easeInOut(span(650, 2100))));
    this.sp.fov.snap(lerp(PLATE_POSE.fov, target.fov, easeOut(span(700, 1900))));
    this.sp.dist.snap(lerp(1, target.dist, flat));
    this.sp.ty.snap(lerp(0, target.ty, flat));
    this.reveal = {
      struct: span(0, 900) + span(900, 1700),
      stars: span(150, 1100) * 1.05,
      world: (t - 1500) / 1000,
      rim: span(600, 1500),
    };
    this.applyReveal();
    this.svg.style.opacity = String(span(1700, 2200));
    if (t >= e.dur) {
      this.entranceAnim = null;
      this.reveal = { struct: 2, stars: 2, world: 2, rim: 2 };
      this.applyReveal();
      this.svg.style.opacity = '';
      for (const key of POSE_KEYS) { this.sp[key].g = this.sp[key].v; this.sp[key].vel = 0; }
      this.lastInput = now;
      this.cb.onEntering(false);
      this.aim(false);
    }
    this.dirty = true;
  }

  toPlate(done: () => void) {
    const frame = this.frame;
    if (!frame) { done(); return; }
    const to: Pose = { ...PLATE_POSE, yaw: Math.round(this.sp.yaw.v / (2 * Math.PI)) * 2 * Math.PI };
    if (this.view.reducedMotion) { this.setTargets(to, true); this.mode_ = 'plate'; this.dirty = true; done(); return; }
    this.transition = { t0: performance.now(), dur: 820 * this.theme.k, from: this.pose(), to, done: () => { this.mode_ = 'plate'; done(); } };
    this.dirty = true;
  }

  fromPlate(done: () => void) {
    const frame = this.frame;
    this.mode_ = '3d';
    if (!frame) { done(); return; }
    for (const key of POSE_KEYS) this.sp[key].vel = 0;
    this.inertia = 0;
    this.setTargets(PLATE_POSE, true);
    const to = homePose(frame, rad(24));
    if (this.view.reducedMotion) { this.setTargets(to, true); this.aim(true); done(); return; }
    this.transition = { t0: performance.now(), dur: 1100 * this.theme.k, from: this.pose(), to, done: () => { this.aim(false); done(); } };
    this.dirty = true;
  }

  private stepTransition(now: number) {
    const tr = this.transition!;
    const w = clamp((now - tr.t0) / tr.dur, 0, 1);
    const k = easeInOut(w);
    for (const key of POSE_KEYS) {
      let v = lerp(tr.from[key], tr.to[key], k);
      if (key === 'fov') v = lerp(tr.from.fov, tr.to.fov, tr.to.fov < tr.from.fov ? easeInOut(clamp(w * 1.15 - 0.15, 0, 1)) : easeOut(w));
      this.sp[key].snap(v);
    }
    this.dirty = true;
    if (w >= 1) { this.transition = null; tr.done(); }
  }

  // ---- pointer ---------------------------------------------------------------------------------------------

  private local(event: { clientX: number; clientY: number }) {
    const rect = this.element.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  private bindPointer() {
    const el = this.element;
    const on = <K extends keyof HTMLElementEventMap>(type: K, fn: (event: HTMLElementEventMap[K]) => void, options?: AddEventListenerOptions) => {
      el.addEventListener(type, fn as EventListener, options);
      this.cleanup.push(() => el.removeEventListener(type, fn as EventListener));
    };
    const locked = () => this.mode_ !== '3d' || Boolean(this.entranceAnim) || Boolean(this.transition);

    on('pointerdown', (event) => {
      if (locked() || event.button === 2) return;
      const p = this.local(event);
      this.drag = { x: p.x, y: p.y, x0: p.x, y0: p.y, t: performance.now(), vx: 0, moved: false, id: event.pointerId };
      el.setPointerCapture?.(event.pointerId);
      this.inertia = 0;
      this.lastInput = performance.now();
    });
    on('pointermove', (event) => {
      const p = this.local(event);
      const d = this.drag;
      if (d) {
        const dx = p.x - d.x;
        const dy = p.y - d.y;
        if (Math.hypot(p.x - d.x0, p.y - d.y0) > 4) d.moved = true;
        if (d.moved) {
          const now = performance.now();
          const turn = -dx * 0.0062;
          this.sp.yaw.snap(this.sp.yaw.v + turn);
          this.sp.el.snap(clamp(this.sp.el.v + dy * 0.0045, EL_MIN, EL_MAX));
          d.vx = lerp(d.vx, turn / Math.max(1, now - d.t), 0.5);
          d.t = now;
          el.classList.add('dragging');
          this.setHover(null, p);
        }
        d.x = p.x; d.y = p.y;
        this.lastInput = performance.now();
        this.dirty = true;
        return;
      }
      if (locked()) return;
      const hit = this.pick(p.x, p.y);
      el.style.cursor = hit ? 'pointer' : 'grab';
      this.setHover(hit, p);
    });
    const release = (event: PointerEvent) => {
      const d = this.drag;
      if (!d) return;
      this.drag = null;
      el.classList.remove('dragging');
      try { el.releasePointerCapture?.(event.pointerId); } catch { /* the pointer is already gone */ }
      if (!d.moved) {
        const p = this.local(event);
        const hit = this.pick(p.x, p.y);
        if (hit?.kind === 'star') this.cb.onSelect({ kind: 'star', id: hit.id });
        else if (hit?.kind === 'cell') this.cb.onSelect({ kind: 'cell', key: hit.key });
        else if (hit?.kind === 'soul') this.cb.onSelect({ kind: 'soul' });
        return;
      }
      if (!this.view.reducedMotion && performance.now() - d.t < 60) this.inertia = d.vx * 1000;
      this.lastInput = performance.now();
    };
    on('pointerup', release);
    on('pointercancel', release);
    on('pointerleave', () => { if (!this.drag) this.setHover(null, { x: 0, y: 0 }); });
    on('wheel', (event) => {
      if (locked()) return;
      event.preventDefault();
      this.sp.dist.g = clamp(this.sp.dist.g * Math.exp(-event.deltaY * 0.0012), DIST_MIN, DIST_MAX);
      this.lastInput = performance.now();
      this.dirty = true;
    }, { passive: false });
    on('dblclick', (event) => {
      if (locked()) return;
      if ((event.target as Element).closest('[role="button"], .c3-tmark')) return;
      this.cb.onOverview();
      if (this.frame) this.setTargets(homePose(this.frame, this.sp.yaw.g), this.view.reducedMotion);
    });
  }

  /** The stars drawn as stars, as screen points, and every ribbon cell as a segment; nothing else is tested. */
  private pick(x: number, y: number): Hit | null {
    const model = this.model;
    const frame = this.frame;
    if (!model || !frame || !this.stars) return null;
    const open = this.expandG >= 0 && this.expandT > 0.5 ? model.groups[this.expandG]?.id ?? null : null;
    if (!this.drawnCache) {
      const sel = this.view.selection;
      this.drawnCache = drawnStarIds(model, { openGroup: open, marks: this.view.marks, selectedId: sel?.kind === 'star' ? sel.id : null });
    }
    if (!this.segCache) this.segCache = cellSegments(model, frame, this.project, open);
    const stars = this.drawnCache.map((id) => {
      const s = this.project(this.stars!.world[this.starIndex.get(id)!]);
      return { id, x: s.x, y: s.y };
    });
    const apex = this.project(frame.apex);
    return pickAt(x, y, { stars, cells: this.segCache, soul: apex });
  }

  private setHover(hit: Hit | null, at: { x: number; y: number }) {
    const star = hit?.kind === 'star' ? this.starIndex.get(hit.id) ?? -1 : -1;
    const key = hit?.kind === 'star' ? `s:${hit.id}` : hit?.kind === 'cell' ? `c:${hit.key}` : null;
    if (star !== this.hoverStar) {
      this.hoverStar = star;
      const u = this.uniformsOf('stars');
      if (u) u.uHover.value = star;
      this.dirty = true;
    }
    if (key === this.hoverKey) return;
    this.hoverKey = key;
    if (hit?.kind === 'star') this.cb.onHover({ kind: 'star', id: hit.id, x: at.x, y: at.y });
    else if (hit?.kind === 'cell') this.cb.onHover({ kind: 'cell', key: hit.key, group: hit.group, x: at.x, y: at.y });
    else this.cb.onHover(null);
  }

  // ---- the loop ----------------------------------------------------------------------------------------------

  private tick = (now: number) => {
    this.raf = requestAnimationFrame(this.tick);
    const dt = Math.min(0.05, (now - this.lastTick) / 1000);
    const gap = now - this.lastTick;
    const drewBefore = this.lastDrew;
    this.lastTick = now;
    this.lastDrew = false;
    if (!this.running) return;

    if (this.entranceAnim) this.stepEntrance(now);
    else if (this.transition) this.stepTransition(now);
    else {
      const { f, z } = this.theme.spring;
      for (const key of POSE_KEYS) { const s = this.sp[key]; if (s.moving) { s.step(dt, f, z); this.dirty = true; } }
      if (this.inertia && !this.drag) {
        this.sp.yaw.snap(this.sp.yaw.v + this.inertia * dt);
        this.inertia *= Math.exp(-dt / 0.45);
        if (Math.abs(this.inertia) < 0.01) this.inertia = 0;
        this.dirty = true;
      }
      const resting = !this.drag && !this.view.selection && !this.view.focusTopic;
      if (resting && !this.view.reducedMotion && this.mode_ === '3d' && now - this.lastInput > IDLE_MS && !document.hidden) {
        this.sp.yaw.snap(this.sp.yaw.v + dt * IDLE_TURN);
        this.dirty = true;
      }
    }
    if (this.expandAnim) {
      const a = this.expandAnim;
      const w = clamp((now - a.t0) / a.dur, 0, 1);
      this.expandT = lerp(a.from, a.to, easeInOut(w));
      this.shared.uExpandT.value = this.expandT;
      if (w >= 1) {
        this.expandAnim = null;
        if (a.to === 0) { this.expandG = -1; this.expandT = 1; this.shared.uExpand.value = -1; this.shared.uExpandT.value = 1; }
        this.drawnCache = null;
      }
      this.dirty = true;
    }
    const annot = this.uniformsOf('annot');
    if (annot) {
      const w = clamp((now - this.annotAt) / (320 * this.theme.k), 0, 1);
      const value = this.view.reducedMotion || w >= 1 ? 2 : w;
      if (annot.uReveal.value !== value) { annot.uReveal.value = value; this.dirty = true; }
    }

    if (!this.dirty) { this.idle = true; return; }
    this.idle = false;
    this.dirty = false;
    if (this.slowMs > 0) { const until = performance.now() + this.slowMs; while (performance.now() < until) { /* dev only: pretend the GPU is slow */ } }
    this.draw();
    this.drawn += 1;
    this.lastDrew = true;
    if (drewBefore) {
      this.samples.push({ at: now, ms: gap });
      if (this.samples.length > 600) this.samples.shift();
      this.cb.onDrawn({ at: now, ms: gap });
    }
  };

  private draw() {
    this.placeCamera();
    const paper = this.meshes.paper;
    if (paper) paper.renderOrder = this.camera.position.y >= 0 ? 12.5 : 1;
    this.renderer.info.reset();
    this.renderer.setRenderTarget(null);
    this.renderer.render(this.scene, this.camera);
    this.calls = this.renderer.info.render.calls;
    this.tris = this.renderer.info.render.triangles;
    this.segCache = null;
    this.drawLabels();
  }

  private drawLabels() {
    const model = this.model;
    const frame = this.frame;
    if (!model || !frame || this.kind === 'empty') return;
    const layout = layoutConeLabels({ model, frame, project: this.project, focusTopic: this.view.focusTopic });
    const sel = this.view.selection;
    this.labels.update(layout, { focusTopic: this.view.focusTopic, month: sel?.kind === 'month' ? sel.key : null, soul: sel?.kind === 'soul' });
  }

  // ---- misc -----------------------------------------------------------------------------------------------------

  setSlow(ms: number) { this.slowMs = ms; }
  kick() { this.dirty = true; this.lastInput = performance.now(); }

  stats(): ConeStats {
    const now = performance.now();
    const recent = this.samples.filter((s) => now - s.at < 1500).map((s) => s.ms).sort((a, b) => a - b);
    return {
      gpu: this.gpu, drawCalls: this.calls, triangles: this.tris, pixelRatio: this.px, tier: this.tier, drawn: this.drawn,
      median: recent.length ? recent[Math.floor(recent.length / 2)] : 0, idle: this.idle,
    };
  }

  frames() { return this.samples.slice(); }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.running = false;
    this.canvas.removeEventListener('webglcontextlost', this.onLost);
    for (const off of this.cleanup) off();
    this.clearMeshes();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.element.remove();
  }
}

/** Build the canvas, the label layer and the WebGL renderer inside `host`. Fails (rather than throws) without a context. */
export function createConeRenderer(host: HTMLElement, callbacks: ConeCallbacks): ConeCreation {
  const wrap = document.createElement('div');
  wrap.className = 'c3-wrap';
  host.appendChild(wrap);
  const canvas = document.createElement('canvas');
  canvas.className = 'c3-canvas';
  wrap.appendChild(canvas);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'c3-labels');
  svg.setAttribute('aria-label', '立体星图的文字');
  wrap.appendChild(svg);
  let renderer: WebGLRenderer;
  try {
    renderer = new WebGLRenderer({ canvas, antialias: false, alpha: true, premultipliedAlpha: true, powerPreference: 'high-performance', preserveDrawingBuffer: false });
  } catch (error) {
    wrap.remove();
    return { ok: false, reason: 'nogl', error: String(error) };
  }
  const gl = renderer.getContext();
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  const gpu = String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
  return { ok: true, handle: new ConeScene(wrap, callbacks, renderer, gpu, canvas, svg) };
}
