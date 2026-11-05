import { PerspectiveCamera } from 'three';
import { describe, expect, it, vi } from 'vitest';

import { plateGeometry } from '../chartCoordinates';
import { MONTH_GAP, NAME_SHIFTS, boxesTouch, ConeLabelLayer, layoutConeLabels } from './coneLabels';
import { coneFrame, homePose, placeCamera, projector, type Viewport } from './coneFrame';
import { modelOf } from './coneFixtures';

const plate = plateGeometry(1440, 840);
const vp: Viewport = { w: 1440, h: 840, cx: plate.cx, cy: plate.cy, R: plate.radius };

function layout(model: ReturnType<typeof modelOf>, focusTopic: string | null = null) {
  const frame = coneFrame(model);
  const pose = homePose(frame);
  const camera = new PerspectiveCamera();
  placeCamera(camera, pose, vp);
  return layoutConeLabels({ model, frame, project: projector(camera, vp, pose.flat), focusTopic });
}

describe('words on the cone (DIRECTION 22.3, in 3D)', () => {
  const model = modelOf(2000, { topics: 30, lastOpen: Date.parse('2026-10-20T00:00:00Z') });

  it('slides a name round the disc by 3, 6 or 9 degrees before it gives up', () => {
    expect(NAME_SHIFTS).toEqual([0, 3, -3, 6, -6, 9, -9]);
  });

  it('never lets two placed words touch', () => {
    const out = layout(model);
    for (let i = 0; i < out.boxes.length; i += 1) {
      for (let j = i + 1; j < out.boxes.length; j += 1) expect(boxesTouch(out.boxes[i], out.boxes[j])).toBe(false);
    }
  });

  it('turns the names that do not fit into dots, and every group is named one way or the other', () => {
    const out = layout(model);
    const groups = model.groups.filter((g) => g.starIds.length);
    expect(out.topics).toHaveLength(groups.length);
    expect(out.topics.some((t) => t.dot)).toBe(true);
    expect(out.topics.some((t) => !t.dot)).toBe(true);
    for (const t of out.topics) expect(t.count).toBe(groups.find((g) => g.id === t.id)!.starIds.length);
  });

  it('writes the focused group\'s name first, so it is never the one turned into a dot', () => {
    const crowded = layout(model).topics.find((t) => t.dot)!;
    expect(layout(model, crowded.id).topics.find((t) => t.id === crowded.id)!.dot).toBe(false);
  });

  it('puts month numbers on rows at least 10.5 px apart, years and quarters before months', () => {
    const out = layout(model);
    const rows = out.months.filter((m) => m.shown).map((m) => m.y).sort((a, b) => a - b);
    expect(rows.length).toBeGreaterThan(3);
    for (let i = 1; i < rows.length; i += 1) expect(rows[i] - rows[i - 1]).toBeGreaterThanOrEqual(MONTH_GAP);
    const ringKind = new Map(model.rings.map((r) => [r.key, r.kind]));
    const shownKinds = out.months.filter((m) => m.shown).map((m) => ringKind.get(m.key));
    expect(shownKinds).toContain('year');
    // a plain month is only shown when every year in view is
    if (shownKinds.includes('month')) {
      const years = model.rings.filter((r) => r.kind === 'year' && !r.nearToday).map((r) => r.key);
      for (const key of years) expect(out.months.find((m) => m.key === key)!.shown).toBe(true);
    }
  });

  it('writes one sentence per group for what is new, in the form 新 n · MM/DD', () => {
    const out = layout(model);
    const fresh = new Set(model.stars.filter((s) => s.isNew).map((s) => s.group));
    expect(out.news.length).toBeGreaterThan(0);
    expect(out.news.length).toBeLessThanOrEqual(fresh.size);
    for (const note of out.news) expect(note.text).toMatch(/^新( \d+)? · \d\d\/\d\d$/);
    expect(new Set(out.news.map((n) => model.byId.get(n.id)!.group)).size).toBe(out.news.length);
  });

  it('says 新 · MM/DD when a group has only one new record', () => {
    const small = modelOf(25, { lastOpen: Date.parse('2026-10-31T00:00:00Z') });
    const out = layout(small);
    const one = out.news.find((n) => small.stars.filter((s) => s.isNew && s.group === small.byId.get(n.id)!.group).length === 1);
    if (one) expect(one.text).toMatch(/^新 · \d\d\/\d\d$/);
  });

  it('keeps TODAY and SOUL where the cone says they are', () => {
    const out = layout(model);
    expect(out.today.text).toMatch(/^今天 \d\d\/\d\d$/);
    expect(out.soul.y).toBeGreaterThan(out.today.y);
  });
});

describe('label layer', () => {
  it('makes one clickable name, one dot and one month number per ring, and routes the clicks', () => {
    const model = modelOf(2000, { topics: 30 });
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const handlers = { onTopic: vi.fn(), onMonth: vi.fn(), onSoul: vi.fn() };
    const layer = new ConeLabelLayer(svg, handlers);
    layer.build(model, 'full');
    const groups = model.groups.filter((g) => g.starIds.length);
    expect(svg.querySelectorAll('.c3-tlab')).toHaveLength(groups.length);
    expect(svg.querySelectorAll('.c3-tmark')).toHaveLength(groups.length);
    expect(svg.querySelectorAll('.c3-mlab')).toHaveLength(model.rings.filter((r) => !r.nearToday).length);
    const out = layout(model);
    layer.update(out, { focusTopic: groups[0].id, month: null, soul: false });
    const first = svg.querySelector<SVGGElement>(`.c3-tlab[data-topic="${groups[0].id}"]`)!;
    expect(first).toHaveClass('on');
    first.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(handlers.onTopic).toHaveBeenCalledWith(groups[0].id);
    svg.querySelector<SVGTextElement>('.c3-mlab')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(handlers.onMonth).toHaveBeenCalledTimes(1);
    svg.querySelector<SVGTextElement>('.c3-soul')!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(handlers.onSoul).toHaveBeenCalled();
    // a name shows or its dot does, never both
    for (const label of out.topics) {
      const nodes = svg.querySelector<SVGGElement>(`.c3-tlab[data-topic="${label.id}"]`)!;
      expect(nodes.style.display === 'none').toBe(label.dot);
    }
  });

  it('shows no topics or months for a young chart, only TODAY and SOUL', () => {
    const model = modelOf(4, { topics: 1 });
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const layer = new ConeLabelLayer(svg, { onTopic: vi.fn(), onMonth: vi.fn(), onSoul: vi.fn() });
    layer.build(model, 'basic');
    expect(svg.querySelectorAll('.c3-tlab, .c3-mlab')).toHaveLength(0);
    expect(svg.querySelector('.c3-soul')).not.toHaveStyle({ display: 'none' });
    layer.build(model, 'none');
    expect(svg.querySelector<SVGTextElement>('.c3-soul')!.style.display).toBe('none');
  });
});
