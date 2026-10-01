import { useEffect, useRef, useState, type RefObject } from 'react';

/** Sending is a visible act: a signal leaves the send key, runs up the seam and reaches her.
 *  Purely decorative (aria-hidden); fires once per new user line. Reduced motion hides it via CSS. */
export function SendSignal({ rootRef, userCount, lastIsUser }: {
  rootRef: RefObject<HTMLDivElement | null>;
  userCount: number;
  lastIsUser: boolean;
}) {
  const [sig, setSig] = useState<{ d: string; k: number; ex: number; ey: number } | null>(null);
  const seen = useRef(userCount);
  useEffect(() => {
    // Exactly one new user line at the tail = a send; loading a history jumps by more and stays silent.
    const grew = userCount === seen.current + 1 && lastIsUser;
    seen.current = userCount;
    const el = rootRef.current;
    if (!grew || !el) return;
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
    setSig({ d, k: Date.now(), ex: x + 46, ey });
    const t = window.setTimeout(() => setSig(null), 1300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userCount, rootRef]);
  if (!sig) return null;
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
