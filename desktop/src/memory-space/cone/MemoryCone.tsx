import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';

import { plateGeometry, type ChartModel } from '../chartCoordinates';
import { md, recordNo, ymd } from '../chartFormat';
import type { ChartSelection } from '../MemoryPlate';
import { FramePerf, perfSampleVisible } from '../renderCapability';
import type { Worldline } from '../types';
import { createConeRenderer, type ConeHandle, type ConeHover } from './coneRenderer';
import '../MemoryPlate.css';
import './MemoryCone.css';

export type ConeControls = {
  /** Camera up and the cone flat onto the disc; `done` when it is the plate. */
  toPlate: (done: () => void) => void;
  /** And back; `done` when the camera is home. */
  fromPlate: (done: () => void) => void;
};

export type MemoryConeProps = {
  model: ChartModel | null;
  width: number;
  height: number;
  selection: ChartSelection;
  focusTopic: string | null;
  reducedMotion: boolean;
  /** The first showing of this visit: the plate draws itself and sinks into the cone. */
  entering: boolean;
  /** Mounted flat under the plate, waiting for `fromPlate`. */
  startFlat?: boolean;
  /** Time the frames and give way to the plate when the machine is slow (not when the user chose 3D). */
  timed: boolean;
  worldline: Worldline;
  footnote?: string | null;
  marks?: readonly string[];
  onEntered: () => void;
  onSelect: (selection: ChartSelection) => void;
  onOverview: () => void;
  /** The context could not be made. */
  onUnavailable: () => void;
  onContextLost: () => void;
  /** Two slow windows in a row. */
  onSlow: () => void;
};

/** Dev builds only: `?slowgl=34` holds every drawn frame that long, to walk the slow-machine path on a fast one. */
function devSlowMs(): number {
  if (!import.meta.env.DEV || typeof location === 'undefined') return 0;
  const value = Number(new URLSearchParams(location.search).get('slowgl'));
  return Number.isFinite(value) && value > 0 ? value : 0;
}

