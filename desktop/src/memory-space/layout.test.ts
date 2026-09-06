import { describe, expect, it } from 'vitest';

import { semanticFilaments, soulLockupLines } from './graphPaths';
import { layoutProjection, readingSurfaceMode, OVERVIEW_REST_CAMERA, overviewFocalPx, projectToScreen } from './layout';
import type { MemoryProjection, ProjectionNode } from './types';

function envelope(over: Partial<MemoryProjection> = {}): MemoryProjection {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'self' },
    view: 'overview',
    projection_version: 'memory-projection-v1',
    generated_at: '2026-08-14T00:00:00Z',
    criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    center: {
      kind: 'continuity_hub',
      projection_id: 'hub:continuity:s:steins_gate:self',
      label_primary: 'AMADEUS',
      label_secondary: 'SOUL',
    },
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
      {
        kind: 'continuity_hub',
        projection_id: 'hub:continuity:s:steins_gate:self',
        label_primary: 'AMADEUS',
        label_secondary: 'SOUL',
      },
      {
        kind: 'topic',
        projection_id: 'topic:t1',
        topic_id: 't1',
        label: '咖啡',
        fact_count: 1,
        experience_count: 0,
      },
      {
        kind: 'fact',
        projection_id: 'fact:1',
        fact_id: '1',
        label: '喜欢黑咖啡',
        is_pinned: false,
        updated_at: '2026-08-14T00:00:00Z',
        topic_id: 't1',
      },
    ],
    edges: [
      { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:self', to: 'topic:t1' },
      { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
    ],
    ...over,
  };
}

function dist(a: { x: number; y: number; z: number }, b = { x: 0, y: 0, z: 0 }) {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  const dz = a.z - b.z;
  return Math.hypot(dx, dy, dz);
}

function hubNode(): ProjectionNode {
  return envelope().nodes[0];
}

function topicNodes(count: number): ProjectionNode[] {
  return Array.from({ length: count }, (_, index) => ({
    kind: 'topic' as const,
    projection_id: `topic:n${index}`,
    topic_id: `n${String(index).padStart(2, '0')}`,
    label: `主题${index}`,
    fact_count: 1,
    experience_count: 0,
  }));
}

