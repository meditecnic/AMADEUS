import { useEffect, useMemo, useRef, useState } from 'react';

import { visibleDisplayNodes } from './graphPaths';
import {
  focusLookAt,
  layoutProjection,
  pickDisplayNode,
} from './layout';
import type { OrreryCamera, OrreryRuntime } from './orreryRenderer';
import type { DecorationLevel, FrameSample } from './renderCapability';
import type { DisplayNode, MemoryProjection, Point3, Worldline } from './types';

const ORIGIN: Point3 = { x: 0, y: 0, z: 0 };
const ZOOM_MIN = 2.4;
const ZOOM_MAX = 9;
const ZOOM_DEFAULT = 6.15;

export type ScreenAnchor = { x: number; y: number };

function cameraOf(
  display: DisplayNode[],
  selectedId: string | null,
  mode: '3d' | '2d',
  yaw: number,
  pitch: number,
  zoom: number,
): OrreryCamera {
  const selected = display.find((n) => n.id === selectedId) ?? null;
  const lookAt = mode === '2d' ? ORIGIN : focusLookAt(selected);
  const distance = mode === '2d' ? 6.4 : Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoom));
  return {
    yaw: mode === '2d' ? 0 : yaw,
    pitch: mode === '2d' ? 0 : pitch,
    distance,
    lookAt,
    mode,
  };
}

function revealedDisplay(
  projection: MemoryProjection,
  expandedTopicIds: readonly string[],
  query: string,
): DisplayNode[] {
  return visibleDisplayNodes(layoutProjection(projection), projection.edges, {
    overviewOpen: true,
    expandedTopicIds,
    query,
    resultIds: projection.result_ids,
  });
}

