import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';

import { plateGeometry, platePoint, type ChartModel, type ChartStar } from './chartCoordinates';
import { md, recordNo, ymd } from './chartFormat';
import { PlateStars } from './PlateStars';
import './MemoryPlate.css';

export type ChartSelection =
  | null
  | { kind: 'star'; id: string }
  | { kind: 'topic'; id: string }
  | { kind: 'soul' }
  | { kind: 'month'; key: string };
export type PlateView = { s: number; x: number; y: number };
type Point = { x: number; y: number };
type Box = { x: number; y: number; w: number; h: number };

const HOME: PlateView = { s: 1, x: 0, y: 0 };
const NO_STARS: ChartStar[] = [];
const FOCUS_MS = 520;
const SOUL_R = 15;
const TOPIC_CHAR = 17.2;
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

export type LabelLayout = {
  months: Array<{ key: string; text: string; x: number; y: number; box: Box }>;
  topics: Array<{ id: string; text: string; count: number; x: number; y: number; right: boolean; box: Box }>;
  today: { x: number; y: number; box: Box };
};

// Labels that would collide slide around their own ring (the same rule the stars
// follow), so a month label never leaves its month and a topic name stays at its arm.
export function layoutLabels(model: ChartModel, width: number, height: number): LabelLayout {
  const g = plateGeometry(width, height);
  const at = (theta: number, phi: number): Point => {
    const p = platePoint(theta, phi, g.radius, model.thetaFit);
    return { x: g.cx + p.x, y: g.cy + p.y };
  };
  const placed: Box[] = [];
  const settle = (offsets: number[], make: (offset: number) => Box) => {
    for (const offset of offsets) {
      const box = make(offset);
      if (!placed.some((other) => overlaps(box, other))) { placed.push(box); return offset; }
    }
    placed.push(make(0));
    return 0;
  };
  const spread = (step: number, count: number) => [0, ...Array.from({ length: count * 2 }, (_, i) => (i % 2 ? -1 : 1) * step * Math.ceil((i + 1) / 2))];

  const todayPoint = at(model.thetaToday + 3.2, model.scalePhi);
  const today = { ...todayPoint, box: { x: todayPoint.x - 36, y: todayPoint.y - 8, w: 72, h: 14 } };
  placed.push(today.box);

  const months = model.rings.filter((ring) => !ring.nearToday).map((ring) => {
    const text = ring.month === 1 ? `${ring.year}.01` : String(ring.month).padStart(2, '0');
    const w = text.length * 7.4 + 6;
    const box = (offset: number): Box => { const p = at(ring.theta, model.scalePhi + offset); return { x: p.x - w / 2, y: p.y - 7, w, h: 13 }; };
    const offset = settle(spread(3, 8), box);
    const p = at(ring.theta, model.scalePhi + offset);
    return { key: ring.key, text, x: p.x, y: p.y, box: box(offset) };
  });

  const topics = model.topics.filter((topic) => topic.starIds.length).map((topic) => {
    const last = model.byId.get(topic.starIds[topic.starIds.length - 1])!;
    const w = topic.label.length * TOPIC_CHAR + String(topic.starIds.length).length * 7 + 6;
    const box = (offset: number): Box => {
      const p = at(last.theta + 4.2, topic.phi + offset);
      return p.x >= g.cx ? { x: p.x, y: p.y - 10, w, h: 16 } : { x: p.x - w, y: p.y - 10, w, h: 16 };
    };
    const offset = settle(spread(3, 8), box);
    const p = at(last.theta + 4.2, topic.phi + offset);
    return { id: topic.id, text: topic.label, count: topic.starIds.length, x: p.x, y: p.y, right: p.x >= g.cx, box: box(offset) };
  });
  return { months, topics, today };
}

function focusView(model: ChartModel, topicId: string | null, width: number, height: number): PlateView {
  const topic = topicId ? model.topics.find((t) => t.id === topicId) : null;
  if (!topic || !topic.starIds.length) return HOME;
  const g = plateGeometry(width, height);
  const pts = topic.starIds.map((id) => { const s = model.byId.get(id)!; return platePoint(s.theta, s.phi, g.radius, model.thetaFit); });
  const mx = pts.reduce((a, p) => a + p.x, 0) / pts.length;
  const my = pts.reduce((a, p) => a + p.y, 0) / pts.length;
  const s = 1.1;
  // Bring the arm a little toward the middle; never recentre on it or on SOUL.
  return { s, x: (-20 - mx * s) * 0.6, y: -my * s * 0.6 };
}

