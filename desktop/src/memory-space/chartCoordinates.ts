// Plate coordinates for the memory chart (DIRECTION section 4). Pure math, no DOM:
// the SVG plate projects these straight down, the 3D light cone lifts the same
// (theta, phi) pairs to their dates.
//
// theta: distance from SOUL in "plate degrees", 9 + 6.2 * sqrt(days since the first
//        conversation), so each day covers the same area.
// phi:   bearing in degrees; topics take golden-angle bearings in the order they
//        were created, their records scatter a little either side.
import { parseMicros, parseMs } from './chartTime';
import type { MemoryChart } from './types';

export const DAY_MS = 86_400_000;
/** A young chart is drawn at no less than six weeks of scale. */
export const MIN_SPAN_DAYS = 42;
/** Closest two stars may sit, in theta units (about 22px at 1440x900 three months in). */
export const MIN_SEPARATION = 4.6;
const GOLDEN = 137.508;
const NEAR_TODAY = 2.2;
const EMPTY_TODAY = 18;
/** A scope with this many records (cognitions plus fragments) is drawn collapsed. */
export const COLLAPSE_AT = 150;
/** A collapsed scope goes back to full stars only once it falls below this: the gap stops it flickering at the line. */
export const EXPAND_BELOW = 130;
export const LOOSE_GROUP = '@loose';
export const FRAGMENT_GROUP = '@fragment';

export type StarKind = 'fact' | 'loose' | 'fragment';
export type ChartStar = {
  id: string;
  kind: StarKind;
  no: number;
  topicId: string | null;
  label: string;
  createdAt: number;
  updatedAt: number;
  expiresAt: number | null;
  theta: number;
  phi: number;
  radius: number;
  pinned: boolean;
  isNew: boolean;
  evidenceCount: number;
  versionCount: number;
  /** Topic id, or LOOSE_GROUP / FRAGMENT_GROUP. */
  group: string;
  /** `${group}|${YYYY-MM}` */
  cellKey: string;
  resident: boolean;
  /** Drawn as a star even when the chart is collapsed: resident or new since the last visit. */
  single: boolean;
};
export type TopicArm = { id: string; label: string; index: number; phi: number; createdAt: number; starIds: string[] };
export type RingKind = 'month' | 'quarter' | 'year';
export type MonthRing = {
  key: string; year: number; month: number; theta: number; nearToday: boolean;
  kind: RingKind;
  /** More than twelve months back. */
  old: boolean;
};
/** One calendar month on the time axis; cells always follow months, whatever rings are drawn. */
export type MonthBand = { key: string; start: number; end: number; theta0: number; theta1: number; starIds: string[] };
export type ChartCell = {
  key: string;
  groupId: string;
  band: MonthBand;
  /** Every record of the cell, in the order they were written down. */
  starIds: string[];
  /** The ones drawn as ticks when collapsed (no single stars), same order. */
  collapsedIds: string[];
  /** Theta of the middle record and the circular mean of the bearings. */
  theta: number;
  phi: number;
};
export type ChartGroup = {
  id: string; label: string; phi: number; isTopic: boolean; starIds: string[];
  /** By month. */
  cells: ChartCell[];
};
export type ChartModel = {
  origin: number | null;
  today: number;
  thetaToday: number;
  thetaFit: number;
  topics: TopicArm[];
  stars: ChartStar[];
  byId: Map<string, ChartStar>;
  rings: MonthRing[];
  collapsed: boolean;
  bands: MonthBand[];
  /** Topics in the order they were made, then LOOSE_GROUP and FRAGMENT_GROUP. */
  groups: ChartGroup[];
  cells: Map<string, ChartCell>;
  /** Bearing of the widest empty wedge; the time ruler and month labels run along it. */
  scalePhi: number;
};

export const norm = (deg: number) => ((deg % 360) + 360) % 360;
const rad = (deg: number) => (deg * Math.PI) / 180;

