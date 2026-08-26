import { useEffect, useRef, useState } from 'react';

import { layoutProjection } from './layout';
import {
  createOverviewMotion,
  pickLiveNode,
  restLayoutFromProjection,
  step,
  type MotionState,
  type OverviewFrame,
  type PresentationChangeCause,
} from './overviewMotion';
import { displayNodesFromPlan, type OverviewDisclosurePlan } from './overviewPresentation';
import type { OrreryCamera, OrreryRuntime } from './orreryRenderer';
import type { DecorationLevel, FrameSample } from './renderCapability';
import { isPointerDrag } from './pointerIntent';
import type { DisplayNode, Worldline } from './types';

export type ScreenAnchor = { x: number; y: number };

function cameraFromFrame(frame: OverviewFrame, mode: '3d' | '2d'): OrreryCamera {
  return {
    yaw: mode === '2d' ? 0 : frame.camera.yaw,
    pitch: mode === '2d' ? 0 : frame.camera.pitch,
    distance: mode === '2d' ? 6.4 : frame.camera.distance,
    lookAt: frame.camera.lookAt,
    mode,
  };
}

function displayFromFrame(plan: OverviewDisclosurePlan, frame: OverviewFrame): DisplayNode[] {
  const rest = layoutProjection(plan.envelope);
  const byId = new Map(rest.map((node) => [node.id, node]));
  const nodes: DisplayNode[] = [];
  for (const [id, live] of frame.nodes) {
    const base = byId.get(id);
    if (!base) continue;
    nodes.push({ ...base, position: live.position, radius: live.hitRadius });
  }
  return nodes;
}

function seedFromProps(
  current: {
    plan: OverviewDisclosurePlan;
    selectedId: string | null;
    mode: '3d' | '2d';
    reducedMotion: boolean;
    expandedTopicIds: readonly string[];
    scopeKey?: string;
    insets?: { top: number; right: number; bottom: number; left: number };
  },
  width: number,
  height: number,
) {
  const scope = current.plan.envelope.scope;
  return {
    scopeKey: current.scopeKey ?? `${scope.session_id}|${scope.worldline}|${scope.identity_mode}`,
    plan: current.plan,
    restLayout: restLayoutFromProjection(current.plan.envelope),
    selectedId: current.selectedId,
    focusedId: current.selectedId,
    expandedTopicIds: current.expandedTopicIds,
    viewport: { x: 0, y: 0, width: Math.max(1, width || 1280), height: Math.max(1, height || 800) },
    insets: current.insets ?? { top: 64, right: 16, bottom: 16, left: 16 },
    renderSurface: current.mode === '2d' ? 'compatibility' as const : '3d' as const,
    reducedMotion: current.reducedMotion,
  };
}

function evidenceIds(plan: OverviewDisclosurePlan): string {
  return displayNodesFromPlan(plan)
    .filter((node) => node.kind === 'fact' || node.kind === 'experience')
    .map((node) => node.id)
    .join(',');
}