export type MemoryPlateProps = {
  model: ChartModel | null;
  soul: 'ready' | 'loading' | 'muted';
  width: number;
  height: number;
  selection: ChartSelection;
  focusTopic: string | null;
  framedTopic: string | null;
  reducedMotion: boolean;
  entering: boolean;
  footnote?: string | null;
  /** Records drawn with the dashed "this turn" reticle; ids not on the plate are skipped. */
  marks?: readonly string[];
  onEntered: () => void;
  onSelect: (selection: ChartSelection) => void;
  onOverview: () => void;
};

const activate = (run: () => void) => (event: KeyboardEvent) => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); run(); }
};

export function MemoryPlate(props: MemoryPlateProps) {
  const { model, width, height, selection, focusTopic, reducedMotion } = props;
  const g = plateGeometry(width, height);
  const [hoverId, setHoverId] = useState<string | null>(null);
  // Roving focus: the stars are one Tab stop; arrow keys walk them in No. order.
  const [activeId, setActiveId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [view, setView] = useState<PlateView>(HOME);
  const viewRef = useRef<PlateView>(HOME);
  const [playing, setPlaying] = useState(false);
  const onEntered = useRef(props.onEntered);
  onEntered.current = props.onEntered;

  const at = (theta: number, phi: number): Point => {
    const p = platePoint(theta, phi, g.radius, model?.thetaFit ?? 49);
    return { x: g.cx + p.x, y: g.cy + p.y };
  };
  const labels = useMemo(() => (model ? layoutLabels(model, width, height) : null), [model, width, height]);
  const pos = useMemo(() => new Map((model?.stars ?? []).map((s) => {
    const p = platePoint(s.theta, s.phi, g.radius, model!.thetaFit);
    return [s.id, { x: g.cx + p.x, y: g.cy + p.y }] as const;
  })), [model, g.cx, g.cy, g.radius]);

  // The plate moves only while a focus change is easing in; otherwise nothing ticks.
  useEffect(() => {
    const target = model ? focusView(model, props.framedTopic, width, height) : HOME;
    const from = viewRef.current;
    const apply = (next: PlateView) => { viewRef.current = next; setView(next); };
    if (reducedMotion || (from.s === target.s && from.x === target.x && from.y === target.y)) { apply(target); return undefined; }
    let frame = 0;
    const started = performance.now();
    const step = (now: number) => {
      const k = easeInOut(Math.min(1, (now - started) / FOCUS_MS));
      apply({ s: from.s + (target.s - from.s) * k, x: from.x + (target.x - from.x) * k, y: from.y + (target.y - from.y) * k });
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [model, props.framedTopic, width, height, reducedMotion]);

  const order = useMemo(() => [...(model?.stars ?? [])].sort((a, b) => a.theta - b.theta), [model]);
  const fresh = order.filter((s) => s.isNew);
  const stagger = Math.min(22, 1400 / Math.max(1, order.length));
  const entranceMs = Math.max(260 + order.length * stagger + 900, 1150 + fresh.length * 520) + 300;
  useLayoutEffect(() => {
    if (!props.entering || !model) return undefined;
    if (reducedMotion) { onEntered.current(); return undefined; }
    setPlaying(true);
    const timer = window.setTimeout(() => { setPlaying(false); onEntered.current(); }, entranceMs);
    return () => window.clearTimeout(timer);
  }, [props.entering, model, reducedMotion, entranceMs]);

  const delay = (ms: number) => (playing ? { animationDelay: `${Math.round(ms)}ms` } : undefined);
  const dimmed = (s: ChartStar) => Boolean(focusTopic && s.topicId !== focusTopic);
  const ready = props.soul === 'ready' && Boolean(model);
  const selectedStar = selection?.kind === 'star' ? model?.byId.get(selection.id) ?? null : null;
  const rovingId = activeId && model?.byId.has(activeId) ? activeId : model?.stars[0]?.id ?? null;
  const starEl = (id: string) => svgRef.current?.querySelector<SVGGElement>(`[data-hit="star"][data-id="${CSS.escape(id)}"]`) ?? null;
  useEffect(() => { if (selectedStar) setActiveId(selectedStar.id); }, [selectedStar]);
  // When the reading of a star closes and focus was lost with it, it returns to that star.
  const lastStarRef = useRef<string | null>(null);
  useEffect(() => {
    const previous = lastStarRef.current;
    lastStarRef.current = selectedStar?.id ?? null;
    if (!previous || previous === selectedStar?.id) return;
    const active = document.activeElement;
    if (!active || active === document.body || !active.isConnected) starEl(previous)?.focus({ preventScroll: true });
  });
  const onSelectRef = useRef(props.onSelect);
  onSelectRef.current = props.onSelect;
  const selectStar = useCallback((id: string) => onSelectRef.current({ kind: 'star', id }), []);
  const hovered = hoverId && hoverId !== selectedStar?.id ? model?.byId.get(hoverId) ?? null : null;
  const transform = `translate(${g.cx + view.x} ${g.cy + view.y}) scale(${view.s}) translate(${-g.cx} ${-g.cy})`;
  const soulAt = { x: g.cx + view.x, y: g.cy + view.y };
  const young = Boolean(model && model.stars.length && model.stars.length <= 6);
  const armed = model?.topics.filter((t) => t.starIds.length) ?? [];

  return (
    <div className="memory-plate" data-testid="memory-plate">
      <svg
        ref={svgRef}
        className={`plate${playing ? ' is-entering' : ''}`}
        width={width}
        height={height}
        role="group"
        aria-label="记忆星图"
        data-view-scale={view.s.toFixed(3)}
        onDoubleClick={(event) => { if (!(event.target as Element).closest('[data-hit]')) props.onOverview(); }}
      >
        <g className={`p-root${focusTopic ? ' dim' : ''}`} transform={transform}>
          {model ? (
            <g>
              {model.rings.filter((r) => !r.nearToday).map((ring, i) => (
                <circle key={ring.key} className="p-ring" cx={g.cx} cy={g.cy} r={at(ring.theta, 0).y - g.cy}
                  pathLength={playing ? 1 : undefined} style={delay(i * 70)} />
              ))}
              <circle className="p-today" cx={g.cx} cy={g.cy} r={at(model.thetaToday, 0).y - g.cy}
                pathLength={playing ? 1 : undefined} style={delay(model.rings.length * 70)} />
              {model.origin !== null ? (() => {
                const a = at(17, model.scalePhi);
                const b = at(model.thetaToday + 7, model.scalePhi);
                return <line className="p-ruler" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />;
              })() : null}
            </g>
          ) : null}
          {armed.map((topic) => (
            <path key={topic.id} className={`p-line${topic.id === focusTopic ? ' lit' : ''}`} style={delay(900)}
              d={topic.starIds.map((id, i) => `${i ? 'L' : 'M'}${pos.get(id)!.x.toFixed(1)} ${pos.get(id)!.y.toFixed(1)}`).join('')} />
          ))}
          <PlateStars stars={model?.stars ?? NO_STARS} order={order} pos={pos} cx={g.cx} cy={g.cy} focusTopic={focusTopic}
            selectedId={selectedStar?.id ?? null} rovingId={rovingId} playing={playing} stagger={stagger}
            onSelectStar={selectStar} onHover={setHoverId} onActive={setActiveId} />
          {labels?.months.map((m) => (
            <text key={m.key} data-hit="month" className={`p-ringlab${selection?.kind === 'month' && selection.key === m.key ? ' on' : ''}`}
              x={m.x} y={m.y + 3.5} textAnchor="middle" role="button" tabIndex={0} aria-label={`${m.key.replace('-', ' 年 ')} 月`}
              onClick={(event) => { event.stopPropagation(); props.onSelect({ kind: 'month', key: m.key }); }}
              onKeyDown={activate(() => props.onSelect({ kind: 'month', key: m.key }))}>{m.text}</text>
          ))}
          {labels ? (
            <text className="p-todaylab" x={labels.today.x} y={labels.today.y + 3.5} textAnchor="middle">今天 {md(model!.today)}</text>
          ) : null}
          {labels?.topics.map((t) => (
            <g key={t.id} data-hit="topic" role="button" tabIndex={0} aria-label={`${t.text}，${t.count} 条`}
              className={`p-topic${t.id === focusTopic ? ' on' : ''}`}
              onClick={(event) => { event.stopPropagation(); props.onSelect({ kind: 'topic', id: t.id }); }}
              onKeyDown={activate(() => props.onSelect({ kind: 'topic', id: t.id }))}>
              <text className="p-tlab" x={t.x} y={t.y + 4} textAnchor={t.right ? 'start' : 'end'}>{t.text}</text>
              <text className="p-tcnt" x={t.right ? t.x + t.text.length * TOPIC_CHAR + 4 : t.x - t.text.length * TOPIC_CHAR - 10}
                y={t.y + 4} textAnchor={t.right ? 'start' : 'end'}>{t.count}</text>
            </g>
          ))}
          {young ? model!.stars.map((s) => {
            const p = pos.get(s.id)!;
            const right = p.x >= g.cx;
            const lx = p.x + (right ? 46 : -46);
            const ly = p.y - 16;
            const anchor = right ? 'start' : 'end';
            return (
              <g key={`young-${s.id}`} className="p-young">
                <path className="p-slead" d={`M${p.x + (right ? 6 : -6)} ${p.y - 3}L${p.x + (right ? 20 : -20)} ${ly + 4}H${lx}`} />
                <text className="p-slab" x={lx + (right ? 6 : -6)} y={ly + 8} textAnchor={anchor}>{s.label}</text>
                <text className="p-slab-m" x={lx + (right ? 6 : -6)} y={ly + 24} textAnchor={anchor}>{md(s.createdAt)}</text>
              </g>
            );
          }) : null}
          {fresh.map((s, i) => {
            const p = pos.get(s.id)!;
            return (
              <g key={`new-${s.id}`} className="p-new" data-new-id={s.id} transform={`translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`}
                style={{ ...delay(1150 + i * 520), opacity: dimmed(s) ? 0.2 : undefined }}>
                <path d="M0 -11v5M0 6v5M-11 0h5M6 0h5" pathLength={playing ? 1 : undefined} />
                <text x={10} y={17}>新 · {md(s.createdAt)}</text>
              </g>
            );
          })}
          {(props.marks ?? []).map((id) => {
            const p = id === selectedStar?.id ? undefined : pos.get(id);
            if (!p) return null;
            return (
              <g key={`ref-${id}`} className="p-ref" data-testid="plate-ref" data-ref-id={id} transform={`translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`}>
                <circle r={10} />
                <text x={-16} y={3} textAnchor="end">本轮参照</text>
              </g>
            );
          })}
          {selectedStar ? (
            <g className="p-reticle" data-testid="plate-reticle" transform={`translate(${pos.get(selectedStar.id)!.x} ${pos.get(selectedStar.id)!.y})`}>
              <circle r={12} />
              <circle className="r2" r={19} />
              <path d="M0 -26v6M0 20v6M-26 0h6M20 0h6" />
            </g>
          ) : null}
          {hovered ? (() => {
            const p = pos.get(hovered.id)!;
            const text = hovered.label.length > 18 ? `${hovered.label.slice(0, 17)}…` : hovered.label;
            const meta = `${recordNo(hovered.no)} · ${md(hovered.createdAt)}${hovered.evidenceCount > 1 ? ` · 你提过 ${hovered.evidenceCount} 次` : ''}`;
            return (
              <g className="p-hover" data-testid="plate-hover" transform={`translate(${p.x + 14} ${p.y - 30})`}>
                <rect width={Math.max(text.length * 13, meta.length * 6.6) + 24} height={40} />
                <text x={10} y={17}>{text}</text>
                <text className="m" x={10} y={32}>{meta}</text>
              </g>
            );
          })() : null}
        </g>
        {/* SOUL sits outside the scaled group: focusing a topic never makes it bigger. */}
        <g className="p-soul" data-hit="soul" data-testid="plate-soul" data-state={props.soul}
          transform={`translate(${soulAt.x} ${soulAt.y})`}
          role={ready ? 'button' : undefined} tabIndex={ready ? 0 : undefined} aria-label="SOUL"
          aria-pressed={ready ? selection?.kind === 'soul' : undefined}
          onClick={ready ? (event) => { event.stopPropagation(); props.onSelect({ kind: 'soul' }); } : undefined}
          onKeyDown={ready ? activate(() => props.onSelect({ kind: 'soul' })) : undefined}>
          <circle className="shell" r={SOUL_R} />
          <path className="cut" d="M-8.6 6.4A10.8 10.8 0 0 1 -6.2 -8.8M3.4 -10.3A10.8 10.8 0 0 1 10.6 2" />
          <circle className="core" r={3.6} />
          <text className="lab" y={30} textAnchor="middle">SOUL</text>
        </g>
      </svg>
      {model && model.stars.length > 6 ? (
        <div className="p-legend" data-testid="plate-legend">
          <div className="lrow">
            <span><i className="lg-dot" />认知</span>
            <span><i className="lg-exp" />片段（会淡去）</span>
            <span><i className="lg-inf" />未归组</span>
          </div>
          <div className="lrow">
            <span><i className="lg-line" />同一主题，按记下的先后相连</span>
            <span><i className="lg-new" />上次来过以后新记下的</span>
          </div>
          <p className="fig">FIG. 1 — 自 {ymd(model.origin!)} 起 · 每圈一个月 · 越往外越近</p>
          {props.footnote ? <p className="note" data-testid="plate-footnote">{props.footnote}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
