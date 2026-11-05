import { describe, expect, it } from 'vitest';

import { entranceAt, entranceLength, worldRevealEnd } from './coneEntrance';
import { PLATE_POSE, coneFrame, homePose, rad } from './coneFrame';
import { modelOf } from './coneFixtures';

const target = homePose(coneFrame(modelOf(25)), rad(24));

describe('the opening shot (DIRECTION 15.5)', () => {
  it('starts as the plate: straight above, long lens, nothing drawn yet', () => {
    const f = entranceAt(0, target);
    expect(f.pose).toMatchObject({ flat: 1, el: PLATE_POSE.el, fov: PLATE_POSE.fov, yaw: 0 });
    expect(f.struct).toBe(0);
    expect(f.stars).toBe(0);
    expect(f.labels).toBe(0);
  });

  it('draws the rings and stars on the plate before the cone moves (first 650 ms)', () => {
    const f = entranceAt(650, target);
    expect(f.pose.flat).toBe(1);
    expect(f.pose.el).toBe(PLATE_POSE.el);
    expect(f.struct).toBeGreaterThan(0.7);
    expect(f.stars).toBeGreaterThan(0.4);
  });

  it('sinks into the cone while the camera comes down to 17 degrees and the lens opens from 6 to 34 degrees (650-1900 ms)', () => {
    const mid = entranceAt(1300, target);
    expect(mid.pose.flat).toBeGreaterThan(0.1);
    expect(mid.pose.flat).toBeLessThan(0.9);
    expect(mid.pose.el).toBeLessThan(PLATE_POSE.el);
    expect(mid.pose.fov).toBeGreaterThan(6);
    expect(mid.pose.fov).toBeLessThan(34);
    const end = entranceAt(2100, target);
    for (const key of ['yaw', 'el', 'dist', 'fov', 'tx', 'ty', 'tz', 'flat'] as const) expect(end.pose[key]).toBeCloseTo(target[key], 9);
    expect(end.pose.el).toBeCloseTo(rad(17));
    expect(end.pose.fov).toBe(34);
    expect(end.pose.flat).toBe(0);
  });

  it('draws the connections from 1.5 s, writes the words from 1.7 s, and the "new" crosses from 2.6 s', () => {
    expect(entranceAt(1500, target).world).toBe(0);
    expect(entranceAt(2500, target).world).toBeCloseTo(1);
    expect(entranceAt(1700, target).labels).toBe(0);
    expect(entranceAt(1950, target).labels).toBeCloseTo(0.5);
    expect(entranceAt(2200, target).labels).toBe(1);
    // the first cross begins at 1.2 units of the world layer, i.e. 2.7 s
    expect(entranceAt(2700, target).world).toBeCloseTo(1.2);
  });

  it('is 2.6 s long, longer only for many "new" records, and the crosses are paced to fit', () => {
    expect(entranceLength(worldRevealEnd(0))).toBe(2600);
    expect(entranceLength(worldRevealEnd(1))).toBeGreaterThan(2600);
    expect(worldRevealEnd(100)).toBeLessThan(4.1);
    expect(entranceLength(worldRevealEnd(100))).toBeLessThan(6000);
  });
});