export const MemoryCone = forwardRef<ConeControls, MemoryConeProps>(function MemoryCone(props, ref) {
  const { model, width, height } = props;
  const g = plateGeometry(width, height);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const handleRef = useRef<ConeHandle | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [hover, setHover] = useState<ConeHover>(null);
  const modelRef = useRef<ChartModel | null>(null);
  const entranceStarted = useRef(false);

  useImperativeHandle(ref, () => ({
    toPlate: (done) => { if (handleRef.current) handleRef.current.toPlate(done); else done(); },
    fromPlate: (done) => { if (handleRef.current) handleRef.current.fromPlate(done); else done(); },
  }), []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const perf = new FramePerf(performance.now());
    const created = createConeRenderer(host, {
      onSelect: (selection) => propsRef.current.onSelect(selection),
      onOverview: () => propsRef.current.onOverview(),
      onHover: setHover,
      onDrawn: ({ at, ms }) => {
        if (!propsRef.current.timed) return;
        const verdict = perf.push({ at, ms, visible: perfSampleVisible(document.visibilityState, document.hasFocus()), focused: document.hasFocus() });
        if (verdict === 'lower-tier') handleRef.current?.setTier(1);
        else if (verdict === 'to-plate') propsRef.current.onSlow();
      },
      onEntering: (entering) => { if (!entering) propsRef.current.onEntered(); },
      onContextLost: () => propsRef.current.onContextLost(),
    });
    if (!created.ok) { propsRef.current.onUnavailable(); return undefined; }
    handleRef.current = created.handle;
    created.handle.setSlow(devSlowMs());
    if (import.meta.env.DEV) (window as unknown as { __amadeusCone?: ConeHandle }).__amadeusCone = created.handle;
    return () => {
      if (import.meta.env.DEV) delete (window as unknown as { __amadeusCone?: ConeHandle }).__amadeusCone;
      created.handle.dispose();
      handleRef.current = null;
      // The next renderer starts with no model; StrictMode replays this effect with the same refs.
      modelRef.current = null;
      entranceStarted.current = false;
    };
  }, []);

  useEffect(() => {
    const handle = handleRef.current;
    if (!handle) return;
    handle.setWorldline(props.worldline === 'beta' ? 'beta' : 'steins_gate');
  }, [props.worldline]);

  useEffect(() => {
    const handle = handleRef.current;
    if (!handle) return;
    const size = { w: width, h: height, cx: g.cx, cy: g.cy, R: g.radius };
    if (modelRef.current !== model) {
      modelRef.current = model;
      handle.setModel(model, size);
      handle.setView({ selection: props.selection, focusTopic: props.focusTopic, marks: props.marks ?? [], reducedMotion: props.reducedMotion });
      if (model && props.startFlat) handle.holdPlate();
      else if (model && props.entering && !entranceStarted.current) {
        entranceStarted.current = true;
        if (props.reducedMotion) props.onEntered();
        else handle.entrance();
      }
    } else {
      handle.resize(size);
    }
    // The view effect below carries selection, focus and marks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, width, height, g.cx, g.cy, g.radius]);

  useEffect(() => {
    handleRef.current?.setView({ selection: props.selection, focusTopic: props.focusTopic, marks: props.marks ?? [], reducedMotion: props.reducedMotion });
  }, [props.selection, props.focusTopic, props.marks, props.reducedMotion]);

  useEffect(() => { if (!hover) return; setHover(null); }, [model]); // eslint-disable-line react-hooks/exhaustive-deps

  const tip = useMemo(() => {
    if (!hover || !model) return null;
    if (hover.kind === 'star') {
      const star = model.byId.get(hover.id);
      if (!star) return null;
      const text = star.label.length > 18 ? `${star.label.slice(0, 17)}…` : star.label;
      const meta = `${recordNo(star.no)} · ${md(star.createdAt)}${star.evidenceCount > 1 ? ` · 你提过 ${star.evidenceCount} 次` : ''}`;
      return { x: hover.x, y: hover.y, text, meta, testid: 'cone-hover' };
    }
    const cell = model.cells.get(hover.key);
    const group = model.groups.find((t) => t.id === hover.group);
    if (!cell || !group) return null;
    return { x: hover.x, y: hover.y, text: `${group.label} · ${cell.band.key.replace('-', '.')} · ${cell.starIds.length} 条`, meta: '点一下展开', testid: 'cone-band-hover' };
  }, [hover, model]);

  const collapsed = Boolean(model?.collapsed);
  return (
    <div className="memory-cone" data-testid="memory-cone" role="group" aria-label="记忆星图（立体）">
      <div ref={hostRef} className="c3-host" />
      {tip ? (
        <div className="c3-tip" data-testid={tip.testid} style={{ transform: `translate(${Math.round(tip.x + 14)}px, ${Math.round(tip.y - 46)}px)` }}>
          <p>{tip.text}</p>
          <p className="m">{tip.meta}</p>
        </div>
      ) : null}
      {model && model.stars.length > 6 ? (
        <div className="p-legend" data-testid="cone-legend">
          <div className="lrow">
            <span><i className="lg-dot" />认知</span>
            <span><i className="lg-exp" />片段（会淡去）</span>
            <span><i className="lg-inf" />未归组</span>
            {collapsed ? <span><i className="lg-tick" />一根刻线 = 一条收起来的记忆，横跨它那个月</span> : null}
          </div>
          <div className="lrow">
            <span><i className="lg-line" />同一主题，按记下的先后相连</span>
            <span><i className="lg-new" />{collapsed ? '单独画的：她每轮都带着的、新记下的' : '上次来过以后新记下的'}</span>
          </div>
          <p className="fig" data-testid="cone-fig">FIG. 1 — 自 {ymd(model.origin!)} 起 · 圆盘是今天 · 往下每圈早一个月{collapsed ? '（一年以前每圈一季）' : ''}</p>
          {props.footnote ? <p className="note" data-testid="plate-footnote">{props.footnote}</p> : null}
        </div>
      ) : null}
    </div>
  );
});
