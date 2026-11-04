import { PerspectiveCamera } from 'three';
import { describe, expect, it } from 'vitest';

import { plateGeometry } from '../chartCoordinates';
import { CELL_REACH, SOUL_REACH, STAR_REACH, cellSegments, drawnStarIds, pickAt, segmentDistance } from './coneHit';
import { coneFrame, homePose, placeCamera, projector, type Viewport } from './coneFrame';
import { modelOf } from './coneFixtures';

const plate = plateGeometry(1440, 840);
const vp: Viewport = { w: 1440, h: 840, cx: plate.cx, cy: plate.cy, R: plate.radius };

function screenOf(model: ReturnType<typeof modelOf>) {
  const frame = coneFrame(model);
  const pose = homePose(frame);
  const camera = new PerspectiveCamera();
  placeCamera(camera, pose, vp);
  return { frame, project: projector(camera, vp, pose.flat) };
}

describe('which records can be hit', () => {
  const model = modelOf(2000, { resident: (i) => i % 40 === 0, lastOpen: Date.parse('2026-10-25T00:00:00Z') });
  const singles = model.stars.filter((s) => s.single).map((s) => s.id);

  it('a collapsed chart offers only the stars it draws: the single ones, not 2000', () => {
    const ids = drawnStarIds(model, { openGroup: null, marks: [], selectedId: null });
    expect(ids.sort()).toEqual([...singles].sort());
    expect(ids.length).toBeLessThan(model.stars.length / 10);
  });

  it('adds the open group, this turn\'s references and the selected record, and nothing else', () => {
    const group = model.groups.find((g) => g.isTopic && g.starIds.length > 50)!;
    const tick = model.stars.find((s) => !s.single && s.group !== group.id)!;
    const other = model.stars.find((s) => !s.single && s.group !== group.id && s.id !== tick.id)!;
    const ids = new Set(drawnStarIds(model, { openGroup: group.id, marks: [tick.id], selectedId: other.id }));
    for (const id of group.starIds) expect(ids.has(id)).toBe(true);
    expect(ids.has(tick.id)).toBe(true);
    expect(ids.has(other.id)).toBe(true);
    expect(ids.size).toBe(new Set([...singles, ...group.starIds, tick.id, other.id]).size);
  });

  it('a chart that is not collapsed offers every star', () => {
    const small = modelOf(25);
    expect(drawnStarIds(small, { openGroup: null, marks: [], selectedId: null })).toHaveLength(25);
  });

  it('offers one segment per ribbon cell (about 500 for 2000), and leaves out the open group\'s', () => {
    const { frame, project } = screenOf(model);
    const cells = [...model.cells.values()].filter((c) => c.collapsedIds.length);
    const all = cellSegments(model, frame, project, null);
    expect(all).toHaveLength(cells.length);
    expect(all.length).toBeLessThan(700);
    const group = model.groups.find((g) => g.isTopic && g.cells.length)!;
    const without = cellSegments(model, frame, project, group.id);
    expect(without.some((s) => s.group === group.id)).toBe(false);
    expect(without).toHaveLength(all.length - group.cells.filter((c) => c.collapsedIds.length).length);
    expect(cellSegments(modelOf(25), coneFrame(modelOf(25)), project, null)).toEqual([]);
  });

  it('hovering a ribbon finds that very cell, and a point away from every ribbon finds nothing', () => {
    const { frame, project } = screenOf(model);
    const segs = cellSegments(model, frame, project, null);
    const probe = segs.filter((s) => Math.hypot(s.bx - s.ax, s.by - s.ay) > 14).slice(0, 40);
    expect(probe.length).toBeGreaterThan(10);
    let found = 0;
    for (const s of probe) {
      const mx = (s.ax + s.bx) / 2;
      const my = (s.ay + s.by) / 2;
      const hit = pickAt(mx, my, { stars: [], cells: segs, soul: null });
      // another ribbon may cross the very same pixel; the one under the pointer is within reach either way
      expect(hit?.kind).toBe('cell');
      if (hit?.kind === 'cell' && hit.key === s.key) found += 1;
    }
    expect(found / probe.length).toBeGreaterThan(0.85);
    expect(pickAt(5, 5, { stars: [], cells: segs, soul: null })).toBeNull();
  });
});

describe('picking', () => {
  const segs = [{ key: 'a|2026-01', group: 'a', ax: 100, ay: 100, bx: 200, by: 100 }];
  it('takes the nearest star within reach, before a ribbon', () => {
    const stars = [{ id: 's1', x: 150, y: 104 }, { id: 's2', x: 156, y: 106 }];
    expect(pickAt(150, 100, { stars, cells: segs, soul: null })).toEqual({ kind: 'star', id: 's1' });
    expect(pickAt(150, 100 + STAR_REACH + 1, { stars: [], cells: segs, soul: null })).toBeNull();
  });
  it('then a ribbon within 9 px, then SOUL within 18 px', () => {
    expect(pickAt(150, 100 + CELL_REACH - 1, { stars: [], cells: segs, soul: null })).toEqual({ kind: 'cell', key: 'a|2026-01', group: 'a' });
    expect(pickAt(150, 100 + CELL_REACH + 1, { stars: [], cells: segs, soul: null })).toBeNull();
    expect(pickAt(400, 400, { stars: [], cells: segs, soul: { x: 400 + SOUL_REACH - 1, y: 400 } })).toEqual({ kind: 'soul' });
    expect(pickAt(400, 400, { stars: [], cells: segs, soul: { x: 400 + SOUL_REACH + 1, y: 400 } })).toBeNull();
  });
  it('measures the distance to a segment, not to its line', () => {
    expect(segmentDistance(50, 100, segs[0])).toBe(50);
    expect(segmentDistance(150, 110, segs[0])).toBe(10);
  });
});