describe('constellation layout', () => {
  it('does not attach a Fact to a Topic from topic_id without has_topic', () => {
    const display = layoutProjection(
      envelope({
        nodes: [
          envelope().nodes[0],
          envelope().nodes[1],
          {
            kind: 'fact',
            projection_id: 'fact:orphan',
            fact_id: 'orphan',
            label: '无边',
            is_pinned: false,
            updated_at: '2026-08-14T00:00:00Z',
            topic_id: 't1',
          },
        ],
        edges: [
          { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:self', to: 'topic:t1' },
        ],
      }),
    );
    const topic = display.find((n) => n.id === 'topic:t1')!;
    const orphan = display.find((n) => n.id === 'fact:orphan')!;
    expect(dist(orphan.position, topic.position)).toBeGreaterThan(0.9);
  });

  it.each([0, 1, 2, 3, 10, 40])('places %i growing Topics at equal distance from SOUL', (count) => {
    const source = envelope();
    const nodes = layoutProjection(envelope({ nodes: [source.center, ...topicNodes(count)], edges: [] }));
    const topics = nodes.filter((node) => node.kind === 'topic');
    expect(topics).toHaveLength(count);
    for (const node of topics) {
      expect(Math.hypot(node.position.x, node.position.y, node.position.z)).toBeCloseTo(2.1, 6);
    }
  });

  it('places one sphere per node with SOUL at the origin', () => {
    const display = layoutProjection(envelope());
    expect(display).toHaveLength(3);
    expect(new Set(display.map((n) => n.kind))).toEqual(new Set(['continuity_hub', 'topic', 'fact']));
    const soul = display.find((n) => n.kind === 'continuity_hub')!;
    expect(soul.position).toEqual({ x: 0, y: 0, z: 0 });
    expect(display.every((n) => n.radius > 0)).toBe(true);
    expect(display.some((n) => n.kind === 'topic' && n.label === '咖啡')).toBe(true);
  });

  it('keeps topic regions away from SOUL and evidence near its parent', () => {
    const display = layoutProjection(
      envelope({
        nodes: [
          envelope().nodes[0],
          {
            kind: 'topic',
            projection_id: 'topic:t1',
            topic_id: 't1',
            label: '咖啡',
            fact_count: 2,
            experience_count: 0,
          },
          {
            kind: 'topic',
            projection_id: 'topic:t2',
            topic_id: 't2',
            label: '研究',
            fact_count: 8,
            experience_count: 1,
          },
          {
            kind: 'fact',
            projection_id: 'fact:1',
            fact_id: '1',
            label: '喜欢黑咖啡',
            is_pinned: false,
            updated_at: '2026-08-14T00:00:00Z',
            topic_id: 't1',
          },
          {
            kind: 'fact',
            projection_id: 'fact:2',
            fact_id: '2',
            label: '实验记录',
            is_pinned: true,
            updated_at: '2026-08-14T00:00:00Z',
            topic_id: 't2',
          },
        ],
      }),
    );
    const soul = display.find((n) => n.kind === 'continuity_hub')!;
    const topics = display.filter((n) => n.kind === 'topic');
    const coffee = display.find((n) => n.id === 'topic:t1')!;
    const research = display.find((n) => n.id === 'topic:t2')!;
    const coffeeFact = display.find((n) => n.id === 'fact:1')!;
    expect(topics).toHaveLength(2);
    const radii = topics.map((n) => dist(n.position));
    expect(Math.min(...radii)).toBeGreaterThan(1.2);
    expect(dist(coffeeFact.position, coffee.position)).toBeLessThan(0.85);
    expect(dist(coffeeFact.position, coffee.position)).toBeLessThan(dist(coffeeFact.position));
    expect(soul.radius).toBeGreaterThan(research.radius);
    expect(research.radius).toBeGreaterThan(coffee.radius);
    expect(coffee.radius).toBeGreaterThan(coffeeFact.radius);
  });

  it('keeps a volumetric ten-topic entry framed without flattening depth', () => {
    const nodes = layoutProjection(envelope({ nodes: [envelope().nodes[0], ...topicNodes(10)] }));
    const { yaw, pitch, distance } = OVERVIEW_REST_CAMERA;
    const projected = nodes.map(node => {
      const point = projectToScreen(node.position, 1100, 760, yaw, pitch, distance, { x: 0, y: 0, z: 0 });
      return { ...point, radius: node.radius * overviewFocalPx(760) / point.depth };
    });
    for (let i = 0; i < projected.length; i++) {
      const a = projected[i];
      expect(a.x - a.radius).toBeGreaterThan(30);
      expect(a.x + a.radius).toBeLessThan(1070);
      expect(a.y - a.radius).toBeGreaterThan(76);
      expect(a.y + a.radius).toBeLessThan(720);
    }
    const depths = projected.map(point => point.depth);
    expect(Math.max(...depths) - Math.min(...depths)).toBeGreaterThan(2.8);
  });

  it('builds one filament per typed projection edge and a belt through every topic', () => {
    const projection = envelope({
      nodes: [
        envelope().nodes[0],
        {
          kind: 'topic',
          projection_id: 'topic:t1',
          topic_id: 't1',
          label: '咖啡',
          fact_count: 1,
          experience_count: 0,
        },
        {
          kind: 'topic',
          projection_id: 'topic:t2',
          topic_id: 't2',
          label: '研究',
          fact_count: 1,
          experience_count: 0,
        },
        {
          kind: 'fact',
          projection_id: 'fact:1',
          fact_id: '1',
          label: '喜欢黑咖啡',
          is_pinned: false,
          updated_at: '2026-08-14T00:00:00Z',
          topic_id: 't1',
        },
      ],
      edges: [
        { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:self', to: 'topic:t1' },
        { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:self', to: 'topic:t2' },
        { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
      ],
    });
    const display = layoutProjection(projection);
    const filaments = semanticFilaments(display, projection.edges);
    expect(filaments.map((item) => item.kind).sort()).toEqual(['has_topic', 'hub_to_anchor', 'hub_to_anchor']);
    const membership = filaments.filter((item) => item.kind === 'hub_to_anchor');
    const children = filaments.filter((item) => item.kind === 'has_topic');
    expect(membership).toHaveLength(2);
    expect(membership.every((item) => item.points.length === 2)).toBe(true);
    expect(
      membership.every((item) => {
        const [start, end] = item.points;
        const mid = {
          x: (start.x + end.x) / 2,
          y: (start.y + end.y) / 2,
          z: (start.z + end.z) / 2,
        };
        const chord = Math.hypot(end.x - start.x, end.y - start.y, end.z - start.z);
        const viaMid = Math.hypot(mid.x - start.x, mid.y - start.y, mid.z - start.z)
          + Math.hypot(end.x - mid.x, end.y - mid.y, end.z - mid.z);
        return Math.abs(viaMid - chord) < 0.01;
      }),
    ).toBe(true);
    expect(children.every((item) => item.points.length > 8)).toBe(true);
    expect(filaments.some((item) => item.kind === 'has_topic' && item.from === 'fact:1' && item.to === 'topic:t1')).toBe(true);
    const topics = display.filter((node) => node.kind === 'topic');
    expect(topics).toHaveLength(2);
  });

  it('spreads ten topics around SOUL with designed height and depth', () => {
    const display = layoutProjection(
      envelope({
        nodes: [hubNode(), ...topicNodes(10)],
        edges: topicNodes(10).map((node) => ({
          kind: 'hub_to_anchor' as const,
          from: 'hub:continuity:s:steins_gate:self',
          to: node.projection_id,
        })),
      }),
    );
    const topics = display.filter((node) => node.kind === 'topic');
    expect(topics).toHaveLength(10);
    const ys = topics.map((node) => node.position.y);
    const zs = topics.map((node) => node.position.z);
    expect(Math.max(...ys) - Math.min(...ys)).toBeGreaterThanOrEqual(2);
    expect(Math.max(...ys)).toBeGreaterThan(0.6);
    expect(Math.min(...ys)).toBeLessThan(-0.6);
    expect(Math.max(...zs)).toBeGreaterThan(0.6);
    expect(Math.min(...zs)).toBeLessThan(-0.6);
    for (let i = 0; i < topics.length; i += 1) {
      for (let j = i + 1; j < topics.length; j += 1) {
        expect(dist(topics[i].position, topics[j].position)).toBeGreaterThan(topics[i].radius + topics[j].radius + 0.25);
      }
    }
  });

  it('keeps one or two topics off the origin', () => {
    const one = layoutProjection(envelope({ nodes: [hubNode(), ...topicNodes(1)], edges: [] }));
    const two = layoutProjection(envelope({ nodes: [hubNode(), ...topicNodes(2)], edges: [] }));
    expect(dist(one.find((node) => node.kind === 'topic')!.position)).toBeGreaterThan(1.8);
    const pair = two.filter((node) => node.kind === 'topic');
    expect(dist(pair[0].position)).toBeGreaterThan(1.2);
    expect(dist(pair[1].position)).toBeGreaterThan(1.2);
    expect(dist(pair[0].position, pair[1].position)).toBeGreaterThan(2);
  });

  it('keeps a SOUL-only lockup', () => {
    expect(soulLockupLines('AMADEUS', 'SOUL')).toEqual(['SOUL']);
  });

  it('uses a DOM reading surface for narrow / list / reduced-motion', () => {
    expect(readingSurfaceMode({ width: 1904, preferList: false, reducedMotion: false })).toBe('scene');
    expect(readingSurfaceMode({ width: 800, preferList: false, reducedMotion: false })).toBe('dom');
    expect(readingSurfaceMode({ width: 1904, preferList: true, reducedMotion: false })).toBe('dom');
    expect(readingSurfaceMode({ width: 1904, preferList: false, reducedMotion: true })).toBe('dom');
  });
});
