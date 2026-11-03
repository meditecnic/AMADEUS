import { describe, expect, it } from 'vitest';
import { MIN_SEPARATION, buildChartModel, hash01, norm, thetaAt, DAY_MS } from './chartCoordinates';
import { parseMicros, parseMs } from './chartTime';
import type { MemoryChart } from './types';

// Reference: the placement as it was before the grid, every probe against every star.
// Kept verbatim so the grid is checked against it, not against itself.
const GOLDEN = 137.508;
const rad = (deg: number) => (deg * Math.PI) / 180;
type Spot = { id: string; theta: number; phi: number };
function gapsBetween(phis: number[]) {
  const sorted = phis.map(norm).sort((a, b) => a - b);
  return sorted.map((p, i) => {
    const q = i + 1 < sorted.length ? sorted[i + 1] : sorted[0] + 360;
    return { mid: norm((p + q) / 2), width: q - p };
  }).sort((a, b) => b.width - a.width || a.mid - b.mid);
}
function planeDistance(a: { theta: number; phi: number }, b: { theta: number; phi: number }) {
  return Math.hypot(
    Math.sin(rad(a.phi)) * a.theta - Math.sin(rad(b.phi)) * b.theta,
    Math.cos(rad(a.phi)) * a.theta - Math.cos(rad(b.phi)) * b.theta,
  );
}
function makeRoom(star: Spot, placed: Spot[]): boolean {
  const clearance = (phi: number) => placed.reduce(
    (min, other) => Math.min(min, planeDistance({ theta: star.theta, phi }, other)), Infinity);
  if (clearance(star.phi) >= MIN_SEPARATION) return false;
  const step = Math.min(6, (MIN_SEPARATION / 3 / Math.max(star.theta, 5)) * 57.3);
  const first = hash01(`${star.id}:side`) < 0.5 ? -1 : 1;
  let best = { phi: star.phi, room: clearance(star.phi) };
  for (let k = 1; k * step <= 40; k += 1) {
    for (const side of [first, -first]) {
      const phi = star.phi + side * k * step;
      const room = clearance(phi);
      if (room >= MIN_SEPARATION) { star.phi = norm(phi); return true; }
      if (room > best.room) best = { phi, room };
    }
  }
  star.phi = norm(best.phi);
  return true;
}
export function referenceSpots(chart: MemoryChart): { spots: Spot[]; pushed: number } {
  const firstRecord = [...chart.facts, ...chart.experiences].map((row) => parseMs(row.created_at)).sort((a, b) => a - b)[0];
  const origin = chart.origin_at ? parseMs(chart.origin_at) : firstRecord ?? null;
  const theta = (at: number) => (origin === null ? 9 : thetaAt(origin, at));
  const byCreated = <T extends { created_at: string; id: string }>(a: T, b: T) =>
    parseMicros(a.created_at)! - parseMicros(b.created_at)! || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const topics = [...chart.topics].sort(byCreated).map((topic, index) => ({
    id: topic.id, phi: norm(-28 + index * GOLDEN), createdAt: parseMs(topic.created_at),
  }));
  const topicById = new Map(topics.map((topic) => [topic.id, topic]));
  const rows = [
    ...chart.facts.map((row) => ({ row, fragment: false })),
    ...chart.experiences.map((row) => ({ row, fragment: true })),
  ].sort((a, b) => byCreated(a.row, b.row));
  const spots: Spot[] = [];
  let pushed = 0;
  rows.forEach(({ row, fragment }) => {
    const createdAt = parseMs(row.created_at);
    const th = theta(createdAt);
    const fact = fragment ? null : chart.facts.find((f) => f.id === row.id)!;
    const topic = fact?.topic_id ? topicById.get(fact.topic_id) ?? null : null;
    let phi: number;
    if (topic) {
      const across = (hash01(`${row.id}j`) - 0.5) * 2 * 26;
      phi = topic.phi + Math.max(-24, Math.min(24, (across / Math.max(30, th * 5.2)) * 57.3));
    } else {
      const gaps = gapsBetween(topics.filter((t) => t.createdAt <= createdAt).map((t) => t.phi));
      if (gaps.length) {
        const gap = gaps[Math.floor(hash01(row.id) * Math.min(3, gaps.length))];
        const side = hash01(`${row.id}s`) < 0.5 ? -1 : 1;
        phi = gap.mid + side * (7 + hash01(`${row.id}k`) * Math.min(16, gap.width * 0.28));
      } else {
        phi = -150 + hash01(row.id) * 300;
      }
    }
    const spot = { id: row.id, theta: th, phi: norm(phi) };
    if (makeRoom(spot, spots)) pushed += 1;
    spots.push(spot);
  });
  return { spots, pushed };
}

