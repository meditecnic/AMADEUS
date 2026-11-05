// A reproducible library for the cone tests: `count` records over a year and a half, five topics.
import { buildChartModel, type ChartModel } from '../chartCoordinates';
import type { MemoryChart } from '../types';

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const ORIGIN = Date.parse('2025-02-10T09:00:00Z');
export const TODAY = Date.parse('2026-11-03T09:00:00Z');
const iso = (at: number) => new Date(at).toISOString();

export function library(count: number, options: { topics?: number; resident?: (i: number) => boolean; seed?: number } = {}): MemoryChart {
  const random = mulberry32(options.seed ?? count);
  const topicCount = options.topics ?? 5;
  const span = TODAY - ORIGIN;
  const topics = Array.from({ length: topicCount }, (_, i) => ({
    id: `t${i}`, label: `主题${i}`, created_at: iso(ORIGIN + (i + 1) * 1000), fact_count: 0,
  }));
  const facts: MemoryChart['facts'] = [];
  const experiences: MemoryChart['experiences'] = [];
  for (let i = 0; i < count; i += 1) {
    const at = ORIGIN + 60_000 + Math.floor(span * random()) + i;
    const id = `r${String(i).padStart(4, '0')}`;
    if (i % 8 === 7) {
      experiences.push({ id, label: id, created_at: iso(at), updated_at: iso(at), is_pinned: false, expires_at: null });
    } else {
      facts.push({
        id, label: id, topic_id: i % 10 === 3 ? null : topics[i % topicCount].id, created_at: iso(at), updated_at: iso(at),
        is_pinned: false, evidence_count: 1, version_count: 1, resident: options.resident?.(i) ?? false,
      });
    }
  }
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    generated_at: iso(TODAY), origin_at: iso(ORIGIN), topics, facts, experiences,
    counts: { facts: facts.length, experiences: experiences.length, topics: topicCount }, truncated: false,
  };
}

export const modelOf = (count: number, options: Parameters<typeof library>[1] & { lastOpen?: number | null } = {}): ChartModel =>
  buildChartModel(library(count, options), { today: TODAY, lastOpen: options.lastOpen ?? null });
