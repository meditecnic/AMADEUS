// Words on the cone: topic names, month numbers, TODAY, SOUL and the "new" notes. They are SVG text over the
// canvas, laid out once per drawn frame by projecting anchors on the cone (DIRECTION 22.3, "in 3D"): a name
// that would touch another slides round the disc by 3, 6, 9 degrees, then gives way to a dot; a month number
// needs its box clear and 10.5 px of height to itself. Nothing here goes into the model.
import type { ChartModel } from '../chartCoordinates';
import { md } from '../chartFormat';
import { rad, type ConeFrame, type Screen, type Vec3 } from './coneFrame';

export type Box = { x0: number; y0: number; x1: number; y1: number };
export const boxesTouch = (a: Box, b: Box) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

export const NAME_SHIFTS = [0, 3, -3, 6, -6, 9, -9];
export const MONTH_GAP = 10.5;
const TOPIC_CHAR = 17.2;
const PSEUDO_CHAR = 13.6;
const MONTH_RANK = { year: 0, quarter: 1, month: 2 } as const;

export type LabelInput = {
  model: ChartModel;
  frame: ConeFrame;
  project: (p: Vec3) => Screen;
  focusTopic: string | null;
};

export type TopicLabel = {
  id: string; text: string; count: number;
  /** Placed as text: where its anchor is, which side it runs to, and where the count sits. */
  x: number; y: number; right: boolean; countX: number;
  /** The name did not fit: a dot outside the bezel that names itself on hover. */
  dot: boolean;
  opacity: number;
};
export type MonthLabel = { key: string; text: string; x: number; y: number; right: boolean; shown: boolean };
export type NewNote = { id: string; text: string; x: number; y: number; right: boolean };
export type ConeLabels = {
  today: { x: number; y: number; text: string };
  topics: TopicLabel[];
  months: MonthLabel[];
  soul: { x: number; y: number };
  news: NewNote[];
  boxes: Box[];
};

const around = (radius: number, phi: number): Vec3 => [Math.sin(rad(phi)) * radius, 0, Math.cos(rad(phi)) * radius];

export function layoutConeLabels({ model, frame, project, focusTopic }: LabelInput): ConeLabels {
  const placed: Box[] = [];
  const free = (box: Box) => !placed.some((other) => boxesTouch(box, other));
  const centre = project([0, 0, 0]);

  const todayAt = project(around(frame.rToday + 0.026, model.scalePhi));
  const today = { x: todayAt.x, y: todayAt.y + 4, text: `今天 ${md(model.today)}` };
  placed.push({ x0: todayAt.x - 44, x1: todayAt.x + 44, y0: todayAt.y - 6, y1: todayAt.y + 8 });

  const groups = (model.collapsed ? model.groups : model.groups.filter((group) => group.isTopic)).filter((group) => group.starIds.length);
  const ordered = [...groups].sort((a, b) =>
    Number(b.id === focusTopic) - Number(a.id === focusTopic)
    || Number(b.isTopic) - Number(a.isTopic)
    || b.starIds.length - a.starIds.length);
  const topics: TopicLabel[] = ordered.map((group) => {
    const width = group.label.length * (group.isTopic ? TOPIC_CHAR : PSEUDO_CHAR);
    const countWidth = String(group.starIds.length).length * 7.2 + 8;
    const seat = (shift: number) => {
      const p = project(around(frame.rim + 0.035, group.phi + shift));
      const right = p.x >= centre.x;
      const box: Box = right
        ? { x0: p.x - 3, x1: p.x + width + countWidth, y0: p.y - 8, y1: p.y + 9 }
        : { x0: p.x - width - countWidth - 6, x1: p.x + 3, y0: p.y - 8, y1: p.y + 9 };
      return { p, right, box };
    };
    // Names on the far side of the disc are seen through it.
    const behind = project(around(frame.rToday, group.phi)).y < centre.y - 4;
    const opacity = (behind ? 0.55 : 1) * (focusTopic && focusTopic !== group.id ? 0.3 : 1);
    for (const shift of NAME_SHIFTS) {
      const s = seat(shift);
      if (!free(s.box)) continue;
      placed.push(s.box);
      return {
        id: group.id, text: group.label, count: group.starIds.length, x: s.p.x, y: s.p.y, right: s.right, opacity, dot: false,
        countX: s.right ? s.p.x + width + 4 : s.p.x - width - 10,
      };
    }
    const p = project(around(frame.rim + 0.012, group.phi));
    return { id: group.id, text: group.label, count: group.starIds.length, x: p.x, y: p.y, right: p.x >= centre.x, dot: true, opacity, countX: p.x };
  });

  // Years first, then quarters, then months; the newer first. One needs a clear box and its own 10.5 px of height.
  const ranks = model.rings.filter((ring) => !ring.nearToday)
    .sort((a, b) => MONTH_RANK[a.kind] - MONTH_RANK[b.kind] || b.theta - a.theta);
  const rows: number[] = [];
  const shown = new Map<string, MonthLabel>();
  for (const ring of ranks) {
    const on = project(frame.pos(ring.theta, model.scalePhi));
    const beside = project(frame.pos(ring.theta, model.scalePhi + 5));
    const right = on.x >= centre.x;
    const x = on.x + (beside.x - on.x) * 2.2 + (right ? 8 : -8);
    const y = on.y + 3.5;
    const text = ring.month === 1 ? `${ring.year}.01` : String(ring.month).padStart(2, '0');
    const w = text.length * 7.4;
    const box: Box = right ? { x0: x, x1: x + w, y0: y - 9, y1: y + 2 } : { x0: x - w, x1: x, y0: y - 9, y1: y + 2 };
    const ok = free(box) && !rows.some((row) => Math.abs(row - y) < MONTH_GAP);
    if (ok) { placed.push(box); rows.push(y); }
    shown.set(ring.key, { key: ring.key, text, x, y, right, shown: ok });
  }
  const months = model.rings.filter((ring) => !ring.nearToday).map((ring) => shown.get(ring.key)!);

  const apex = project(frame.apex);
  const soul = { x: apex.x, y: apex.y + 26 };

  // One sentence per group, on its newest new record: right of the cross first, then left, else just the cross.
  const news: NewNote[] = [];
  const fresh = new Map<string, typeof model.stars>();
  for (const star of model.stars) if (star.isNew) fresh.set(star.group, [...(fresh.get(star.group) ?? []), star]);
  for (const list of fresh.values()) {
    const newest = list.reduce((a, b) => (b.createdAt > a.createdAt ? b : a));
    const text = list.length > 1 ? `新 ${list.length} · ${md(newest.createdAt)}` : `新 · ${md(newest.createdAt)}`;
    const p = project(frame.pos(newest.theta, newest.phi));
    const w = text.length * 8.6;
    for (const right of [true, false]) {
      const box: Box = right ? { x0: p.x + 10, x1: p.x + 10 + w, y0: p.y + 6, y1: p.y + 19 } : { x0: p.x - 10 - w, x1: p.x - 10, y0: p.y + 6, y1: p.y + 19 };
      if (!free(box)) continue;
      placed.push(box);
      news.push({ id: newest.id, text, x: p.x, y: p.y, right });
      break;
    }
  }
  return { today, topics, months, soul, news, boxes: placed };
}

