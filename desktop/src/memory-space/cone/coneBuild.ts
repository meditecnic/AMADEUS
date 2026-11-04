// Turns a ChartModel into the cone's draw data: line segments for each layer and one instance per star.
// Pure functions of the model, so jsdom can test what the renderer will draw without a GL context.
import { thetaAt, type ChartCell, type ChartModel, type ChartStar } from '../chartCoordinates';
import { RIBBON_W, bandSpan } from '../ribbonGeometry';
import { clamp, lerp, rad, type ConeFrame, type RGB, type Vec3 } from './coneFrame';
import { rgb, type ConeTheme } from './coneTheme';

/** What the chart has to show: `new` is a young scope with no topics yet, `empty` has nothing but the disc. */
export type ChartKind = 'normal' | 'new' | 'empty';

export function chartKindOf(model: Pick<ChartModel, 'stars' | 'topics'>): ChartKind {
  if (!model.stars.length) return 'empty';
  if (model.stars.length <= 6 && !model.topics.some((t) => t.starIds.length)) return 'new';
  return 'normal';
}

export type Seg = {
  a: Vec3;
  b: Vec3;
  /** The star a collapsed tick turns into. */
  x?: Vec3;
  /** Width in CSS px. */
  w?: number;
  alpha?: number;
  /** 0 solid, 1 dashed, 2 centre line, 3 dotted. */
  kind?: number;
  dist?: number;
  /** Reveal window along the layer's drawing order (the entrance sweeps `uReveal` through it). */
  r0?: number;
  r1?: number;
  /** Group the segment belongs to: focus dims the others. -1 none. */
  g?: number;
  shade?: boolean;
  hid?: boolean;
  stroke?: boolean;
  gate?: boolean;
  col: RGB;
};

export type LineLayers = { struct: Seg[]; rim: Seg[]; plan: Seg[]; world: Seg[]; strokes: Seg[] };

const dist3 = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/** A polyline as consecutive segments; the dash phase and the reveal window run on along it. */
export function polyline(points: readonly Vec3[], tpl: Omit<Seg, 'a' | 'b'>): Seg[] {
  const out: Seg[] = [];
  let total = 0;
  for (let i = 1; i < points.length; i += 1) total += dist3(points[i - 1], points[i]);
  let run = 0;
  const r0 = tpl.r0 ?? 0;
  const r1 = tpl.r1 ?? 0;
  for (let i = 1; i < points.length; i += 1) {
    const len = dist3(points[i - 1], points[i]);
    out.push({ ...tpl, a: points[i - 1], b: points[i], dist: run, r0: lerp(r0, r1, total ? run / total : 0), r1: lerp(r0, r1, total ? (run + len) / total : 1) });
    run += len;
  }
  return out;
}

const circle = (radius: number, y: number, steps = 160): Vec3[] => {
  const out: Vec3[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const a = rad((i / steps) * 360);
    out.push([Math.sin(a) * radius, y, Math.cos(a) * radius]);
  }
  return out;
};

export function groupIndexOf(model: Pick<ChartModel, 'groups'>): Map<string, number> {
  return new Map(model.groups.map((group, i) => [group.id, i]));
}

/** The group a star dims with: every group when collapsed, only topics otherwise. */
export function starGroup(model: Pick<ChartModel, 'collapsed'>, index: Map<string, number>, star: ChartStar): number {
  const id = model.collapsed ? star.group : star.topicId;
  return id !== null && index.has(id) ? index.get(id)! : -1;
}

/** Ticks of one cell, side by side across its ribbon, each tied to the star it turns into. */
export function cellStrokes(
  cell: ChartCell, phi: number, frame: ConeFrame, model: ChartModel, g: number, theme: ConeTheme, width: number,
  opts: { loose: boolean; fragment: boolean; reveal0?: number },
): Seg[] {
  const n = cell.collapsedIds.length;
  if (!n) return [];
  const [lo, hi] = bandSpan(cell.band);
  const a = frame.pos(lo, phi);
  const b = frame.pos(hi, phi);
  const tangent: Vec3 = [Math.cos(rad(phi)), 0, -Math.sin(rad(phi))];
  const col = rgb(theme.ink);
  const reveal = opts.reveal0 ?? clamp(lo / frame.thToday, 0, 1) * 0.8;
  return cell.collapsedIds.map((id, i) => {
    const star = model.byId.get(id)!;
    const u = ((i + 0.5) / n - 0.5) * width;
    const shift = (p: Vec3): Vec3 => [p[0] + tangent[0] * u, p[1], p[2] + tangent[2] * u];
    return {
      a: shift(a), b: shift(b), x: frame.pos(star.theta, star.phi), w: 1, alpha: opts.loose ? 0.34 : 0.55,
      kind: opts.fragment ? 3 : 0, col, g, r0: reveal, r1: reveal + 0.12, hid: true, stroke: true,
    };
  });
}

