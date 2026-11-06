import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';

/**
 * The wires her signal travels on (design/references.md §2.3, §2.7).
 *
 * Routes are measured from anchors inside the root (`data-hw="composer|core|transcript|meter|stage|header"`), drawn
 * as 45° traces with solder points, and carry a light pulse only when something real happens:
 *   send  — your words leave the transmit panel and reach her;
 *   reply — a sentence of hers lands in the reading column;
 *   meter — the divergence meter feeds the stage (worldline settled).
 * Between events a wire only holds a dim ember drifting along it (ambient, < 0.5 Hz).
 */
export type WireRoute = 'send' | 'reply' | 'meter';
export type WireHeat = 'idle' | 'thinking' | 'replying' | 'offline';

interface Props {
  rootRef: RefObject<HTMLElement | null>;
  pulse: { key: string | number; route: WireRoute } | null;
  heat: WireHeat;
  /** Your words are with her (local send until the turn completes): the send wire stays warm. */
  sending?: boolean;
  reduced?: boolean;
  /** Changes whenever anchors may have moved (pack on/off, rail toggled). */
  layoutKey?: string;
}

type Pt = [number, number];
interface Wire { id: string; d: string; dots: Pt[]; kind: WireRoute | 'trace' }

const CHAMFER = 12;

/** Polyline with every corner cut at 45°. */
function chamfered(points: Pt[]): string {
  if (points.length < 2) return '';
  let d = `M${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`;
  for (let i = 1; i < points.length - 1; i += 1) {
    const [px, py] = points[i - 1];
    const [x, y] = points[i];
    const [nx, ny] = points[i + 1];
    const inLen = Math.hypot(x - px, y - py) || 1;
    const outLen = Math.hypot(nx - x, ny - y) || 1;
    const c = Math.min(CHAMFER, inLen / 2, outLen / 2);
    const ax = x - ((x - px) / inLen) * c;
    const ay = y - ((y - py) / inLen) * c;
    const bx = x + ((nx - x) / outLen) * c;
    const by = y + ((ny - y) / outLen) * c;
    d += ` L${ax.toFixed(1)} ${ay.toFixed(1)} L${bx.toFixed(1)} ${by.toFixed(1)}`;
  }
  const last = points[points.length - 1];
  d += ` L${last[0].toFixed(1)} ${last[1].toFixed(1)}`;
  return d;
}