const NS = 'http://www.w3.org/2000/svg';

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>, parent: Element): SVGElementTagNameMap[K] {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  parent.appendChild(node);
  return node;
}

export type LabelHandlers = {
  onTopic: (id: string) => void;
  onMonth: (key: string) => void;
  onSoul: () => void;
};

type TopicNodes = { group: SVGGElement; name: SVGTextElement; count: SVGTextElement; mark: SVGGElement; markText: SVGTextElement };

/** The SVG layer. Elements are made once per model and only repositioned per frame. */
export class ConeLabelLayer {
  private topics = new Map<string, TopicNodes>();
  private months = new Map<string, SVGTextElement>();
  private news = new Map<string, SVGTextElement>();
  private today: SVGTextElement;
  private soul: SVGTextElement;

  constructor(private svg: SVGSVGElement, private handlers: LabelHandlers) {
    this.today = el('text', { class: 'c3-today' }, svg);
    this.soul = el('text', { class: 'c3-soul', role: 'button', tabindex: 0, 'aria-label': 'SOUL' }, svg);
    this.soul.textContent = 'SOUL';
    this.soul.addEventListener('click', (event) => { event.stopPropagation(); handlers.onSoul(); });
    this.soul.addEventListener('keydown', (event) => this.key(event, handlers.onSoul));
    this.soul.addEventListener('pointerdown', (event) => event.stopPropagation());
  }

