import { describe, expect, it } from 'vitest';

import { cognitionKind, cognitionOptics, topicChildCount } from './cognitionMaterials';
import type { DisplayNode } from './types';

describe('production cognition materials', () => {
  it('does not encode Topic as three fake clusters', () => {
    const topic = cognitionOptics('steins_gate', 'topic', 1, 4);
    const empty = cognitionOptics('steins_gate', 'topic', 1, 0);
    expect(topic.flowStrands).toBeGreaterThan(empty.flowStrands);
    expect(topic.flowStrands - empty.flowStrands).toBeLessThan(4 * 20);
    expect(topic.flowStrands).toBeLessThan(160);
    expect(topicChildCount({
      id: 'topic:t1',
      kind: 'topic',
      label: '咖啡',
      radius: 0.2,
      position: { x: 0, y: 0, z: 0 },
      node: { kind: 'topic', projection_id: 'topic:t1', topic_id: 't1', label: '咖啡', fact_count: 2, experience_count: 1 },
    } satisfies DisplayNode)).toBe(3);
  });

  it('maps real node kinds without a look-dev record alias for Topics', () => {
    expect(cognitionKind('continuity_hub')).toBe('soul');
    expect(cognitionKind('topic')).toBe('topic');
    expect(cognitionKind('fact')).toBe('fact');
    expect(cognitionKind('experience')).toBe('experience');
  });

  it('SG vs β differ in rhythm and plate structure, not only hue', () => {
    const sg = cognitionOptics('steins_gate', 'soul', 1);
    const beta = cognitionOptics('beta', 'soul', 1);
    expect(sg.flowJitter).toBeLessThan(beta.flowJitter);
    expect(sg.plateRegularity).toBeGreaterThan(beta.plateRegularity);
    expect(sg.fissure).not.toBe(beta.fissure);
    expect(sg.flowSpeed).not.toBe(beta.flowSpeed);
  });

  it('keeps Fact and Experience as the same family with mild rhythm difference', () => {
    const fact = cognitionOptics('steins_gate', 'fact', 1);
    const exp = cognitionOptics('steins_gate', 'experience', 1);
    expect(fact.minAlpha).toBe(exp.minAlpha);
    expect(exp.flowSpeed).not.toBe(fact.flowSpeed);
  });
});