/** Every collapsed record of the chart as a tick: one segment per entry of every cell's `collapsedIds`. */
export function strokeSegments(model: ChartModel, frame: ConeFrame, theme: ConeTheme, plateRadiusPx: number): Seg[] {
  if (!model.collapsed) return [];
  const index = groupIndexOf(model);
  const width = RIBBON_W / Math.max(1, plateRadiusPx);
  const out: Seg[] = [];
  for (const group of model.groups) {
    const loose = group.id === '@loose';
    const fragment = group.id === '@fragment';
    for (const cell of group.cells) {
      out.push(...cellStrokes(cell, group.phi, frame, model, index.get(group.id)!, theme, width, { loose, fragment }));
    }
  }
  return out;
}

export function buildLayers(model: ChartModel, frame: ConeFrame, theme: ConeTheme, state: ChartKind, plateRadiusPx: number): LineLayers {
  const struct: Seg[] = [];
  const rim: Seg[] = [];
  const plan: Seg[] = [];
  const world: Seg[] = [];
  const index = groupIndexOf(model);
  const ink = rgb(theme.ink);
  const acc = rgb(theme.acc);
  const her = rgb(theme.her);
  const line = rgb(theme.struct);
  const hasChart = model.origin !== null && state !== 'empty';
  const normal = state === 'normal';
  const collapsed = model.collapsed && normal;
  const sg = (star: ChartStar) => starGroup(model, index, star);
  const rings = model.rings.filter((ring) => !ring.nearToday);

  if (hasChart) {
    const spread = Math.min(0.1, 1 / Math.max(1, rings.length));
    rings.forEach((ring, i) => {
      struct.push(...polyline(circle(frame.r(ring.theta), 0, 128).map((p): Vec3 => [p[0], frame.y(ring.theta), p[2]]), {
        w: 1, alpha: 0.3 * theme.structA, kind: theme.dots ? 3 : 0, col: line, r0: i * spread, r1: i * spread + 0.35, g: -1, hid: true,
      }));
    });
    struct.push(...polyline(circle(frame.r(9), frame.y(9), 96), { w: 1, alpha: 0.2 * theme.structA, kind: 3, col: line, r0: 0, r1: 0.3, hid: true }));
    const dim = collapsed ? 0.35 : 1;
    for (let i = 0; i < 48; i += 1) {
      struct.push(...polyline([frame.apex, frame.pos(frame.thToday, i * 7.5)], {
        w: 1, alpha: (i % 4 === 0 ? 0.26 : 0.13) * theme.structA * dim, kind: 0, col: line, shade: true, hid: true, r0: 0.1, r1: 0.55,
      }));
    }
    const rule = frame.pos(frame.thToday * 1.08, model.scalePhi);
    struct.push(...polyline([frame.apex, [rule[0], 0.02, rule[2]]], { w: 1, alpha: 0.32 * theme.structA, kind: 2, col: line, r0: 0.05, r1: 0.5 }));
  }
  struct.push(...polyline([frame.apex, [0, 0.18, 0]], { w: 1, alpha: 0.42, kind: 2, col: ink, r0: 0, r1: 0.4, g: -2 }));
  if (hasChart) {
    for (const ring of model.rings) {
      const y = frame.y(ring.theta);
      struct.push({ a: [-0.018, y, 0], b: [0.018, y, 0], w: 1, alpha: 0.35, kind: 0, col: ink, r0: 0.3, r1: 0.4, g: -2 });
    }
  }
  ([[0.05, 0, 0], [0.036, 0.012, 15], [0.024, 0.022, 30]] as const).forEach(([half, lift, turn], i) => {
    const square: Vec3[] = [];
    for (let k = 0; k <= 4; k += 1) {
      const a = rad(turn + 45 + k * 90);
      square.push([Math.cos(a) * half * Math.SQRT2, -frame.hc + lift - 0.012, Math.sin(a) * half * Math.SQRT2]);
    }
    struct.push(...polyline(square, { w: 1, alpha: 0.75 - i * 0.12, kind: 0, col: ink, r0: 0, r1: 0.12, g: -3 }));
  });

  rim.push(...polyline(circle(frame.rToday, 0), { w: 1.25, alpha: hasChart ? 0.85 : 0.35, kind: 0, col: acc, r0: 0, r1: 1 }));
  rim.push(...polyline(circle(frame.rToday + 0.046, 0), { w: 1, alpha: 0.2, kind: 0, col: ink, r0: 0, r1: 1 }));
  rim.push(...polyline(circle(frame.rim, 0), { w: 1, alpha: 0.42, kind: 0, col: ink, r0: 0, r1: 1 }));
  rim.push(...polyline(circle(frame.rim, -0.016), { w: 1, alpha: 0.16, kind: 0, col: ink, r0: 0, r1: 1 }));
  // One short tick per record on the bezel: the whole circle reads as "which way she remembers".
  const tickAlpha = 0.6 * clamp(Math.sqrt(150 / Math.max(1, model.stars.length)), 0.2, 1);
  if (hasChart) {
    for (const star of model.stars) {
      const a = rad(star.phi);
      const from = frame.rToday + 0.008;
      const to = from + (star.pinned ? 0.04 : 0.024);
      rim.push({ a: [Math.sin(a) * from, 0, Math.cos(a) * from], b: [Math.sin(a) * to, 0, Math.cos(a) * to], w: 1, alpha: tickAlpha, kind: 0, col: ink, r0: 0.5, r1: 0.9, g: sg(star) });
    }
  }

  if (hasChart) {
    for (const ring of rings) plan.push(...polyline(circle(frame.r(ring.theta), 0, 128), { w: 1, alpha: 0.07, kind: 1, col: ink }));
    if (normal && !model.collapsed) {
      model.topics.forEach((topic, i) => {
        if (!topic.starIds.length) return;
        const pts = topic.starIds.map((id): Vec3 => {
          const star = model.byId.get(id)!;
          const p = frame.pos(star.theta, star.phi);
          return [p[0], 0, p[2]];
        });
        plan.push(...polyline(pts, { w: 1, alpha: 0.13, kind: 0, col: ink, g: i }));
      });
    }
    const arm = 0.007;
    for (const star of model.stars) {
      if (model.collapsed && !star.single) continue;
      const p = frame.pos(star.theta, star.phi);
      const g = sg(star);
      plan.push({ a: [p[0] - arm, 0, p[2]], b: [p[0] + arm, 0, p[2]], w: 1, alpha: 0.26, col: ink, g });
      plan.push({ a: [p[0], 0, p[2] - arm], b: [p[0], 0, p[2] + arm], w: 1, alpha: 0.26, col: ink, g });
    }
  }

  if (hasChart && normal) {
    const live = model.topics.filter((topic) => topic.starIds.length);
    const stagger = Math.min(0.07, 0.6 / Math.max(1, live.length));
    live.forEach((topic, order) => {
      const g = index.get(topic.id)!;
      const stars = topic.starIds.map((id) => model.byId.get(id)!);
      const path: Vec3[] = [];
      stars.forEach((star, i) => {
        if (i === 0) { path.push(frame.pos(star.theta, star.phi)); return; }
        const prev = stars[i - 1];
        const turn = ((star.phi - prev.phi + 540) % 360) - 180;
        for (let k = 1; k <= 10; k += 1) path.push(frame.pos(lerp(prev.theta, star.theta, k / 10), prev.phi + turn * (k / 10)));
      });
      const r0 = model.collapsed ? 0 : 0.05 + order * stagger;
      world.push(...polyline(path, { w: 1.4, alpha: 0.72, kind: 0, col: ink, g, r0, r1: r0 + 0.55, hid: true, gate: model.collapsed }));
      const last = stars[stars.length - 1];
      const tail: Vec3[] = [];
      for (let k = 0; k <= 8; k += 1) tail.push(frame.pos(lerp(last.theta, frame.thToday, k / 8), lerp(last.phi, topic.phi, k / 8)));
      world.push(...polyline(tail, { w: 1, alpha: 0.32, kind: 3, col: ink, g, r0: r0 + 0.5, r1: r0 + 0.62, hid: true, gate: model.collapsed }));
    });
    // A fragment fades toward the axis: its short tail points that way.
    for (const star of model.stars) {
      if (star.kind !== 'fragment' || star.pinned) continue;
      const here = frame.pos(star.theta, star.phi);
      const toward = frame.pos(star.theta * 0.9, star.phi);
      const d: Vec3 = [toward[0] - here[0], toward[1] - here[1], toward[2] - here[2]];
      const len = Math.hypot(d[0], d[1], d[2]) || 1;
      const u = d.map((v) => v / len);
      world.push({
        a: [here[0] + u[0] * 0.012, here[1] + u[1] * 0.012, here[2] + u[2] * 0.012], b: [here[0] + u[0] * 0.045, here[1] + u[1] * 0.045, here[2] + u[2] * 0.045],
        w: 1, alpha: 0.5, kind: 0, col: ink, g: sg(star), r0: 0.6, r1: 0.7, hid: true, gate: model.collapsed && !star.single,
      });
    }
  }

  // The "new since you were last here" crosses are drawn one after another once the cone has formed.
  if (hasChart) {
    const fresh = model.stars.filter((star) => star.isNew);
    const step = Math.min(0.42, 2.4 / Math.max(1, fresh.length));
    const arm = 9 / Math.max(1, plateRadiusPx);
    fresh.forEach((star, i) => {
      const p = frame.pos(star.theta, star.phi);
      const t = [Math.cos(rad(star.phi)), 0, -Math.sin(rad(star.phi))];
      const radial = [Math.sin(rad(star.phi)), 0, Math.cos(rad(star.phi))];
      const r0 = 1.2 + i * step;
      const axes = [t, radial, [0, 1, 0]];
      for (const u of axes) {
        world.push({
          a: [p[0] - u[0] * arm, p[1] - u[1] * arm, p[2] - u[2] * arm], b: [p[0] + u[0] * arm, p[1] + u[1] * arm, p[2] + u[2] * arm],
          w: 1.2, alpha: 0.95, kind: 0, col: her, g: sg(star), r0, r1: r0 + step * 0.98, hid: true,
        });
      }
    });
  }

  const strokes = hasChart && normal ? strokeSegments(model, frame, theme, plateRadiusPx) : [];
  return { struct, rim, plan, world, strokes };
}