// Seeded library: dates denser toward today, no two records in the same millisecond.
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export function seededChart(count: number, topicCount: number, months: number, seed: number): MemoryChart {
  const random = mulberry32(seed);
  const origin = Date.parse('2025-01-01T09:00:00Z');
  const span = months * 30.4 * DAY_MS;
  const used = new Set<number>();
  const when = (u: number) => {
    let at = Math.floor(origin + span * Math.sqrt(u));
    while (used.has(at)) at += 1;
    used.add(at);
    return at;
  };
  const iso = (at: number) => new Date(at).toISOString();
  const topics = Array.from({ length: topicCount }, (_, i) => {
    const at = when(random() * 0.9);
    return { id: `t${i}-${Math.floor(random() * 1e6)}`, label: `主题${i}`, created_at: iso(at), fact_count: 0, at };
  });
  const facts: MemoryChart['facts'] = [];
  const experiences: MemoryChart['experiences'] = [];
  for (let i = 0; i < count; i += 1) {
    const at = when(random());
    const id = `${random() < 0.5 ? 'r' : 'm'}${Math.floor(random() * 1e9).toString(36)}-${i}`;
    if (random() < 0.12) {
      experiences.push({ id, label: id, created_at: iso(at), updated_at: iso(at), is_pinned: random() < 0.1, expires_at: null });
      continue;
    }
    const open = topics.filter((t) => t.at <= at);
    const topic = open.length && random() > 0.1 ? open[Math.floor(random() * open.length)] : null;
    if (topic) topic.fact_count += 1;
    facts.push({ id, label: id, topic_id: topic?.id ?? null, created_at: iso(at), updated_at: iso(at),
      is_pinned: random() < 0.08, evidence_count: 1 + Math.floor(random() * 5), version_count: 1, resident: random() < 0.3 });
  }
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    generated_at: iso(origin + span), origin_at: iso(origin),
    topics: topics.map(({ at: _at, ...topic }) => topic),
    facts, experiences,
    counts: { facts: facts.length, experiences: experiences.length, topics: topics.length }, truncated: false,
  };
}

describe('grid placement matches the all-pairs placement exactly', () => {
  const cases: Array<[number, number, number]> = [[25, 5, 3], [300, 12, 14], [1000, 20, 18], [2000, 30, 24]];
  for (const [count, topicCount, months] of cases) {
    // The all-pairs reference itself takes seconds at 2000 records.
    it(`${count} records, ${topicCount} topics, ${months} months`, { timeout: 120_000 }, () => {
      const chart = seededChart(count, topicCount, months, count * 7919 + topicCount);
      const today = Date.parse(chart.generated_at);
      const model = buildChartModel(chart, { today, lastOpen: null });
      const { spots: reference, pushed } = referenceSpots(chart);
      expect(model.stars).toHaveLength(count);
      reference.forEach((spot, i) => {
        const star = model.stars[i];
        expect(star.id).toBe(spot.id);
        expect(star.theta).toBe(spot.theta);
        expect(star.phi).toBe(spot.phi);
      });
      // Dense enough that stars really do give way, so the grid is exercised.
      expect(pushed).toBeGreaterThan(count / 10);
    });
  }
});
