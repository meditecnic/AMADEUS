import { describe, expect, it } from 'vitest';

import { memorySpaceShortcut } from './chrome';
import {
  nodeWeight,
  emphasisMode,
  neighborhoodIds,
  searchHitIds,
} from './emphasis';
import {
  evidenceParentId,
  hasSpokeWheel,
  semanticFilaments,
  openTopicBranch,
  soulLockupLines,
  soulMarkOffset,
  SOUL_MARK_LIFT,
  branchReveal,
  filamentPrefix,
  visibleDisplayNodes,
  visibleOverviewPaths,
} from './graphPaths';
import { layoutProjection } from './layout';
import { OrreryRuntime } from './orreryRenderer';
import { worldlinePalette } from './palette';
import type { DisplayNode, MemoryProjection, Point3, ProjectionEdge } from './types';

function envelope(over: Partial<MemoryProjection> = {}): MemoryProjection {
  return {
    scope: { session_id: 's', worldline: 'steins_gate', identity_mode: 'okabe' },
    view: 'overview',
    projection_version: 'memory-projection-v1',
    generated_at: '2026-08-14T00:00:00Z',
    criteria: { query: null, kinds: [], topic_id: null, pinned_only: false, updated_from: null, updated_to: null },
    center: {
      kind: 'continuity_hub',
      projection_id: 'hub:continuity:s:steins_gate:okabe',
      label_primary: 'AMADEUS',
      label_secondary: 'SOUL',
    },
    composition: {
      active_facts: 2,
      active_experiences: 2,
      eligible_topics: 3,
      latest_memory_change_at: '2026-08-14T00:00:00Z',
      person_anchors_supported: false,
    },
    budgets: { nodes: 64, edges: 128, hard_max_nodes: 160, hard_max_edges: 320 },
    eligible: { nodes: 8, edges: 7, records: 4, results: 4 },
    shown: { nodes: 8, edges: 7, records: 4, results: 4 },
    truncated: { nodes: false, edges: false, records: false, results: false },
    empty: false,
    result_ids: ['fact:1', 'fact:2', 'exp:1', 'exp:2'],
    nodes: [
      {
        kind: 'continuity_hub',
        projection_id: 'hub:continuity:s:steins_gate:okabe',
        label_primary: 'AMADEUS',
        label_secondary: 'SOUL',
      },
      { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 1, experience_count: 1 },
      { kind: 'topic', projection_id: 'topic:t2', topic_id: 't2', label: '研究', fact_count: 1, experience_count: 0 },
      { kind: 'topic', projection_id: 'topic:t3', topic_id: 't3', label: '日常', fact_count: 0, experience_count: 0 },
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
      {
        kind: 'experience',
        projection_id: 'exp:1',
        experience_id: 'e1',
        label: '第一次通话',
        is_pinned: false,
        created_at: '2026-08-14T00:00:00Z',
        updated_at: '2026-08-14T00:00:00Z',
        expires_at: null,
        is_expired: false,
        conversation_id: 'c1',
      },
      {
        kind: 'experience',
        projection_id: 'exp:2',
        experience_id: 'e2',
        label: '未归类片段',
        is_pinned: false,
        created_at: '2026-08-14T00:00:00Z',
        updated_at: '2026-08-14T00:00:00Z',
        expires_at: null,
        is_expired: false,
        conversation_id: 'c2',
      },
    ],
    edges: [
      { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:okabe', to: 'topic:t1' },
      { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:okabe', to: 'topic:t2' },
      { kind: 'hub_to_anchor', from: 'hub:continuity:s:steins_gate:okabe', to: 'topic:t3' },
      { kind: 'has_topic', from: 'fact:1', to: 'topic:t1' },
      { kind: 'has_topic', from: 'fact:2', to: 'topic:t2' },
      { kind: 'has_topic', from: 'exp:1', to: 'topic:t1' },
      { kind: 'hub_to_evidence', from: 'hub:continuity:s:steins_gate:okabe', to: 'exp:2' },
    ],
    ...over,
  };
}

function dist(a: Point3, b: Point3 = { x: 0, y: 0, z: 0 }): number {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}

function rgb(hex: number): { r: number; g: number; b: number } {
  return { r: (hex >> 16) & 255, g: (hex >> 8) & 255, b: hex & 255 };
}

function restPaths(display: DisplayNode[], edges: readonly ProjectionEdge[]) {
  return visibleOverviewPaths({
    display,
    edges,
    query: '',
    hoverId: null,
    selectedId: null,
    expanded: true,
  });
}

describe('R0 overview reconstruction', () => {
  it('keeps a SOUL-only lockup', () => {
    expect(soulLockupLines('AMADEUS', 'SOUL')).toEqual(['SOUL']);
    expect(soulLockupLines('  amadeus  ', 'soul')).toEqual(['SOUL']);
  });

  it('default Overview shows SOUL and Topics only; evidence waits for expand', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    const rest = visibleDisplayNodes(display, projection.edges, {
      overviewOpen: true,
      expandedTopicIds: [],
      query: '',
      resultIds: projection.result_ids,
    });
    expect(rest.map((node) => node.kind).sort()).toEqual([
      'continuity_hub',
      'topic',
      'topic',
      'topic',
    ]);
    expect(rest.some((node) => node.kind === 'fact' || node.kind === 'experience')).toBe(false);
    const opened = visibleDisplayNodes(display, projection.edges, {
      overviewOpen: true,
      expandedTopicIds: ['topic:t1'],
      query: '',
    });
    expect(opened.some((node) => node.id === 'fact:1')).toBe(true);
    expect(opened.some((node) => node.id === 'exp:1')).toBe(true);
    expect(opened.some((node) => node.id === 'exp:2')).toBe(false);
    expect(opened.some((node) => node.id === 'fact:2')).toBe(false);
    expect(openTopicBranch('topic:t1', [])).toEqual(['topic:t1']);
    expect(openTopicBranch('topic:t1', ['topic:t1'])).toEqual(['topic:t1']);
    expect(openTopicBranch('topic:t2', ['topic:t1'])).toEqual(['topic:t2']);
    const searched = visibleDisplayNodes(display, projection.edges, {
      overviewOpen: true,
      expandedTopicIds: [],
      query: '咖啡',
      resultIds: ['fact:1'],
    });
    expect(searched.some((node) => node.id === 'fact:1')).toBe(true);
    expect(searched.some((node) => node.id === 'exp:2')).toBe(false);
  });

  it('default rest paths keep quiet membership lines and no Topic–Topic belt', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    const paths = restPaths(display, projection.edges);
    const topics = display.filter((node) => node.kind === 'topic');
    expect(topics.length).toBeGreaterThan(2);
    const spokes = paths.filter((path) => path.class === 'typed_relation' && path.kind === 'hub_to_anchor');
    expect(spokes).toHaveLength(topics.length);
    expect(spokes.every((path) => path.points.length === 2)).toBe(true);
    expect(paths.some((path) => path.class === 'field_belt')).toBe(false);
    const syntheticWheel = topics.map((topic) => ({
      class: 'typed_relation' as const,
      kind: 'hub_to_anchor' as const,
      from: 'hub:continuity:s:steins_gate:okabe',
      to: topic.id,
      points: [{ x: 0, y: 0, z: 0 }, topic.position],
    }));
    expect(hasSpokeWheel(syntheticWheel, display)).toBe(true);
  });

  it('emits only typed filaments for currently visible nodes, never belt or spokes', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    const collapsed = visibleDisplayNodes(display, projection.edges, {
      overviewOpen: true,
      expandedTopicIds: [],
    });
    const rest = visibleOverviewPaths({
      display: collapsed,
      edges: projection.edges,
      query: '',
      selectedId: 'topic:t1',
      expanded: true,
    });
    expect(rest.every((path) => path.class === 'typed_relation')).toBe(true);
    expect(rest.some((path) => path.class === 'field_belt')).toBe(false);
    expect(rest.filter((path) => path.kind === 'hub_to_anchor')).toHaveLength(3);
    expect(rest.filter((path) => path.kind === 'has_topic')).toHaveLength(0);

    const opened = visibleDisplayNodes(display, projection.edges, {
      overviewOpen: true,
      expandedTopicIds: ['topic:t1'],
    });
    const focused = visibleOverviewPaths({
      display: opened,
      edges: projection.edges,
      selectedId: 'topic:t1',
      expanded: true,
    });
    expect(focused.some((path) => path.kind === 'has_topic' && path.from === 'fact:1')).toBe(true);
    expect(focused.filter((path) => path.kind === 'hub_to_anchor')).toHaveLength(3);
    expect(focused.some((path) => path.kind === 'hub_to_evidence')).toBe(false);
  });

  it('places has_topic evidence as satellites and keeps hub_to_evidence off Topics', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    const coffee = display.find((node) => node.id === 'topic:t1')!;
    const research = display.find((node) => node.id === 'topic:t2')!;
    const fact = display.find((node) => node.id === 'fact:1')!;
    const lived = display.find((node) => node.id === 'exp:1')!;
    const unbound = display.find((node) => node.id === 'exp:2')!;
    expect(evidenceParentId('fact:1', projection.edges)).toBe('topic:t1');
    expect(evidenceParentId('exp:1', projection.edges)).toBe('topic:t1');
    expect(evidenceParentId('exp:2', projection.edges)).toBeNull();
    expect(dist(fact.position, coffee.position)).toBeLessThan(0.85);
    expect(dist(fact.position, coffee.position)).toBeLessThan(dist(fact.position));
    expect(dist(fact.position, coffee.position)).toBeLessThan(dist(fact.position, research.position));
    expect(dist(lived.position, coffee.position)).toBeLessThan(0.85);
    expect(dist(lived.position, coffee.position)).toBeLessThan(dist(lived.position));
    const opened = visibleDisplayNodes(display, projection.edges, {
      overviewOpen: true,
      expandedTopicIds: ['topic:t1'],
    });
    const paths = visibleOverviewPaths({ display: opened, edges: projection.edges, expanded: true });
    expect(paths.some((path) => path.kind === 'has_topic' && path.to === 'topic:t1')).toBe(true);
    expect(paths.some((path) => path.to === 'exp:2')).toBe(false);
    expect(unbound).toBeTruthy();
    const topicCount = display.filter((node) => node.kind === 'topic').length;
    expect(topicCount).toBe(3);
    const seven = layoutProjection(
      envelope({
        nodes: [
          envelope().nodes[0],
          ...Array.from({ length: 7 }, (_, index) => ({
            kind: 'topic' as const,
            projection_id: `topic:n${index}`,
            topic_id: `n${index}`,
            label: `主题${index}`,
            fact_count: 1,
            experience_count: 0,
          })),
        ],
        edges: Array.from({ length: 7 }, (_, index) => ({
          kind: 'hub_to_anchor' as const,
          from: 'hub:continuity:s:steins_gate:okabe',
          to: `topic:n${index}`,
        })),
      }),
    );
    expect(seven.filter((node) => node.kind === 'topic')).toHaveLength(7);
    expect(hasSpokeWheel(restPaths(seven, []), seven)).toBe(false);
  });

  it('treats a blank query as rest even when leftover result_ids exist', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    expect(emphasisMode({ query: '', resultIds: projection.result_ids, hoverId: null, selectedId: null })).toBe('rest');
    expect(emphasisMode({ query: '   ', resultIds: projection.result_ids, hoverId: null, selectedId: null })).toBe('rest');
    expect(emphasisMode({ query: '咖啡', resultIds: projection.result_ids, hoverId: null, selectedId: null })).toBe('search');
    expect(searchHitIds('', projection.result_ids)).toEqual([]);
    expect(searchHitIds('咖啡', projection.result_ids)).toEqual(projection.result_ids);
    const restHit = nodeWeight('fact:1', {
      query: '',
      resultIds: projection.result_ids,
      hoverId: null,
      selectedId: null,
      edges: projection.edges,
      nodes: display,
    });
    const restOther = nodeWeight('topic:t3', {
      query: '',
      resultIds: projection.result_ids,
      hoverId: null,
      selectedId: null,
      edges: projection.edges,
      nodes: display,
    });
    expect(restHit).toBe(restOther);
    const searchHit = nodeWeight('fact:1', {
      query: '咖啡',
      resultIds: projection.result_ids,
      hoverId: null,
      selectedId: null,
      edges: projection.edges,
      nodes: display,
    });
    const searchMiss = nodeWeight('topic:t3', {
      query: '咖啡',
      resultIds: ['fact:1'],
      hoverId: null,
      selectedId: null,
      edges: projection.edges,
      nodes: display,
    });
    expect(searchHit).toBeGreaterThan(searchMiss);
  });

  it('hover lights only the hovered sphere while selection retains its semantic neighborhood', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    const hood = neighborhoodIds('topic:t1', projection.edges, display);
    expect(hood.has('topic:t1')).toBe(true);
    expect(hood.has('fact:1')).toBe(true);
    expect(hood.has('exp:1')).toBe(true);
    expect(hood.has('topic:t2')).toBe(false);
    expect(hood.has('topic:t3')).toBe(false);
    const input = {
      query: '',
      resultIds: projection.result_ids,
      hoverId: 'topic:t1' as string | null,
      selectedId: null as string | null,
      edges: projection.edges,
      nodes: display,
    };
    const restInput = { ...input, hoverId: null as string | null };
    expect(emphasisMode(input)).toBe('focus');
    expect(nodeWeight('topic:t1', input)).toBeGreaterThan(nodeWeight('topic:t3', input));
    expect(nodeWeight('fact:1', input)).toBe(nodeWeight('fact:2', input));
    expect(nodeWeight('topic:t2', input)).toBe(nodeWeight('topic:t2', restInput));
    expect(nodeWeight('topic:t3', input)).toBe(nodeWeight('topic:t3', restInput));
    const soulHover = {
      ...input,
      hoverId: 'hub:continuity:s:steins_gate:okabe' as string | null,
      selectedId: null as string | null,
    };
    expect(nodeWeight('topic:t1', soulHover)).toBe(nodeWeight('topic:t1', restInput));
    expect(nodeWeight('topic:t2', soulHover)).toBe(nodeWeight('topic:t2', restInput));
    expect(nodeWeight('topic:t3', soulHover)).toBe(nodeWeight('topic:t3', restInput));
    const selectedAndHover = {
      ...input,
      selectedId: 'topic:t1' as string | null,
      hoverId: 'topic:t3' as string | null,
    };
    expect(nodeWeight('topic:t3', selectedAndHover)).toBeGreaterThan(nodeWeight('fact:2', selectedAndHover));
    expect(nodeWeight('topic:t1', selectedAndHover)).toBeGreaterThan(0.7);
    const soulHood = neighborhoodIds('hub:continuity:s:steins_gate:okabe', projection.edges, display);
    expect([...soulHood]).toEqual(['hub:continuity:s:steins_gate:okabe']);
    const soulPaths = visibleOverviewPaths({
      display,
      edges: projection.edges,
      query: '',
      selectedId: 'hub:continuity:s:steins_gate:okabe',
      expanded: true,
    });
    expect(soulPaths.filter((path) => path.kind === 'hub_to_anchor')).toHaveLength(3);
    const filaments = semanticFilaments(display, projection.edges);
    expect(filaments.filter((item) => item.kind === 'hub_to_anchor')).toHaveLength(3);
  });

  it('keeps SG cool and β copper without a rust-red wash', () => {
    const sg = worldlinePalette('steins_gate');
    const beta = worldlinePalette('beta');
    const sgAccent = rgb(sg.accent);
    const betaAccent = rgb(beta.accent);
    const betaVoid = rgb(beta.void);
    expect(sgAccent.b).toBeGreaterThanOrEqual(sgAccent.r);
    expect(betaAccent.r).toBeGreaterThan(betaAccent.b);
    expect(betaVoid.r).toBeLessThan(40);
    expect(betaVoid.r - betaVoid.b).toBeLessThan(20);
    expect(sg.void).not.toBe(beta.void);
    expect(sg.accent).not.toBe(beta.accent);
  });

  it('maps compact chrome shortcuts without stealing input typing', () => {
    expect(memorySpaceShortcut({ key: '/' })).toBe('search');
    expect(memorySpaceShortcut({ key: 'l' })).toBe('list');
    expect(memorySpaceShortcut({ key: '2' })).toBeNull();
    expect(memorySpaceShortcut({ key: '/', target: { tagName: 'INPUT' } })).toBeNull();
    expect(memorySpaceShortcut({ key: 'a' })).toBeNull();
  });

  it('parks the SOUL mark on the camera-facing hemisphere and eases branch reveal', () => {
    const radius = 0.5;
    const offset = soulMarkOffset({ x: 0, y: 0, z: 5 }, radius);
    expect(offset.z).toBeCloseTo(radius + SOUL_MARK_LIFT, 5);
    expect(Math.hypot(offset.x, offset.y, offset.z)).toBeCloseTo(radius + SOUL_MARK_LIFT, 5);
    expect(Math.abs(offset.x)).toBeLessThan(0.01);
    const back = soulMarkOffset({ x: -4, y: 0, z: 0 }, radius);
    expect(back.x).toBeCloseTo(-(radius + SOUL_MARK_LIFT), 5);
    const topic = { x: 2, y: 0, z: 0 };
    const rest = { x: 2.4, y: 0.2, z: 0.1 };
    expect(branchReveal(0, topic, rest)).toEqual(topic);
    expect(branchReveal(1, topic, rest)).toEqual(rest);
    const mid = branchReveal(0.5, topic, rest);
    expect(mid.x).toBeGreaterThan(topic.x);
    expect(mid.x).toBeLessThan(rest.x);
    const line = [
      { x: 0, y: 0, z: 0 },
      { x: 1, y: 0, z: 0 },
      { x: 2, y: 0, z: 0 },
    ];
    expect(filamentPrefix(line, 0)).toHaveLength(1);
    expect(filamentPrefix(line, 1)).toHaveLength(3);
  });

  it('keeps one quiet membership line per Topic through select and SOUL focus', () => {
    const projection = envelope();
    const display = layoutProjection(projection);
    const runtime = new OrreryRuntime(document.createElement('canvas'));
    runtime.setExpanded(true);
    runtime.setGraph(display, projection.edges, projection.result_ids);
    runtime.setSelected(null);
    const hubSpokes = () =>
      runtime.visiblePaths().filter((path) => path.class === 'typed_relation' && path.kind === 'hub_to_anchor');
    const pathOpacity = () => (runtime as unknown as {
      pathLines: Array<{ line: THREE.Line; path: { from?: string; to?: string } }>;
    }).pathLines.map(({ line, path }) => ({
      from: path.from,
      to: path.to,
      opacity: (line.material as THREE.LineBasicMaterial).opacity,
    }));

    expect(hubSpokes()).toHaveLength(3);
    expect(pathOpacity().every((path) => path.opacity <= 0.055)).toBe(true);
    runtime.setSelected('topic:t1');
    expect(hubSpokes()).toHaveLength(3);
    const focusedPaths = pathOpacity();
    const focusTether = focusedPaths.find((path) => path.from === 'hub:continuity:s:steins_gate:okabe'
      && path.to === 'topic:t1')?.opacity ?? 0;
    expect(focusTether).toBeGreaterThanOrEqual(0.14);
    expect(focusTether).toBeLessThanOrEqual(0.2);
    expect(focusedPaths.filter((path) => path.to !== 'topic:t1').every((path) => path.opacity <= 0.035)).toBe(true);

    runtime.setGraph(display, projection.edges, projection.result_ids);
    runtime.setSelected(null);
    expect(hubSpokes()).toHaveLength(3);

    runtime.setSelected('hub:continuity:s:steins_gate:okabe');
    expect(hubSpokes()).toHaveLength(3);

    runtime.setSelected(null);
    runtime.setHover('topic:t3');
    expect(hubSpokes()).toHaveLength(3);
    runtime.setHover(null);
    expect(hubSpokes()).toHaveLength(3);
    runtime.dispose();
  });
});