export function ConstellationStage(props: {
  projection: MemoryProjection;
  selectedId: string | null;
  mode: '3d' | '2d';
  reducedMotion: boolean;
  decoration?: DecorationLevel;
  expanded: boolean;
  expandedTopicIds: readonly string[];
  worldline: Worldline;
  query: string;
  onSelect: (id: string | null) => void;
  onSelectedAnchor?: (anchor: ScreenAnchor | null) => void;
  onCreateFail?: () => void;
  onContextLost?: () => void;
  onFrame?: (sample: FrameSample) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const runtimeRef = useRef<OrreryRuntime | null>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const movedRef = useRef(false);
  const orbitRef = useRef({ yaw: 0.62, pitch: 0.48, zoom: ZOOM_DEFAULT });
  const propsRef = useRef(props);
  const displayRef = useRef<DisplayNode[]>([]);
  const branchRef = useRef(new Map<string, { p: number; target: number }>());
  const lastTickRef = useRef(0);
  const membershipRef = useRef('');
  const [orbit, setOrbit] = useState({ yaw: 0.62, pitch: 0.48, zoom: ZOOM_DEFAULT });
  const [revealRev, setRevealRev] = useState(0);
  const liveTopicIds = useMemo(() => {
    const ids = new Set(props.expandedTopicIds);
    branchRef.current.forEach((_, id) => ids.add(id));
    return [...ids];
  }, [props.expandedTopicIds, revealRev]);
  const display = revealedDisplay(props.projection, liveTopicIds, props.query);
  displayRef.current = display;
  propsRef.current = props;
  const cam = cameraOf(display, props.selectedId, props.mode, orbit.yaw, orbit.pitch, orbit.zoom);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    let cancelled = false;
    let frame = 0;
    const onWinResize = () => {
      runtimeRef.current?.setSize(canvas.clientWidth, canvas.clientHeight);
    };
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
      runtime.setSelected(propsRef.current.selectedId);
      runtime.setGraph(displayRef.current, propsRef.current.projection.edges, propsRef.current.projection.result_ids);
      window.addEventListener('resize', onWinResize);
      canvas.addEventListener('webglcontextlost', (event) => {
        event.preventDefault();
        propsRef.current.onContextLost?.();
      });
      if (!runtime.supported) {
        propsRef.current.onCreateFail?.();
        return;
      }
      let frames = 0;
      const started = performance.now();
      const tick = () => {
        const live = runtimeRef.current;
        const current = propsRef.current;
        if (!live) return;
        if (current.mode === '3d' && !current.reducedMotion && !dragRef.current) {
          orbitRef.current.yaw += 0.00028;
        }
        const now = performance.now();
        frames += 1;
        current.onFrame?.({
          at: now,
          visible: typeof document === 'undefined' ? true : document.visibilityState === 'visible',
          warmup: now - started < 1200 || frames < 12,
        });
        const dt = lastTickRef.current ? Math.min(0.05, (now - lastTickRef.current) / 1000) : 0;
        lastTickRef.current = now;
        const want = new Set(current.expandedTopicIds);
        const branches = branchRef.current;
        for (const id of want) {
          const existing = branches.get(id);
          if (!existing) branches.set(id, { p: current.reducedMotion ? 1 : 0, target: 1 });
          else existing.target = 1;
        }
        for (const [id, state] of branches) {
          if (!want.has(id)) state.target = 0;
        }
        const speed = 1 / 0.45;
        for (const state of branches.values()) {
          if (current.reducedMotion) state.p = state.target;
          else if (state.p < state.target) state.p = Math.min(1, state.p + dt * speed);
          else if (state.p > state.target) state.p = Math.max(0, state.p - dt * speed);
        }
        for (const [id, state] of [...branches]) {
          if (state.target === 0 && state.p <= 0) branches.delete(id);
        }
        const membership = [...branches.keys()].sort().join('|');
        if (membership !== membershipRef.current) {
          membershipRef.current = membership;
          setRevealRev((value) => value + 1);
        }
        const progress: Record<string, number> = {};
        branches.forEach((state, id) => {
          progress[id] = state.p;
        });
        live.setBranchProgress(progress);
        const next = cameraOf(
          displayRef.current,
          current.selectedId,
          current.mode,
          orbitRef.current.yaw,
          orbitRef.current.pitch,
          orbitRef.current.zoom,
        );
        live.setSelected(current.selectedId);
        live.render(next);
        current.onSelectedAnchor?.(
          current.selectedId ? live.project(current.selectedId) : null,
        );
        frame = window.requestAnimationFrame(tick);
      };
      frame = window.requestAnimationFrame(tick);
    });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', onWinResize);
      runtimeRef.current?.dispose();
      runtimeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const nextDisplay = revealedDisplay(props.projection, liveTopicIds, props.query);
    displayRef.current = nextDisplay;
    runtime.setWorldline(props.worldline);
    runtime.setReducedMotion(props.reducedMotion);
    runtime.setDecoration(props.decoration ?? 'full');
    runtime.setExpanded(true);
    runtime.setQuery(props.query);
    runtime.setSelected(props.selectedId);
    runtime.setGraph(nextDisplay, props.projection.edges, props.projection.result_ids);
  }, [liveTopicIds, props.projection, props.query, props.reducedMotion, props.selectedId, props.worldline]);

  return (
    <canvas
      ref={canvasRef}
      className="memory-constellation-stage"
      data-testid="constellation-stage"
      data-mode={props.mode}
      data-phase="overview"
      data-yaw={cam.yaw.toFixed(3)}
      data-pitch={cam.pitch.toFixed(3)}
      data-zoom={cam.distance.toFixed(2)}
      aria-label="记忆星座仪"
      onPointerDown={(event) => {
        if (props.mode === '2d') return;
        movedRef.current = false;
        dragRef.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerMove={(event) => {
        const canvas = canvasRef.current;
        const runtime = runtimeRef.current;
        if (dragRef.current && props.mode !== '2d') {
          const dx = event.clientX - dragRef.current.x;
          const dy = event.clientY - dragRef.current.y;
          if (Math.abs(dx) + Math.abs(dy) > 2) movedRef.current = true;
          dragRef.current = { x: event.clientX, y: event.clientY };
          orbitRef.current = {
            ...orbitRef.current,
            yaw: orbitRef.current.yaw + dx * 0.008,
            pitch: Math.max(-0.9, Math.min(0.9, orbitRef.current.pitch + dy * 0.006)),
          };
          setOrbit({ ...orbitRef.current });
          return;
        }
        if (!canvas || !runtime || props.mode === '2d') {
          runtime?.setHover(null);
          return;
        }
        const rect = canvas.getBoundingClientRect();
        const fromGl = runtime.supported ? runtime.pick(event.clientX, event.clientY, rect) : null;
        runtime.setHover(fromGl);
      }}
      onPointerUp={(event) => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const dragged = movedRef.current;
        const rect = canvas.getBoundingClientRect();
        const runtime = runtimeRef.current;
        const fromGl = runtime?.supported
          ? runtime.pick(event.clientX, event.clientY, rect)
          : null;
        const hit = fromGl
          ? display.find((node) => node.id === fromGl) ?? null
          : pickDisplayNode(
              display,
              event.clientX - rect.left,
              event.clientY - rect.top,
              canvas.clientWidth,
              canvas.clientHeight,
              cam.yaw,
              cam.pitch,
              cam.distance,
              cam.lookAt,
            );
        dragRef.current = null;
        movedRef.current = false;
        if (dragged) return;
        props.onSelect(hit ? hit.id : null);
      }}
      onWheel={(event) => {
        if (props.mode === '2d') return;
        event.preventDefault();
        const factor = props.reducedMotion
          ? (event.deltaY > 0 ? 1.12 : 1 / 1.12)
          : (1 + event.deltaY * 0.0012);
        const next = Math.max(
          ZOOM_MIN,
          Math.min(ZOOM_MAX, orbitRef.current.zoom * factor),
        );
        orbitRef.current = { ...orbitRef.current, zoom: next };
        setOrbit({ ...orbitRef.current });
      }}
      onPointerLeave={() => {
        dragRef.current = null;
        runtimeRef.current?.setHover(null);
      }}
    />
  );
}