export function ConstellationStage(props: {
  plan: OverviewDisclosurePlan;
  selectedId: string | null;
  mode: '3d' | '2d';
  reducedMotion: boolean;
  decoration?: DecorationLevel;
  expandedTopicIds: readonly string[];
  worldline: Worldline;
  query: string;
  motionCause?: PresentationChangeCause;
  motionCauses?: readonly PresentationChangeCause[];
  motionEpoch?: number;
  scopeKey?: string;
  nowMs?: number;
  insets?: { top: number; right: number; bottom: number; left: number };
  onSelect: (id: string | null) => void;
  onUnwind?: () => void;
  onSelectedAnchor?: (anchor: ScreenAnchor | null) => void;
  onOwnershipChange?: (ownership: OverviewFrame['ownership']) => void;
  onCreateFail?: () => void;
  onContextLost?: () => void;
  onFrame?: (sample: FrameSample) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const runtimeRef = useRef<OrreryRuntime | null>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const rightPressRef = useRef<{ x: number; y: number; dragged: boolean } | null>(null);
  const movedRef = useRef(false);
  const hoverGateRef = useRef<string | null>(null);
  const pointerActiveRef = useRef(false);
  const hiddenClockRef = useRef(false);
  const propsRef = useRef(props);

  const documentHidden = () => typeof document !== 'undefined' && document.visibilityState !== 'visible';
  const clockMs = () => propsRef.current.nowMs ?? (typeof performance !== 'undefined' ? performance.now() : 0);
  const ambientHold = () => (
    documentHidden()
    || propsRef.current.reducedMotion
    || Boolean(propsRef.current.selectedId)
    || Boolean(hoverGateRef.current)
    || pointerActiveRef.current
    || frameRef.current?.ownership === 'user'
  );
  const motionRef = useRef<MotionState | null>(null);
  const frameRef = useRef<OverviewFrame | null>(null);
  const epochRef = useRef(props.motionEpoch ?? 0);
  const scopeRef = useRef(props.scopeKey ?? '');
  const [view, setView] = useState({
    yaw: 0.62,
    pitch: 0.48,
    zoom: 6.15,
    ownership: 'rest' as OverviewFrame['ownership'],
    generation: 1,
  });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [pointerActive, setPointerActive] = useState(false);
  propsRef.current = props;
  if (!motionRef.current) {
    const created = step(
      createOverviewMotion(seedFromProps(props, 1280, 800)),
      { type: 'tick', nowMs: clockMs(), paused: true },
    );
    motionRef.current = created.state;
    frameRef.current = created.frame;
  }

  const publish = (next: OverviewFrame) => {
    frameRef.current = next;
    const camera = next.camera;
    setView((prev) => {
      if (
        prev.yaw === camera.yaw
        && prev.pitch === camera.pitch
        && prev.zoom === camera.distance
        && prev.ownership === next.ownership
        && prev.generation === next.generation
      ) return prev;
      return {
        yaw: camera.yaw,
        pitch: camera.pitch,
        zoom: camera.distance,
        ownership: next.ownership,
        generation: next.generation,
      };
    });
    propsRef.current.onOwnershipChange?.(next.ownership);
    const selected = propsRef.current.selectedId;
    const fromRuntime = selected ? runtimeRef.current?.project(selected) : null;
    const fromFrame = selected ? next.projected.get(selected) : null;
    const projected = fromRuntime ?? (fromFrame ? { x: fromFrame.x, y: fromFrame.y } : null);
    propsRef.current.onSelectedAnchor?.(projected);
  };

  const ensureMotion = (current: typeof props, width: number, height: number) => {
    if (!motionRef.current) {
      const created = step(
        createOverviewMotion(seedFromProps(current, width, height)),
        { type: 'tick', nowMs: current.nowMs ?? clockMs(), paused: true },
      );
      motionRef.current = created.state;
      frameRef.current = created.frame;
      epochRef.current = current.motionEpoch ?? 0;
      scopeRef.current = current.scopeKey ?? seedFromProps(current, width, height).scopeKey;
      return created.frame;
    }
    const seed = seedFromProps(current, width, height);
    if (seed.scopeKey !== scopeRef.current) {
      const changed = step(motionRef.current, { type: 'scopeChanged', seed });
      const rebased = step(changed.state, {
        type: 'tick',
        nowMs: current.nowMs ?? clockMs(),
        paused: true,
      });
      motionRef.current = rebased.state;
      scopeRef.current = seed.scopeKey;
      epochRef.current = current.motionEpoch ?? epochRef.current;
      return rebased.frame;
    }
    const epoch = current.motionEpoch ?? 0;
    if (epoch !== epochRef.current) {
      epochRef.current = epoch;
      const causes = current.motionCauses?.length
        ? current.motionCauses
        : [current.motionCause ?? 'viewport'];
      let changed = { state: motionRef.current, frame: frameRef.current! };
      for (const cause of causes) {
        changed = step(changed.state, {
          type: 'presentationChanged',
          cause,
          presentation: seed,
        });
      }
      motionRef.current = changed.state;
      return changed.frame;
    }
    const currentView = frameRef.current?.viewport;
    if (
      currentView
      && (Math.abs(currentView.width - seed.viewport.width) > 0.5
        || Math.abs(currentView.height - seed.viewport.height) > 0.5)
    ) {
      const changed = step(motionRef.current, {
        type: 'presentationChanged',
        cause: 'viewport',
        presentation: seed,
      });
      motionRef.current = changed.state;
      return changed.frame;
    }
    return frameRef.current;
  };

  const pickAtClient = (clientX: number, clientY: number, rect: DOMRect) => {
    const runtime = runtimeRef.current;
    const live = frameRef.current;
    const fromGl = runtime?.supported ? runtime.pick(clientX, clientY, rect) : null;
    if (fromGl) return fromGl;
    if (!live) return null;
    const viewport = live.viewport ?? { x: 0, y: 0, width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
    const x = ((clientX - rect.left) / Math.max(1, rect.width)) * Math.max(1, viewport.width);
    const y = ((clientY - rect.top) / Math.max(1, rect.height)) * Math.max(1, viewport.height);
    return pickLiveNode(live, x, y);
  };

  const setPointerHold = (active: boolean) => {
    pointerActiveRef.current = active;
    setPointerActive(active);
    const runtime = runtimeRef.current;
    if (motionRef.current) {
      const gated = step(motionRef.current, {
        type: 'activityChanged',
        hovered: Boolean(hoverGateRef.current),
        pointerActive: active,
        nowMs: clockMs(),
      });
      motionRef.current = gated.state;
      if (runtime) {
        runtime.setAmbientPaused(ambientHold());
        runtime.applyFrame(gated.frame, displayFromFrame(propsRef.current.plan, gated.frame));
        runtime.render(cameraFromFrame(gated.frame, propsRef.current.mode));
      }
      publish(gated.frame);
      return;
    }
    runtime?.setAmbientPaused(ambientHold());
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    let cancelled = false;
    let raf = 0;
    const onLost = (event: Event) => {
      event.preventDefault();
      if (cancelled) return;
      propsRef.current.onContextLost?.();
    };
    const onWinResize = () => {
      runtimeRef.current?.setSize(canvas.clientWidth, canvas.clientHeight);
    };
    const stopLoop = () => {
      if (raf) {
        window.cancelAnimationFrame(raf);
        raf = 0;
      }
    };
    const paintHold = (frame: OverviewFrame) => {
      const live = runtimeRef.current;
      live?.setAmbientPaused(true);
      if (live && frame) {
        live.applyFrame(frame, displayFromFrame(propsRef.current.plan, frame));
        live.render(cameraFromFrame(frame, propsRef.current.mode));
      }
      publish(frame);
    };
    const applyHidden = (nowMs: number) => {
      canvas.setAttribute('data-clock', 'paused');
      canvas.setAttribute('data-ambient', 'settled');
      stopLoop();
      if (!motionRef.current) {
        hiddenClockRef.current = true;
        runtimeRef.current?.setAmbientPaused(true);
        return;
      }
      if (!hiddenClockRef.current) {
        const frozen = step(motionRef.current, { type: 'tick', nowMs, paused: true });
        motionRef.current = frozen.state;
        paintHold(frozen.frame);
      }
      hiddenClockRef.current = true;
      runtimeRef.current?.setAmbientPaused(true);
    };
    const applyVisible = (nowMs: number) => {
      canvas.setAttribute('data-clock', 'running');
      if (hiddenClockRef.current && motionRef.current) {
        const resumed = step(motionRef.current, { type: 'tick', nowMs, paused: true });
        motionRef.current = resumed.state;
        publish(resumed.frame);
      }
      hiddenClockRef.current = false;
      runtimeRef.current?.setAmbientPaused(ambientHold());
      startLoop();
    };
    const runFrame = (now: number) => {
      raf = 0;
      const live = runtimeRef.current;
      const current = propsRef.current;
      if (cancelled || !live || !motionRef.current || hiddenClockRef.current) return;
      frames += 1;
      current.onFrame?.({
        at: now,
        visible: true,
        warmup: now - started < 1200 || frames < 12,
      });
      ensureMotion(current, canvas.clientWidth, canvas.clientHeight);
      const nowMs = current.nowMs ?? now;
      canvas.setAttribute('data-clock', 'running');
      const stepped = step(motionRef.current, {
        type: 'tick',
        nowMs,
        paused: pointerActiveRef.current,
      });
      motionRef.current = stepped.state;
      live.setWorldline(current.worldline);
      live.setReducedMotion(current.reducedMotion);
      live.setAmbientPaused(ambientHold());
      live.setDecoration(current.decoration ?? 'full');
      live.setQuery(current.query);
      live.setSelected(current.selectedId);
      live.applyFrame(stepped.frame, displayFromFrame(current.plan, stepped.frame));
      live.render(cameraFromFrame(stepped.frame, current.mode));
      publish(stepped.frame);
      canvas.setAttribute('data-ambient', ambientHold() ? 'settled' : 'pulse');
      if (!cancelled && !hiddenClockRef.current) raf = window.requestAnimationFrame(runFrame);
    };
    let frames = 0;
    let started = 0;
    const startLoop = () => {
      if (cancelled || raf || hiddenClockRef.current || documentHidden()) return;
      if (!runtimeRef.current?.supported) return;
      started = started || performance.now();
      raf = window.requestAnimationFrame(runFrame);
    };
    const onVisibility = () => {
      if (cancelled) return;
      const nowMs = clockMs();
      if (documentHidden()) applyHidden(nowMs);
      else applyVisible(nowMs);
    };
    document.addEventListener('visibilitychange', onVisibility);
    void import('./orreryRenderer').then(({ OrreryRuntime }) => {
      if (cancelled || !canvasRef.current) return;
      const runtime = new OrreryRuntime(canvas);
      runtimeRef.current = runtime;
      runtime.setSize(canvas.clientWidth, canvas.clientHeight);
      runtime.setWorldline(propsRef.current.worldline);
      runtime.setReducedMotion(propsRef.current.reducedMotion);
      runtime.setDecoration(propsRef.current.decoration ?? 'full');
      runtime.setExpanded(true);
      runtime.setQuery(propsRef.current.query);
      const initial = ensureMotion(propsRef.current, canvas.clientWidth, canvas.clientHeight);
      runtime.setSelected(propsRef.current.selectedId);
      if (initial) runtime.applyFrame(initial, displayFromFrame(propsRef.current.plan, initial));
      if (initial) publish(initial);
      window.addEventListener('resize', onWinResize);
      canvas.addEventListener('webglcontextlost', onLost);
      if (!runtime.supported) {
        propsRef.current.onCreateFail?.();
        return;
      }
      if (documentHidden()) applyHidden(clockMs());
      else startLoop();
    });
    return () => {
      cancelled = true;
      stopLoop();
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('resize', onWinResize);
      canvas.removeEventListener('webglcontextlost', onLost);
      runtimeRef.current?.dispose();
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    let next = ensureMotion(
      props,
      canvas?.clientWidth || 1280,
      canvas?.clientHeight ?? 800,
    );
    if (props.nowMs != null && motionRef.current) {
      const hidden = documentHidden();
      canvas?.setAttribute('data-clock', hidden ? 'paused' : 'running');
      if (hiddenClockRef.current !== hidden) {
        const gated = step(motionRef.current, { type: 'tick', nowMs: props.nowMs, paused: true });
        motionRef.current = gated.state;
        hiddenClockRef.current = hidden;
        next = gated.frame;
      }
      if (!hidden) {
        const stepped = step(motionRef.current, {
          type: 'tick',
          nowMs: props.nowMs,
          paused: pointerActiveRef.current,
        });
        motionRef.current = stepped.state;
        next = stepped.frame;
      }
    }
    const runtime = runtimeRef.current;
    if (runtime && next) {
      runtime.setWorldline(props.worldline);
      runtime.setReducedMotion(props.reducedMotion);
      runtime.setAmbientPaused(ambientHold());
      runtime.setDecoration(props.decoration ?? 'full');
      runtime.setQuery(props.query);
      runtime.setSelected(props.selectedId);
      runtime.applyFrame(next, displayFromFrame(props.plan, next));
    }
    if (next) publish(next);
  }, [
    props.decoration,
    props.expandedTopicIds,
    props.motionCause,
    props.motionCauses,
    props.motionEpoch,
    props.nowMs,
    props.plan,
    props.query,
    props.reducedMotion,
    props.scopeKey,
    props.selectedId,
    props.worldline,
  ]);

  return (
    <canvas
      ref={canvasRef}
      className="memory-constellation-stage"
      data-testid="constellation-stage"
      data-visible-evidence={evidenceIds(props.plan)}
      data-mode={props.mode}
      data-phase="overview"
      data-yaw={view.yaw.toFixed(3)}
      data-pitch={view.pitch.toFixed(3)}
      data-zoom={view.zoom.toFixed(2)}
      data-ownership={view.ownership}
      data-generation={String(view.generation)}
      data-clock="running"
      data-ambient={props.reducedMotion || view.ownership === 'user' || props.selectedId || hoverId || pointerActive ? 'settled' : 'pulse'}
      data-hover={hoverId ?? ''}
      aria-label="记忆星座仪"
      onPointerDown={(event) => {
        if (props.mode === '2d') return;
        if (event.button === 2) {
          setPointerHold(true);
          rightPressRef.current = { x: event.clientX, y: event.clientY, dragged: false };
          return;
        }
        if (event.button !== 0) return;
        setPointerHold(true);
        rightPressRef.current = null;
        movedRef.current = false;
        dragRef.current = { x: event.clientX, y: event.clientY };
        ensureMotion(props, canvasRef.current?.clientWidth || 1280, canvasRef.current?.clientHeight || 800);
      }}
      onPointerMove={(event) => {
        const canvas = canvasRef.current;
        const runtime = runtimeRef.current;
        ensureMotion(props, canvas?.clientWidth || 1280, canvas?.clientHeight || 800);
        if (rightPressRef.current) {
          const dx = event.clientX - rightPressRef.current.x;
          const dy = event.clientY - rightPressRef.current.y;
          if (isPointerDrag(dx, dy)) rightPressRef.current.dragged = true;
          return;
        }
        if (dragRef.current && props.mode !== '2d' && motionRef.current) {
          const dx = event.clientX - dragRef.current.x;
          const dy = event.clientY - dragRef.current.y;
          if (!movedRef.current && !isPointerDrag(dx, dy)) return;
          movedRef.current = true;
          dragRef.current = { x: event.clientX, y: event.clientY };
          const moved = step(motionRef.current, {
            type: 'userCamera',
            source: 'pointer',
            dyaw: dx * 0.008,
            dpitch: dy * 0.006,
            nowMs: clockMs(),
          });
          motionRef.current = moved.state;
          if (runtime) {
            runtime.applyFrame(moved.frame, displayFromFrame(propsRef.current.plan, moved.frame));
            runtime.render(cameraFromFrame(moved.frame, propsRef.current.mode));
          }
          publish(moved.frame);
          return;
        }
        if (!canvas || !runtime || props.mode === '2d') {
          runtime?.setHover(null);
          setHoverId(null);
          return;
        }
        const rect = canvas.getBoundingClientRect();
        const hover = pickAtClient(event.clientX, event.clientY, rect);
        runtime.setHover(hover);
        setHoverId(hover);
        const hoverChanged = hoverGateRef.current !== hover;
        hoverGateRef.current = hover;
        runtime.setAmbientPaused(ambientHold());
        if (motionRef.current && hoverChanged) {
          const gated = step(motionRef.current, {
            type: 'activityChanged',
            hovered: Boolean(hover),
            nowMs: clockMs(),
          });
          motionRef.current = gated.state;
          if (runtime) {
            runtime.applyFrame(gated.frame, displayFromFrame(propsRef.current.plan, gated.frame));
            runtime.render(cameraFromFrame(gated.frame, propsRef.current.mode));
          }
          publish(gated.frame);
        }
      }}
      onPointerUp={(event) => {
        const canvas = canvasRef.current;
        setPointerHold(false);
        if (!canvas || event.button === 2) return;
        const dragged = movedRef.current;
        const rect = canvas.getBoundingClientRect();
        const hit = pickAtClient(event.clientX, event.clientY, rect);
        dragRef.current = null;
        movedRef.current = false;
        if (dragged) return;
        if (!hit) return;
        props.onSelect(hit);
      }}
      onPointerCancel={() => {
        dragRef.current = null;
        movedRef.current = false;
        rightPressRef.current = null;
        setPointerHold(false);
      }}
      onWheel={(event) => {
        ensureMotion(props, canvasRef.current?.clientWidth || 1280, canvasRef.current?.clientHeight || 800);
        if (props.mode === '2d' || !motionRef.current) return;
        event.preventDefault();
        const factor = props.reducedMotion
          ? (event.deltaY > 0 ? 1.12 : 1 / 1.12)
          : (1 + event.deltaY * 0.0012);
        const moved = step(motionRef.current, {
          type: 'userCamera',
          source: 'wheel',
          zoomMul: factor,
          nowMs: clockMs(),
        });
        motionRef.current = moved.state;
        const runtime = runtimeRef.current;
        if (runtime) {
          runtime.applyFrame(moved.frame, displayFromFrame(props.plan, moved.frame));
          runtime.render(cameraFromFrame(moved.frame, props.mode));
        }
        publish(moved.frame);
      }}
      onPointerLeave={() => {
        dragRef.current = null;
        rightPressRef.current = null;
        movedRef.current = false;
        setPointerHold(false);
        runtimeRef.current?.setHover(null);
        setHoverId(null);
        if (motionRef.current && hoverGateRef.current) {
          hoverGateRef.current = null;
          const gated = step(motionRef.current, {
            type: 'activityChanged',
            hovered: false,
            nowMs: clockMs(),
          });
          motionRef.current = gated.state;
          publish(gated.frame);
        }
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        const press = rightPressRef.current;
        rightPressRef.current = null;
        if (press?.dragged) return;
        props.onUnwind?.();
      }}
    />
  );
}
