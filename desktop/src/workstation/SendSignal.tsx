import { useEffect, useState, type RefObject } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';

/** Sending is a visible act: a signal leaves the send key, runs up the seam and reaches her.
 *  Purely decorative (aria-hidden); the transport supplies the actual local send identity. */
export function SendSignal({ rootRef, sendId }: {
  rootRef: RefObject<HTMLDivElement | null>;
  sendId: string | null;
}) {
  const reducedMotion = useReducedMotion();
  const [sig, setSig] = useState<{ d: string; k: string; ex: number; ey: number } | null>(null);
  useEffect(() => {
    setSig(null);
    const el = rootRef.current;
    if (!sendId || reducedMotion || !el) return;
    const o = el.getBoundingClientRect();
    const send = el.querySelector('.ws2-send')?.getBoundingClientRect();
    const stage = el.querySelector('.ws2-stage')?.getBoundingClientRect();
    if (!send || !stage || !send.width) return;
    const sx = send.right - o.left + 2;
    const sy = send.top + send.height / 2 - o.top;
    const x = stage.left - o.left;
    const ey = stage.top - o.top + stage.height * 0.4;
    const c = 10;
    const d = `M${sx} ${sy}H${x - c}L${x} ${sy - c}V${ey + c}L${x + c} ${ey}H${x + 46}`;
    setSig({ d, k: sendId, ex: x + 46, ey });
    const t = window.setTimeout(() => setSig(null), 1300);
    return () => window.clearTimeout(t);
  }, [sendId, rootRef, reducedMotion]);
  if (!sig || sig.k !== sendId || reducedMotion) return null;
  return (
    <div className="ws2-signal" key={sig.k} aria-hidden="true">
      <svg className="ws2-signal-layer">
        <path d={sig.d} className="ws2-signal-trail" pathLength={1} />
        <circle cx={sig.ex} cy={sig.ey} r="4" className="ws2-signal-hit" />
      </svg>
      <i className="ws2-signal-dot" style={{ offsetPath: `path('${sig.d}')` }} />
    </div>
  );
}