export function hash01(text: string): number {
  let h = 2166136261;
  for (const ch of text) {
    h ^= ch.codePointAt(0)!;
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

export function thetaAt(origin: number, at: number): number {
  return 9 + 6.2 * Math.sqrt(Math.max(0, (at - origin) / DAY_MS));
}

export function starRadius(kind: StarKind, evidenceCount: number): number {
  if (kind === 'fragment') return 3.4;
  if (kind === 'loose') return 2.2;
  return 1.9 + 0.55 * Math.min(4, evidenceCount);
}

type Gap = { mid: number; width: number };
function gapsBetween(phis: number[]): Gap[] {
  const sorted = phis.map(norm).sort((a, b) => a - b);
  return sorted.map((p, i) => {
    const q = i + 1 < sorted.length ? sorted[i + 1] : sorted[0] + 360;
    return { mid: norm((p + q) / 2), width: q - p };
  }).sort((a, b) => b.width - a.width || a.mid - b.mid);
}

// Placed stars bucketed by plane position, cells MIN_SEPARATION wide. A clearance only
// changes a decision while it is below MIN_SEPARATION, and every star that close lies
// in the 3x3 cells around the probe, so looking there alone gives the same result as
// comparing with every star, bit for bit: x = sin(phi) * theta, y = cos(phi) * theta and
// hypot(dx, dy) are the same floats a pairwise comparison would compute.
type Placed = { x: number; y: number };
const cellOf = (v: number) => Math.floor(v / MIN_SEPARATION);
const cellKey = (cx: number, cy: number) => (cx + 4096) * 8192 + (cy + 4096);
class PlacedGrid {
  private cells = new Map<number, Placed[]>();
  add(star: ChartStar) {
    const p = { x: Math.sin(rad(star.phi)) * star.theta, y: Math.cos(rad(star.phi)) * star.theta };
    const key = cellKey(cellOf(p.x), cellOf(p.y));
    const list = this.cells.get(key);
    if (list) list.push(p); else this.cells.set(key, [p]);
  }
  /** Distance to the nearest placed star when it is below MIN_SEPARATION; otherwise some value at or above it. */
  clearance(theta: number, phi: number): number {
    const x = Math.sin(rad(phi)) * theta;
    const y = Math.cos(rad(phi)) * theta;
    let min = Infinity;
    const x1 = cellOf(x + MIN_SEPARATION);
    const y0 = cellOf(y - MIN_SEPARATION);
    const y1 = cellOf(y + MIN_SEPARATION);
    for (let cx = cellOf(x - MIN_SEPARATION); cx <= x1; cx += 1) {
      for (let cy = y0; cy <= y1; cy += 1) {
        const list = this.cells.get(cellKey(cx, cy));
        if (!list) continue;
        for (const other of list) {
          const dx = x - other.x;
          const dy = y - other.y;
          if (dx >= MIN_SEPARATION || dx <= -MIN_SEPARATION || dy >= MIN_SEPARATION || dy <= -MIN_SEPARATION) continue;
          const d = Math.hypot(dx, dy);
          if (d < min) min = d;
        }
      }
    }
    return min;
  }
}

// Only the newer star gives way, and only around its ring: its date never moves, and
// nothing written earlier is disturbed by what comes later.
function makeRoom(star: ChartStar, placed: PlacedGrid): void {
  const clearance = (phi: number) => placed.clearance(star.theta, phi);
  if (clearance(star.phi) >= MIN_SEPARATION) return;
  const step = Math.min(6, (MIN_SEPARATION / 3 / Math.max(star.theta, 5)) * 57.3);
  const first = hash01(`${star.id}:side`) < 0.5 ? -1 : 1;
  let best = { phi: star.phi, room: clearance(star.phi) };
  for (let k = 1; k * step <= 40; k += 1) {
    for (const side of [first, -first]) {
      const phi = star.phi + side * k * step;
      const room = clearance(phi);
      if (room >= MIN_SEPARATION) { star.phi = norm(phi); return; }
      if (room > best.room) best = { phi, room };
    }
  }
  star.phi = norm(best.phi);
}

/** `wasCollapsed` is the state the caller drew last time for this scope; it only matters between EXPAND_BELOW and COLLAPSE_AT. */
export function buildChartModel(
  chart: MemoryChart,
  options: { today: number; lastOpen: number | null; wasCollapsed?: boolean },
): ChartModel {
  const today = options.today;
  const firstRecord = [...chart.facts, ...chart.experiences].map((row) => parseMs(row.created_at)).sort((a, b) => a - b)[0];
  // origin_at is the first message of the scope; after its oldest conversation is deleted, records can
  // be older than it. The chart starts at whichever is earlier, so no record is dated before the start.
  const origin = chart.origin_at ? Math.min(parseMs(chart.origin_at), firstRecord ?? Infinity) : firstRecord ?? null;
  const thetaToday = origin === null ? EMPTY_TODAY : thetaAt(origin, today);
  const minFit = 9 + 6.2 * Math.sqrt(MIN_SPAN_DAYS);
  const thetaFit = Math.max(thetaToday, minFit);
  const theta = (at: number) => (origin === null ? 9 : thetaAt(origin, at));

  // True write order, to the microsecond: two records in the same millisecond must not
  // swap by id, or a later one would push an earlier star aside. Only exact ties use id.
  const micros = new Map<string, number>();
  const at = (text: string) => {
    let us = micros.get(text);
    if (us === undefined) { us = parseMicros(text) ?? 0; micros.set(text, us); }
    return us;
  };
  const byCreated = <T extends { created_at: string; id: string }>(a: T, b: T) =>
    at(a.created_at) - at(b.created_at) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const topics: TopicArm[] = [...chart.topics].sort(byCreated).map((topic, index) => ({
    id: topic.id, label: topic.label, index, phi: norm(-28 + index * GOLDEN),
    createdAt: parseMs(topic.created_at), starIds: [],
  }));
  const topicById = new Map(topics.map((topic) => [topic.id, topic]));

  const rows = [
    ...chart.facts.map((row) => ({ row, fact: row as MemoryChart['facts'][number] | null, fragment: false })),
    ...chart.experiences.map((row) => ({ row, fact: null, fragment: true })),
  ].sort((a, b) => byCreated(a.row, b.row));

  const stars: ChartStar[] = [];
  const placed = new PlacedGrid();
  rows.forEach(({ row, fact, fragment }, i) => {
    const createdAt = parseMs(row.created_at);
    const th = theta(createdAt);
    const topic = fact?.topic_id ? topicById.get(fact.topic_id) ?? null : null;
    const kind: StarKind = fragment ? 'fragment' : topic ? 'fact' : 'loose';
    let phi: number;
    if (topic) {
      const across = (hash01(`${row.id}j`) - 0.5) * 2 * 26;
      phi = topic.phi + Math.max(-24, Math.min(24, (across / Math.max(30, th * 5.2)) * 57.3));
    } else {
      // Loose stars and fragments live in the widest wedges between the topics that
      // existed when they were written down.
      const gaps = gapsBetween(topics.filter((t) => t.createdAt <= createdAt).map((t) => t.phi));
      if (gaps.length) {
        const gap = gaps[Math.floor(hash01(row.id) * Math.min(3, gaps.length))];
        const side = hash01(`${row.id}s`) < 0.5 ? -1 : 1;
        phi = gap.mid + side * (7 + hash01(`${row.id}k`) * Math.min(16, gap.width * 0.28));
      } else {
        phi = -150 + hash01(row.id) * 300;
      }
    }
    const evidenceCount = fact?.evidence_count ?? 1;
    const star: ChartStar = {
      id: row.id, kind, no: i + 1, topicId: topic?.id ?? null, label: row.label,
      createdAt, updatedAt: parseMs(row.updated_at),
      expiresAt: fragment && (row as { expires_at: string | null }).expires_at
        ? parseMs((row as { expires_at: string }).expires_at) : null,
      theta: th, phi: norm(phi), radius: starRadius(kind, evidenceCount), pinned: row.is_pinned,
      isNew: options.lastOpen !== null && createdAt > options.lastOpen,
      evidenceCount, versionCount: fact?.version_count ?? 1,
      group: topic ? topic.id : fragment ? FRAGMENT_GROUP : LOOSE_GROUP, cellKey: '',
      resident: fact?.resident ?? false, single: false,
    };
    star.single = star.resident || star.isNew;
    makeRoom(star, placed);
    placed.add(star);
    stars.push(star);
    topic?.starIds.push(star.id);
  });

  const collapsed = stars.length >= COLLAPSE_AT || (Boolean(options.wasCollapsed) && stars.length >= EXPAND_BELOW);

  // The time axis. Bands are always one month each; rings are drawn every month for the last
  // twelve, and further back only as quarters and years, so an old chart is not a fog of rings.
  // A chart that is not collapsed keeps all its month rings as it always had.
  const rings: MonthRing[] = [];
  const bands: MonthBand[] = [];
  if (origin !== null) {
    const todayDate = new Date(today);
    const cut = new Date(todayDate.getFullYear(), todayDate.getMonth() - 12, 1).getTime();
    const first = new Date(origin);
    const edges = [{ t: origin, th: theta(origin), y: first.getFullYear(), m: first.getMonth() + 1 }];
    for (let y = first.getFullYear(), m = first.getMonth() + 1; ; m += 1) {
      if (m > 11) { y += 1; m = 0; }
      const at = new Date(y, m, 1).getTime();
      if (at > today) break;
      const th = theta(at);
      edges.push({ t: at, th, y, m: m + 1 });
      const kind: RingKind = m === 0 ? 'year' : m % 3 === 0 ? 'quarter' : 'month';
      const old = at < cut;
      if (collapsed && old && kind === 'month') continue;
      rings.push({
        key: `${y}-${String(m + 1).padStart(2, '0')}`, year: y, month: m + 1, theta: th,
        nearToday: thetaToday - th < NEAR_TODAY, kind, old,
      });
    }
    edges.push({ t: today, th: thetaToday, y: 0, m: 0 });
    for (let i = 0; i + 1 < edges.length; i += 1) {
      const a = edges[i];
      bands.push({
        key: `${a.y}-${String(a.m).padStart(2, '0')}`, start: a.t, end: edges[i + 1].t,
        theta0: a.th, theta1: edges[i + 1].th, starIds: [],
      });
    }
  }

  const armed = topics.filter((topic) => topic.starIds.length);
  const gaps = gapsBetween(armed.map((t) => t.phi));
  const groups: ChartGroup[] = [
    ...topics.map((topic) => ({
      id: topic.id, label: topic.label, phi: topic.phi, isTopic: true, starIds: topic.starIds, cells: [] as ChartCell[],
    })),
    { id: LOOSE_GROUP, label: '未归组', phi: gaps.length > 1 ? gaps[1].mid : 150, isTopic: false,
      starIds: [] as string[], cells: [] as ChartCell[] },
    { id: FRAGMENT_GROUP, label: '片段', phi: gaps.length > 2 ? gaps[2].mid : 210, isTopic: false,
      starIds: [] as string[], cells: [] as ChartCell[] },
  ];
  const groupById = new Map(groups.map((group) => [group.id, group]));

  // One pass over the stars in the order they were written: each lands in the month it was written in.
  const cells = new Map<string, ChartCell>();
  let bi = 0;
  for (const star of stars) {
    const group = groupById.get(star.group)!;
    if (!group.isTopic) group.starIds.push(star.id);
    if (!bands.length) continue;
    while (bi + 1 < bands.length && star.createdAt >= bands[bi + 1].start) bi += 1;
    const band = bands[bi];
    band.starIds.push(star.id);
    star.cellKey = `${group.id}|${band.key}`;
    let cell = cells.get(star.cellKey);
    if (!cell) {
      cell = { key: star.cellKey, groupId: group.id, band, starIds: [], collapsedIds: [], theta: 0, phi: 0 };
      cells.set(star.cellKey, cell);
      group.cells.push(cell);
    }
    cell.starIds.push(star.id);
    if (!star.single) cell.collapsedIds.push(star.id);
  }
  const byId = new Map(stars.map((star) => [star.id, star]));
  for (const cell of cells.values()) {
    cell.theta = byId.get(cell.starIds[Math.floor(cell.starIds.length / 2)])!.theta;
    let sin = 0;
    let cos = 0;
    for (const id of cell.starIds) { const p = rad(byId.get(id)!.phi); sin += Math.sin(p); cos += Math.cos(p); }
    cell.phi = norm((Math.atan2(sin, cos) * 180) / Math.PI);
  }
  const widest = gaps[0];
  return {
    origin, today, thetaToday, thetaFit, topics, stars, byId,
    rings, collapsed, bands, groups, cells, scalePhi: widest ? widest.mid : 205,
  };
}

/** Offset from the plate centre in px; y grows downward like SVG. */
export function platePoint(theta: number, phi: number, radius: number, thetaFit: number): { x: number; y: number } {
  const k = (radius / thetaFit) * 0.96;
  return { x: Math.sin(rad(phi)) * theta * k, y: Math.cos(rad(phi)) * theta * k };
}

/** Plate centre and radius inside a sheet, leaving the right lane to the reading surface. */
export function plateGeometry(width: number, height: number): { cx: number; cy: number; radius: number } {
  return {
    cx: Math.round((width - 400) / 2 + 56),
    cy: Math.round(height / 2 + 22),
    radius: Math.max(80, Math.min((width - 400) * 0.4, height * 0.375)),
  };
}

/** Frame cell (A-F x 1-8) of a sheet point, as printed on the frame's edges. */
export function zoneOf(x: number, y: number, width: number, height: number): string {
  const col = Math.min(8, Math.max(1, Math.floor((x - 14) / ((width - 28) / 8)) + 1));
  const row = 'ABCDEF'[Math.min(5, Math.max(0, Math.floor((y - 14) / ((height - 28) / 6))))];
  return `${row}${col}`;
}
