import { describe, expect, it } from 'vitest';

import { platePoint, plateGeometry } from '../chartCoordinates';
import { chartKindOf, buildLayers, packSegs, starInstances, strokeSegments, annotSegments } from './coneBuild';
import { PLATE_POSE, coneFrame, homePose, projectAt, type Viewport } from './coneFrame';
import { modelOf } from './coneFixtures';
import { CONE_THEMES } from './coneTheme';

const plate = plateGeometry(1440, 840);
const vp: Viewport = { w: 1440, h: 840, cx: plate.cx, cy: plate.cy, R: plate.radius };

describe('cone frame', () => {
  it.each([25, 2000])('looked at from straight above, %i stars land where the plate puts them', (count) => {
    const model = modelOf(count);
    const frame = coneFrame(model);
    let worst = 0;
    for (const star of model.stars) {
      const flat = platePoint(star.theta, star.phi, plate.radius, model.thetaFit);
      const seen = projectAt(PLATE_POSE, vp, frame.pos(star.theta, star.phi));
      worst = Math.max(worst, Math.hypot(seen.x - (plate.cx + flat.x), seen.y - (plate.cy + flat.y)));
    }
    expect(model.stars).toHaveLength(count);
    expect(worst).toBeLessThan(0.5);
  });

  it('the plate pose is flat; the overview pose is not', () => {
    const frame = coneFrame(modelOf(25));
    expect(PLATE_POSE.flat).toBe(1);
    expect(homePose(frame).flat).toBe(0);
    expect(frame.y(frame.thToday)).toBeCloseTo(0);
    expect(frame.y(0)).toBeCloseTo(-frame.hc);
    expect(frame.r(frame.thToday)).toBeCloseTo(frame.rToday);
  });

  it('seen from the side the oldest record is the lowest', () => {
    const model = modelOf(25);
    const frame = coneFrame(model);
    const [first, last] = [model.stars[0], model.stars[model.stars.length - 1]];
    expect(frame.pos(first.theta, first.phi)[1]).toBeLessThan(frame.pos(last.theta, last.phi)[1]);
  });
});

describe('collapsed ticks', () => {
  const model = modelOf(2000, { resident: (i) => i % 40 === 0, lastOpen: Date.parse('2026-10-25T00:00:00Z') });
  const frame = coneFrame(model);
  const theme = CONE_THEMES.steins_gate;

  it('draws one instanced segment per collapsed record, each tied to its star', () => {
    expect(model.collapsed).toBe(true);
    const segs = strokeSegments(model, frame, theme, plate.radius);
    const expected: string[] = [];
    for (const group of model.groups) for (const cell of group.cells) expected.push(...cell.collapsedIds);
    const total = [...model.cells.values()].reduce((sum, cell) => sum + cell.collapsedIds.length, 0);
    expect(segs).toHaveLength(total);
    expect(segs).toHaveLength(expected.length);
    expect(total).toBeLessThan(model.stars.length);
    segs.forEach((seg, i) => {
      const star = model.byId.get(expected[i])!;
      expect(star.single).toBe(false);
      expect(seg.x).toEqual(frame.pos(star.theta, star.phi));
      expect(seg.stroke).toBe(true);
    });
  });

  it('packs them into the layer the renderer draws as one extra call', () => {
    const layers = buildLayers(model, frame, theme, chartKindOf(model), plate.radius);
    const total = [...model.cells.values()].reduce((sum, cell) => sum + cell.collapsedIds.length, 0);
    expect(layers.strokes).toHaveLength(total);
    const packed = packSegs(layers.strokes);
    expect(packed.count).toBe(total);
    // flags: bit 4 marks a tick, bit 2 asks for the hidden-line test
    for (let i = 0; i < packed.count; i += 1) expect(packed.iG[i * 2 + 1] & 6).toBe(6);
  });

  it('keeps the single stars out of the tick layer', () => {
    const singles = model.stars.filter((star) => star.single).length;
    expect(singles).toBeGreaterThan(0);
    const ticks = strokeSegments(model, frame, theme, plate.radius).length;
    expect(ticks + singles).toBe(model.stars.length);
  });

  it('a chart that is not collapsed has no ticks', () => {
    const small = modelOf(25);
    expect(strokeSegments(small, coneFrame(small), theme, plate.radius)).toEqual([]);
  });

  it('marks the star instances that wait for their group to open', () => {
    const stars = starInstances(model, frame);
    expect(stars.ids).toHaveLength(2000);
    model.stars.forEach((star, i) => expect(stars.k[i]).toBe(star.single ? 0 : 1));
  });
});

describe('layers', () => {
  it('a young chart draws the disc and the axis but no topic lines', () => {
    const young = modelOf(4, { topics: 1 });
    const kind = chartKindOf(young);
    expect(['new', 'normal']).toContain(kind);
    const layers = buildLayers(young, coneFrame(young), CONE_THEMES.beta, 'new', plate.radius);
    expect(layers.world.filter((seg) => seg.hid && seg.stroke)).toEqual([]);
    expect(layers.struct.length).toBeGreaterThan(0);
  });

  it('measures the selected star down to the disc and across to the axis', () => {
    const model = modelOf(25);
    const frame = coneFrame(model);
    const star = model.stars[10];
    const segs = annotSegments(model, frame, CONE_THEMES.steins_gate, { selectedId: star.id, soul: false, monthKey: null, marks: [], state: 'normal' });
    const p = frame.pos(star.theta, star.phi);
    expect(segs.some((s) => s.a[1] === p[1] && s.b[1] === 0)).toBe(true);
    expect(segs.some((s) => s.b[0] === 0 && s.b[2] === 0 && s.b[1] === p[1])).toBe(true);
  });

  it('draws this turn\'s references from the middle of the disc, and only for stars that exist', () => {
    const model = modelOf(25);
    const frame = coneFrame(model);
    const segs = annotSegments(model, frame, CONE_THEMES.steins_gate, { selectedId: null, soul: false, monthKey: null, marks: [model.stars[3].id, 'gone'], state: 'normal' });
    expect(segs.length).toBeGreaterThan(0);
    expect(segs.every((s) => s.a[0] === 0 && s.a[2] === 0 || s.a[1] === 0)).toBe(true);
  });
});
