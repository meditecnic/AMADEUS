// @refresh reset
// Opaque motion handles and WebGL resources belong to the loaded module instance.
import { useEffect, useRef, useState } from 'react';

import { layoutProjection, OVERVIEW_FOV_DEG, OVERVIEW_REST_CAMERA } from './layout';
import {
  createOverviewMotion,
  pickLiveNode,
  restLayoutFromProjection,
  screenHitRadiusPx,
  step,
  type MotionState,
  type OverviewFrame,
  type PresentationChangeCause,
} from './overviewMotion';
import { displayNodesFromPlan, type OverviewDisclosurePlan } from './overviewPresentation';
import type { OrreryCamera, OrreryRuntime } from './orreryRenderer';
import type { DecorationLevel, FrameSample } from './renderCapability';
import { perfSampleVisible } from './renderCapability';
import { isPointerDrag } from './pointerIntent';
import type { DisplayNode, Worldline } from './types';

export type ScreenAnchor = { x: number; y: number; radius: number };

function cameraFromFrame(frame: OverviewFrame): OrreryCamera {
  return {
    yaw: frame.camera.yaw,
    pitch: frame.camera.pitch,
    distance: frame.camera.distance,
    lookAt: frame.camera.lookAt,
    mode: '3d',
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
    renderSurface: '3d' as const,
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
  soulOpen?: boolean;
  soulReading?: boolean;
  onSoulRead?: () => void;
  plan: OverviewDisclosurePlan;
  selectedId: string | null;
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
  /** P1 (round 5): visibility/focus recovery; the parent must clear its
   *  full FPS sampling window on this boundary. */
  onPerfBaselineReset?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const runtimeRef = useRef<OrreryRuntime | null>(null);
  useEffect(() => { runtimeRef.current?.soulOpening.setOpen(Boolean(props.soulOpen)); }, [props.soulOpen]);
  useEffect(() => { if (runtimeRef.current) runtimeRef.current.soulOpening.reading = Boolean(props.soulReading); }, [props.soulReading]);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const rightPressRef = useRef<{ x: number; y: number; dragged: boolean } | null>(null);
  const movedRef = useRef(false);
  const hoverGateRef = useRef<string | null>(null);
  const pointerActiveRef = useRef(false);
  const hiddenClockRef = useRef(false);
  const propsRef = useRef(props);

  const documentHidden = () => typeof document !== 'undefined' && document.visibilityState !== 'visible';
  const windowFocused = () => typeof document === 'undefined' || typeof document.hasFocus !== 'function' || document.hasFocus();
  const clockMs = () => propsRef.current.nowMs ?? (typeof performance !== 'undefined' ? performance.now() : 0);
  const ambientHold = () => (
    documentHidden()
    || propsRef.current.reducedMotion
  );
  const motionRef = useRef<MotionState | null>(null);
  const frameRef = useRef<OverviewFrame | null>(null);
  const epochRef = useRef(props.motionEpoch ?? 0);
  const scopeRef = useRef(props.scopeKey ?? '');
  const [view, setView] = useState({
    yaw: Number(OVERVIEW_REST_CAMERA.yaw),
    pitch: Number(OVERVIEW_REST_CAMERA.pitch),
    zoom: Number(OVERVIEW_REST_CAMERA.distance),
    ownership: 'rest' as OverviewFrame['ownership'],
    generation: 1,
  });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [labels, setLabels] = useState<Array<{ id: string; text: string; x: number; y: number; hot: boolean }>>([]);
  const labelTickRef = useRef(0);
  propsRef.current = props;
  if (!motionRef.current) {
    const created = step(
      createOverviewMotion(seedFromProps(props, 1280, 800)),
      { type: 'tick', nowMs: clockMs(), paused: true },
    );
    motionRef.current = created.state;
    frameRef.current = created.frame;
  }

  const lastPublishedRef = useRef<OverviewFrame | null>(null);
  const publish = (next: OverviewFrame) => {
    frameRef.current = next;
    // P1-A (round 4): an identical frame must not re-run the callback chain.
    // Effect re-runs that publish the frame they already published become
    // no-ops instead of re-projecting anchors into the parent.
    if (next === lastPublishedRef.current) return;
    lastPublishedRef.current = next;
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
    const liveNode = selected ? next.nodes.get(selected) : null;
    const projectedPoint = fromRuntime ?? (fromFrame ? { x: fromFrame.x, y: fromFrame.y } : null);
    const projected = projectedPoint && liveNode
      ? {
        ...projectedPoint,
        radius: screenHitRadiusPx(liveNode, next.camera, next.viewport) / 1.45,
      }
      : null;
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
        runtime.render(cameraFromFrame(gated.frame));
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
    let resizeObserver: ResizeObserver | undefined;
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
        live.render(cameraFromFrame(frame));
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
      // P1-B (round 4): returning from a throttled state re-establishes the
      // FPS baseline; warmup restarts so throttled samples cannot demote.
      // P1 (round 5): the parent's full sampling window is cleared on the
      // same boundary.
      frames = 0;
      started = 0;
      propsRef.current.onPerfBaselineReset?.();
      if (hiddenClockRef.current && motionRef.current) {
        const resumed = step(motionRef.current, { type: 'tick', nowMs, paused: true });
        motionRef.current = resumed.state;
        publish(resumed.frame);
      }
      hiddenClockRef.current = false;
      runtimeRef.current?.setAmbientPaused(ambientHold());
      startLoop();
    };
    const onFocusGained = () => {
      if (cancelled) return;
      frames = 0;
      started = 0;
      // P1 (round 5): regaining focus restarts the sampling window upstream.
      propsRef.current.onPerfBaselineReset?.();
    };
    const runFrame = (now: number) => {
      raf = 0;
      const live = runtimeRef.current;
      const current = propsRef.current;
      if (cancelled || !live || !motionRef.current || hiddenClockRef.current) return;
      frames += 1;
      current.onFrame?.({
        at: now,
        // P1-B (round 4): hidden OR unfocused time never counts toward the
        // low-FPS judgment; rAF is system-throttled in both states.
        visible: perfSampleVisible(document.visibilityState, windowFocused()),
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
      live.render(cameraFromFrame(stepped.frame));
      publish(stepped.frame);
      labelTickRef.current += 1;
      if (labelTickRef.current % 3 === 0) {
        const display = displayFromFrame(current.plan, stepped.frame);
        const nextLabels: Array<{ id: string; text: string; x: number; y: number; hot: boolean }> = [];
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        const hub = display.find((node) => node.kind === 'continuity_hub');
        const soulAt = hub ? live.project(hub.id) : { x: width * 0.5, y: height * 0.5 };
        const cam = stepped.frame.camera;
        const cp = Math.cos(cam.pitch);
        const camX = cam.lookAt.x - cam.distance * cp * Math.sin(cam.yaw);
        const camY = cam.lookAt.y - cam.distance * Math.sin(cam.pitch);
        const camZ = cam.lookAt.z - cam.distance * cp * Math.cos(cam.yaw);
        const fov = (OVERVIEW_FOV_DEG * Math.PI) / 180;
        const pxAt = (pos: { x: number; y: number; z: number }, radius: number) => {
          const dist = Math.hypot(pos.x - camX, pos.y - camY, pos.z - camZ);
          return dist > 0.001 ? ((height / 2) / Math.tan(fov / 2) / dist) * radius : 12;
        };
        const soulLive = hub ? stepped.frame.nodes.get(hub.id) : undefined;
        const soulR = soulAt && soulLive ? pxAt(soulLive.position, soulLive.hitRadius) : 80;
        for (const node of display) {
          if (node.kind === 'continuity_hub') continue;
          const record = node.kind === 'fact' || node.kind === 'experience';
          if (record && node.id !== current.selectedId && node.id !== hoverGateRef.current) continue;
          const at = live.project(node.id);
          if (!at) continue;
          if (at.x < -48 || at.y < -48 || at.x > width + 48 || at.y > height + 48) continue;
          if (soulAt && Math.hypot(at.x - soulAt.x, at.y - soulAt.y) < soulR * 0.9) continue;
          const liveNode = stepped.frame.nodes.get(node.id);
          const pos = liveNode?.position ?? node.position;
          const lift = pxAt(pos, liveNode?.hitRadius ?? node.radius) + 10;
          let x = at.x;
          let y = at.y - lift;
          if (soulAt) {
            const dx = x - soulAt.x;
            const dy = y - soulAt.y;
            const d = Math.hypot(dx, dy);
            const minD = soulR + 26;
            if (d < minD && d > 0.001) {
              const s = minD / d;
              x = soulAt.x + dx * s;
              y = soulAt.y + dy * s;
            }
          }
          nextLabels.push({
            id: node.id,
            text: node.label,
            x,
            y,
            hot: node.id === current.selectedId || node.id === hoverGateRef.current,
          });
        }
        nextLabels.sort((a, b) => a.x - b.x);
        for (let i = 1; i < nextLabels.length; i += 1) {
          const prev = nextLabels[i - 1];
          const cur = nextLabels[i];
          if (Math.abs(cur.x - prev.x) < 96 && Math.abs(cur.y - prev.y) < 20) cur.y = prev.y + 20;
        }
        setLabels(nextLabels);
      }
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
    window.addEventListener('focus', onFocusGained);
    void import('./orreryRenderer').then(({ OrreryRuntime }) => {
      if (cancelled || !canvasRef.current) return;
      const runtime = new OrreryRuntime(canvas);
      runtimeRef.current = runtime;
      runtime.setSize(canvas.clientWidth, canvas.clientHeight);
      // Layout can settle after switching views or opening a narrow sheet,
      // without a window resize. Keep the drawing buffer in sync with it.
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(onWinResize);
        resizeObserver.observe(canvas);
      }
      runtime.setWorldline(propsRef.current.worldline);
      runtime.soulOpening.setOpen(Boolean(propsRef.current.soulOpen));
      runtime.soulOpening.reading = Boolean(propsRef.current.soulReading);
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
      window.removeEventListener('focus', onFocusGained);
      window.removeEventListener('resize', onWinResize);
      resizeObserver?.disconnect();
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

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent) => {
      const current = propsRef.current;
      ensureMotion(current, canvas.clientWidth || 1280, canvas.clientHeight || 800);
      if (!motionRef.current) return;
      event.preventDefault();
      const factor = current.reducedMotion
        ? (event.deltaY > 0 ? 1.12 : 1 / 1.12)
        : (1 + event.deltaY * 0.0012);
      if (runtimeRef.current?.soulOpening.open || runtimeRef.current?.soulOpening.progress) {
        runtimeRef.current.soulOpening.dolly(factor);
        return;
      }
      const moved = step(motionRef.current, {
        type: 'userCamera', source: 'wheel', zoomMul: factor, nowMs: clockMs(),
      });
      motionRef.current = moved.state;
      const runtime = runtimeRef.current;
      if (runtime) {
        runtime.applyFrame(moved.frame, displayFromFrame(current.plan, moved.frame));
        runtime.render(cameraFromFrame(moved.frame));
      }
      publish(moved.frame);
    };
    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', onWheel);
  }, []);

  return (
    <>
    <canvas
      ref={canvasRef}
      className="memory-constellation-stage"
      data-testid="constellation-stage"
      data-visible-evidence={evidenceIds(props.plan)}
      data-mode="3d"
      data-phase="overview"
      data-yaw={view.yaw.toFixed(3)}
      data-pitch={view.pitch.toFixed(3)}
      data-zoom={view.zoom.toFixed(2)}
      data-ownership={view.ownership}
      data-generation={String(view.generation)}
      data-clock="running"
      data-ambient={props.reducedMotion ? 'settled' : 'pulse'}
      data-hover={hoverId ?? ''}
      aria-label="记忆星座仪"
      onPointerDown={(event) => {
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
        if (dragRef.current && motionRef.current) {
          const dx = event.clientX - dragRef.current.x;
          const dy = event.clientY - dragRef.current.y;
          if (!movedRef.current && !isPointerDrag(dx, dy)) return;
          movedRef.current = true;
          dragRef.current = { x: event.clientX, y: event.clientY };
          if (runtime?.soulOpening.open || runtime?.soulOpening.progress) {
            runtime.soulOpening.orbit(dx, dy);
            return;
          }
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
            runtime.render(cameraFromFrame(moved.frame));
          }
          publish(moved.frame);
          return;
        }
        if (!canvas || !runtime) {
          runtime?.setHover(null);
          setHoverId(null);
          return;
        }
        const rect = canvas.getBoundingClientRect();
        if (runtime.soulOpening.open || runtime.soulOpening.progress) {
          canvas.style.cursor = runtime.pickSoulCore(event.clientX, event.clientY, rect) ? 'pointer' : 'grab';
          return;
        }
        canvas.style.cursor = '';
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
            runtime.render(cameraFromFrame(gated.frame));
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
        if (runtimeRef.current?.soulOpening.open || runtimeRef.current?.soulOpening.progress) {
          dragRef.current = null; movedRef.current = false;
          if (!dragged && runtimeRef.current.soulOpening.open && runtimeRef.current.pickSoulCore(event.clientX, event.clientY, rect)) props.onSoulRead?.();
          return;
        }
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
        event.stopPropagation();
        const press = rightPressRef.current;
        rightPressRef.current = null;
        setPointerHold(false);
        if (press?.dragged) return;
        props.onUnwind?.();
      }}
    />
    <div className="memory-constellation-labels" aria-hidden="true">
      {labels.map((label) => (
        <span
          key={label.id}
          className={label.hot ? 'memory-constellation-label is-hot' : 'memory-constellation-label'}
          style={{ left: `${label.x}px`, top: `${label.y}px` }}
        >
          {label.text}
        </span>
      ))}
    </div>
    </>
  );
}