  private key(event: KeyboardEvent, run: () => void) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    event.stopPropagation();
    run();
  }

  /** Make the elements for this model; a new model replaces the old ones. */
  build(model: ChartModel | null, level: 'none' | 'basic' | 'full') {
    for (const node of [...this.svg.children]) if (node !== this.today && node !== this.soul) node.remove();
    this.topics.clear();
    this.months.clear();
    this.news.clear();
    this.today.style.display = level === 'none' ? 'none' : '';
    this.soul.style.display = level === 'none' ? 'none' : '';
    if (level !== 'full' || !model) return;
    const groups = (model.collapsed ? model.groups : model.groups.filter((group) => group.isTopic)).filter((group) => group.starIds.length);
    for (const group of groups) {
      const open = () => this.handlers.onTopic(group.id);
      const g = el('g', { class: `c3-tlab${group.isTopic ? '' : ' pseudo'}`, 'data-topic': group.id, tabindex: 0, role: 'button', 'aria-label': `${group.label}，${group.starIds.length} 条` }, this.svg);
      const name = el('text', { class: 'n' }, g);
      name.textContent = group.label;
      const count = el('text', { class: 'c' }, g);
      count.textContent = String(group.starIds.length);
      const mark = el('g', { class: 'c3-tmark', tabindex: 0, role: 'button', 'aria-label': group.label }, this.svg);
      el('circle', { r: 2.6 }, mark);
      el('circle', { class: 'hit', r: 9 }, mark);
      const markText = el('text', { x: 8, y: 4 }, mark);
      markText.textContent = `${group.label} ${group.starIds.length}`;
      for (const node of [g, mark]) {
        node.addEventListener('click', (event) => { event.stopPropagation(); open(); });
        node.addEventListener('keydown', (event) => this.key(event as KeyboardEvent, open));
        node.addEventListener('pointerdown', (event) => event.stopPropagation());
      }
      this.topics.set(group.id, { group: g, name, count, mark, markText });
    }
    for (const ring of model.rings) {
      if (ring.nearToday) continue;
      const text = el('text', { class: 'c3-mlab', tabindex: 0, role: 'button', 'aria-label': `${ring.year} 年 ${ring.month} 月` }, this.svg);
      text.textContent = ring.month === 1 ? `${ring.year}.01` : String(ring.month).padStart(2, '0');
      const open = () => this.handlers.onMonth(ring.key);
      text.addEventListener('click', (event) => { event.stopPropagation(); open(); });
      text.addEventListener('keydown', (event) => this.key(event, open));
      text.addEventListener('pointerdown', (event) => event.stopPropagation());
      this.months.set(ring.key, text);
    }
  }

  update(layout: ConeLabels, state: { focusTopic: string | null; month: string | null; soul: boolean }) {
    this.today.setAttribute('x', layout.today.x.toFixed(1));
    this.today.setAttribute('y', layout.today.y.toFixed(1));
    this.today.setAttribute('text-anchor', 'middle');
    this.today.textContent = layout.today.text;
    this.soul.setAttribute('x', layout.soul.x.toFixed(1));
    this.soul.setAttribute('y', layout.soul.y.toFixed(1));
    this.soul.setAttribute('text-anchor', 'middle');
    this.soul.classList.toggle('on', state.soul);
    for (const label of layout.topics) {
      const nodes = this.topics.get(label.id);
      if (!nodes) continue;
      nodes.group.classList.toggle('on', state.focusTopic === label.id);
      if (!label.dot) {
        const anchor = label.right ? 'start' : 'end';
        nodes.name.setAttribute('x', label.x.toFixed(1));
        nodes.name.setAttribute('y', (label.y + 4).toFixed(1));
        nodes.name.setAttribute('text-anchor', anchor);
        nodes.count.setAttribute('x', label.countX.toFixed(1));
        nodes.count.setAttribute('y', (label.y + 4).toFixed(1));
        nodes.count.setAttribute('text-anchor', anchor);
        nodes.group.style.display = '';
        nodes.group.style.opacity = String(label.opacity);
        nodes.mark.style.display = 'none';
      } else {
        nodes.mark.setAttribute('transform', `translate(${label.x.toFixed(1)} ${label.y.toFixed(1)})`);
        nodes.markText.setAttribute('x', label.right ? '8' : '-8');
        nodes.markText.setAttribute('text-anchor', label.right ? 'start' : 'end');
        nodes.group.style.display = 'none';
        nodes.mark.style.display = '';
        nodes.mark.style.opacity = String(label.opacity);
      }
    }
    for (const label of layout.months) {
      const node = this.months.get(label.key);
      if (!node) continue;
      node.setAttribute('x', label.x.toFixed(1));
      node.setAttribute('y', label.y.toFixed(1));
      node.setAttribute('text-anchor', label.right ? 'start' : 'end');
      node.style.opacity = label.shown ? '1' : '0';
      node.style.pointerEvents = label.shown ? '' : 'none';
      node.classList.toggle('on', state.month === label.key);
    }
    const live = new Set(layout.news.map((note) => note.id));
    for (const [id, node] of this.news) if (!live.has(id)) { node.remove(); this.news.delete(id); }
    for (const note of layout.news) {
      let node = this.news.get(note.id);
      if (!node) { node = el('text', { class: 'c3-new' }, this.svg); this.news.set(note.id, node); }
      node.textContent = note.text;
      node.setAttribute('x', (note.x + (note.right ? 10 : -10)).toFixed(1));
      node.setAttribute('y', (note.y + 17).toFixed(1));
      node.setAttribute('text-anchor', note.right ? 'start' : 'end');
    }
  }
}