export type Packed = {
  count: number;
  iA: Float32Array; iB: Float32Array; iX: Float32Array; iP: Float32Array; iR: Float32Array; iG: Float32Array; iC: Float32Array;
};

export function packSegs(segs: readonly Seg[]): Packed {
  const n = Math.max(1, segs.length);
  const out: Packed = {
    count: segs.length,
    iA: new Float32Array(n * 3), iB: new Float32Array(n * 3), iX: new Float32Array(n * 3), iP: new Float32Array(n * 4),
    iR: new Float32Array(n * 2), iG: new Float32Array(n * 2), iC: new Float32Array(n * 3),
  };
  segs.forEach((s, i) => {
    out.iA.set(s.a, i * 3);
    out.iB.set(s.b, i * 3);
    if (s.x) out.iX.set(s.x, i * 3);
    out.iP.set([s.w ?? 1, s.alpha ?? 1, s.kind ?? 0, s.dist ?? 0], i * 4);
    out.iR.set([s.r0 ?? 0, s.r1 ?? 0], i * 2);
    out.iG.set([s.g ?? -1, (s.shade ? 1 : 0) + (s.hid ? 2 : 0) + (s.stroke ? 4 : 0) + (s.gate ? 8 : 0)], i * 2);
    out.iC.set(s.col, i * 3);
  });
  return out;
}

