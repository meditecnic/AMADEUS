import React, { useLayoutEffect, useRef, useState } from 'react';
import './parts.css';

/*
 * Field parts of the drafting sheet, shared by the login and the workstation's composer so the two are one
 * instrument: a nameplate on the field, a ruler under it and the round key that sends it. Styles live with each
 * screen (.lp-*, .sl-ruler / .ws2-ruler, .sl-go / .ws2-send).
 */

const CUT = 9;

/**
 * A field label's nameplate: a drafted plate with a cut corner and an engraved inner rule, printed
 * slightly off register. Measured to the label so every line lands on a whole pixel. On focus the edge is drawn
 * first, then the plate is inked in. `press` names a letterpress filter (see <PressFilter>).
 */
export function Plate({ press }: { press?: string }) {
  const ref = useRef<SVGSVGElement>(null);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  useLayoutEffect(() => {
    const host = ref.current?.parentElement;
    if (!host) return;
    const read = () => {
      const w = host.offsetWidth;
      const h = host.offsetHeight;
      if (w && h) setBox((b) => (b && b.w === w && b.h === h ? b : { w, h }));
    };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(host);
    return () => ro.disconnect();
  }, []);
  const w = box?.w ?? 0;
  const h = box?.h ?? 0;
  const shape = `M${CUT} 0H${w}V${h}H0V${CUT}Z`;
  const i = 2.5;
  const inner = `M${CUT + 1} ${i}H${w - i}V${h - i}H${i}V${CUT + 1}Z`;
  return (
    <svg ref={ref} className="lp" width={w} height={h} aria-hidden="true" focusable="false">
      {box && (
        <>
          <path className="lp-reg r" d={shape} transform="translate(-1 0)" />
          <path className="lp-reg b" d={shape} transform="translate(1 .5)" />
          <path className="lp-fill" d={shape} filter={press ? `url(#${press})` : undefined} />
          <path className="lp-inner" d={inner} />
          <path className="lp-ghost" d={shape} />
          <path className="lp-edge" d={shape} pathLength={1} />
        </>
      )}
    </svg>
  );
}

/** A ruler under a field: one division per character (up to `max`), the nib at the end of the measurement. */
export function Ruler({ n, max = 48, className = 'sl-ruler' }: { n: number; max?: number; className?: string }) {
  return (
    <span className={className} aria-hidden="true" style={{ '--n': Math.min(n, max) } as React.CSSProperties}>
      {n > 0 && <i key={n} className="fresh" />}
      <b className="nib" />
    </span>
  );
}

/**
 * The round key: a dial of ticks, a ring, an arrow; a check is drawn in when it succeeds. The dial of ticks is a
 * drawing of its own in a box of its own (.bezel), so it can be turned as a whole by the compositor while the page
 * behind the key is busy (the login turns it while the workstation is being opened).
 */
export function KeyGlyph() {
  return (
    <>
      <span className="bezel" aria-hidden="true">
        <svg viewBox="0 0 64 64" aria-hidden="true">
          {/* two trailing arcs on the halo, brightest at the head, as the compass arm on the login sheet drags */}
          <circle cx="32" cy="32" r="30.5" className="trail t1" pathLength={1} />
          <circle cx="32" cy="32" r="30.5" className="trail t2" pathLength={1} />
          <g className="ticks">
            {Array.from({ length: 24 }, (_, i) => <path key={i} d={`M32 4.2v${i % 6 === 0 ? 4.2 : 2.4}`} transform={`rotate(${i * 15} 32 32)`} />)}
          </g>
        </svg>
      </span>
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <circle cx="32" cy="32" r="30.5" className="halo" />
        <circle cx="32" cy="32" r="23" className="ring" pathLength={1} />
        <circle cx="32" cy="32" r="23" className="disc" />
        <path d="M23.5 32h16M34 26.5l5.5 5.5-5.5 5.5" className="arrow" />
        <path d="M24 32.5l5.5 5.5 11-12" className="check" pathLength={1} />
      </svg>
    </>
  );
}

/** Letterpress: a little ink spread at the edges and paper grain through the ink. The host class takes it out of flow. */
export function PressFilter({ id, className }: { id: string; className: string }) {
  return (
    <svg className={className} width="0" height="0" aria-hidden="true" focusable="false">
      <filter id={id} x="-4%" y="-20%" width="108%" height="140%" colorInterpolationFilters="sRGB">
        <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves={2} seed={7} result="n" />
        <feDisplacementMap in="SourceGraphic" in2="n" scale={1.3} xChannelSelector="R" yChannelSelector="G" result="d" />
        <feGaussianBlur in="d" stdDeviation={0.3} result="s" />
        <feColorMatrix in="n" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0.3 0 0 0 0.74" result="g" />
        <feComposite in="s" in2="g" operator="in" />
      </filter>
    </svg>
  );
}