function measure(root: HTMLElement): { wires: Wire[]; w: number; h: number } | null {
  const rb = root.getBoundingClientRect();
  if (!rb.width || !rb.height) return null;
  const box = (name: string) => {
    const el = root.querySelector<HTMLElement>(`[data-hw="${name}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) return null;
    return { l: r.left - rb.left, t: r.top - rb.top, r: r.right - rb.left, b: r.bottom - rb.top, w: r.width, h: r.height };
  };
  const composer = box('composer');
  const core = box('core');
  const transcript = box('transcript');
  const meter = box('meter');
  const stage = box('stage');
  const header = box('header');
  const wires: Wire[] = [];
  if (composer && core && stage) {
    const kx = core.l + core.w / 2;
    const ky = core.t + core.h / 2;
    const ring = Math.min(core.w, core.h) * 0.3 * 1.42;
    const y0 = composer.t + Math.min(34, composer.h / 2);
    const sx = Math.max(stage.l + 26, composer.r + 18);
    const endX = kx - ring;
    const climb = Math.max(0, y0 - ky);
    const diag = Math.min(climb, Math.max(0, endX - sx));
    const pts: Pt[] = [[composer.r, y0], [sx, y0], [sx, ky + diag], [sx + diag, ky], [endX, ky]];
    wires.push({ id: 'send', kind: 'send', d: chamfered(pts), dots: [pts[0], pts[pts.length - 1]] });
  }
  if (core && transcript && stage) {
    const kx = core.l + core.w / 2;
    const ky = core.t + core.h / 2;
    const ring = Math.min(core.w, core.h) * 0.3 * 1.42;
    const ex = kx - ring * 0.94;
    const ey = ky - ring * 0.34;
    const hx = stage.l + 12;
    const tx = transcript.r + 6;
    const ty = Math.max(transcript.t + 60, transcript.b - 150);
    const drop = Math.max(0, ty - ey);
    const run = Math.max(0, hx - tx);
    const diag = Math.min(drop, run);
    const pts: Pt[] = [[ex, ey], [hx, ey], [hx - diag, ey + diag], [tx, ey + diag], [tx, ty], [tx - 10, ty]];
    wires.push({ id: 'reply', kind: 'reply', d: chamfered(pts), dots: [pts[0], pts[pts.length - 1]] });
  }
  if (meter && core) {
    const kx = core.l + core.w / 2;
    const ky = core.t + core.h / 2;
    const ring = Math.min(core.w, core.h) * 0.3 * 1.42;
    const mx = meter.l + meter.w / 2;
    const my = meter.b + 4;
    const top = ky - ring * 1.16;
    const mid = my + 22;
    const dx = kx - mx;
    const diag = Math.min(Math.abs(dx), Math.max(0, top - mid - 10));
    const sign = Math.sign(dx) || 1;
    const pts: Pt[] = [[mx, my], [mx, mid], [mx + sign * diag, mid + diag], [kx, mid + diag], [kx, top]];
    wires.push({ id: 'meter', kind: 'meter', d: chamfered(pts), dots: [pts[0], pts[pts.length - 1]] });
  }
  if (header) {
    // The header's lower rail: a trace with breaks and pads, the instrument panel's seam.
    const y = header.b - 0.5;
    const right = stage ? stage.l - 18 : header.r - 18;
    const pts: Pt[] = [[18, y], [right, y], [right + 9, y + 9], [right + 9, y + 44]];
    wires.push({ id: 'rail', kind: 'trace', d: chamfered(pts), dots: [pts[0], [right * 0.42, y], pts[pts.length - 1]] });
  }
  if (transcript && composer) {
    const x = Math.max(8, transcript.l - 18);
    const pts: Pt[] = [[x, transcript.t + 24], [x, composer.t - 30], [x + 14, composer.t - 16]];
    wires.push({ id: 'spine', kind: 'trace', d: chamfered(pts), dots: [pts[0], pts[2]] });
  }
  return { wires, w: rb.width, h: rb.height };
}

export function HotWires({ rootRef, pulse, heat, sending = false, reduced = false, layoutKey }: Props) {
  const [geo, setGeo] = useState<{ wires: Wire[]; w: number; h: number } | null>(null);
  const pulseRefs = useRef<Record<string, SVGPathElement | null>>({});
  const [lit, setLit] = useState<Record<string, number>>({});

  const remeasure = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const next = measure(root);
    setGeo((prev) => (prev && next && prev.w === next.w && prev.h === next.h && prev.wires.map((w) => w.d).join() === next.wires.map((w) => w.d).join() ? prev : next));
  }, [rootRef]);

  useLayoutEffect(() => {
    remeasure();
    const root = rootRef.current;
    if (!root || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => remeasure());
    ro.observe(root);
    root.querySelectorAll('[data-hw]').forEach((el) => ro.observe(el));
    // Grid tracks animate (rail toggle); measure again once they settle.
    const late = window.setTimeout(remeasure, 450);
    return () => { ro.disconnect(); window.clearTimeout(late); };
  }, [remeasure, rootRef, layoutKey]);

  // Anchors can mount after the wires do (session still connecting); every real event measures again first.
  useLayoutEffect(() => { remeasure(); }, [remeasure, pulse?.key, sending, heat]);

  useEffect(() => {
    if (!pulse) return;
    const id = pulse.route;
    setLit((m) => ({ ...m, [id]: (m[id] ?? 0) + 1 }));
    const off = window.setTimeout(() => setLit((m) => { const n = { ...m }; delete n[id]; return n; }), reduced ? 650 : 900);
    const path = pulseRefs.current[id];
    if (!reduced && path && typeof path.animate === 'function' && typeof path.getTotalLength === 'function') {
      const len = path.getTotalLength();
      const dash = Math.min(150, Math.max(70, len * 0.22));
      path.style.strokeDasharray = `${dash} ${len + dash}`;
      path.animate(
        [{ strokeDashoffset: dash, opacity: 1 }, { strokeDashoffset: -len, opacity: 1 }],
        { duration: Math.max(260, (len / 1400) * 1000), easing: 'cubic-bezier(.3,0,.2,1)', fill: 'forwards' },
      );
    }
    return () => window.clearTimeout(off);
    // The key identifies one event; the route is part of it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulse?.key]);

  if (!geo) return null;
  return (
    <svg className="hw-wires" data-heat={heat} viewBox={`0 0 ${geo.w} ${geo.h}`} width={geo.w} height={geo.h} aria-hidden="true" focusable="false">
      {geo.wires.map((wire) => (
        <g key={wire.id} className={`hw-wire is-${wire.kind}${lit[wire.id] ? ' is-lit' : ''}${sending && wire.kind === 'send' ? ' is-carrying' : ''}`}>
          <path className="hw-wire-base" d={wire.d} />
          {!reduced && <path className="hw-wire-ember" d={wire.d} pathLength={1000} />}
          {wire.kind !== 'trace' && (
            <path className="hw-wire-pulse" d={wire.d} ref={(el) => { pulseRefs.current[wire.id] = el; }} />
          )}
          {wire.dots.map(([x, y], i) => <circle key={i} className="hw-wire-pad" cx={x} cy={y} r={2.6} />)}
        </g>
      ))}
    </svg>
  );
}