export type StarInstances = {
  ids: string[];
  pos: Float32Array;
  /** radius px, kind (0 cognition, 1 loose, 2 fragment), group, pinned */
  s: Float32Array;
  /** index, appearance order (0..0.85) */
  t: Float32Array;
  /** 1: a collapsed record, invisible until its group opens */
  k: Float32Array;
  world: Vec3[];
};

export function starInstances(model: ChartModel, frame: ConeFrame): StarInstances {
  const index = groupIndexOf(model);
  const stars = model.stars;
  const n = Math.max(1, stars.length);
  const out: StarInstances = {
    ids: stars.map((s) => s.id), pos: new Float32Array(n * 3), s: new Float32Array(n * 4), t: new Float32Array(n * 2), k: new Float32Array(n), world: [],
  };
  const order = new Map([...stars].sort((a, b) => a.theta - b.theta).map((s, i) => [s.id, i]));
  stars.forEach((star, i) => {
    const p = frame.pos(star.theta, star.phi);
    out.world.push(p);
    out.pos.set(p, i * 3);
    out.s.set([star.radius, star.kind === 'fact' ? 0 : star.kind === 'loose' ? 1 : 2, starGroup(model, index, star), star.pinned ? 1 : 0], i * 4);
    out.t.set([i, (order.get(star.id)! / Math.max(1, stars.length)) * 0.85], i * 2);
    out.k[i] = model.collapsed && !star.single ? 1 : 0;
  });
  return out;
}

/** Theta span of a calendar month, clamped to the chart. */
export function monthTheta(model: ChartModel, key: string): [number, number] | null {
  if (model.origin === null) return null;
  const [y, m] = key.split('-').map(Number);
  if (!y || !m) return null;
  const lo = Math.max(9, thetaAt(model.origin, new Date(y, m - 1, 1).getTime()));
  const hi = Math.min(model.thetaToday, thetaAt(model.origin, new Date(y, m, 1).getTime()));
  return hi > lo ? [lo, hi] : null;
}

export type AnnotView = { selectedId: string | null; soul: boolean; monthKey: string | null; marks: readonly string[]; state: ChartKind };

/** Measuring lines for the selection, the SOUL axis and this turn's references. */
export function annotSegments(model: ChartModel, frame: ConeFrame, theme: ConeTheme, view: AnnotView): Seg[] {
  const out: Seg[] = [];
  const acc = rgb(theme.acc);
  const her = rgb(theme.her);
  const star = view.selectedId ? model.byId.get(view.selectedId) : null;
  if (star) {
    const p = frame.pos(star.theta, star.phi);
    // Down to the disc: where the plate shows it. Across to the axis: its date.
    out.push(...polyline([p, [p[0], 0, p[2]]], { w: 1, alpha: 0.55, kind: 3, col: acc, r0: 0, r1: 0.5 }));
    out.push(...polyline([p, [0, p[1], 0]], { w: 1, alpha: 0.6, kind: 1, col: acc, r0: 0.2, r1: 0.7 }));
    const arm = 0.014;
    out.push({ a: [p[0] - arm, 0, p[2]], b: [p[0] + arm, 0, p[2]], w: 1, alpha: 0.7, col: acc, r0: 0.5, r1: 0.6 });
    out.push({ a: [p[0], 0, p[2] - arm], b: [p[0], 0, p[2] + arm], w: 1, alpha: 0.7, col: acc, r0: 0.5, r1: 0.6 });
  }
  if (view.state === 'normal') {
    for (const id of view.marks) {
      const mark = model.byId.get(id);
      if (mark) out.push(...polyline([[0, 0, 0], frame.pos(mark.theta, mark.phi)], { w: 1, alpha: 0.75, kind: 1, col: acc, r0: 0, r1: 1 }));
    }
  }
  if (view.soul) out.push(...polyline([frame.apex, [0, 0, 0]], { w: 2, alpha: 0.9, kind: 0, col: her, r0: 0, r1: 1 }));
  const span = view.monthKey ? monthTheta(model, view.monthKey) : null;
  if (span) {
    for (const [i, theta] of span.entries()) {
      out.push(...polyline(circle(frame.r(theta), frame.y(theta), 128), { w: i ? 1 : 1.5, alpha: i ? 0.55 : 0.85, kind: i ? 1 : 0, col: acc, r0: 0, r1: 1 }));
    }
  }
  return out;
}
