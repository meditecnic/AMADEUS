import { forwardRef, useImperativeHandle, useRef, type CSSProperties } from 'react';
import { WORD_CAP, WORD_GLYPHS, WORD_INK } from './wordmarkGlyphs';

/** User space of the wordmark: glyph units, baseline at y = 0, room above for the tag. */
const VB = { x: -60, y: -930, w: 6260, h: 1010 };
/** The drafting pen crosses the word once: from just before the first stem to just past the last terminal. */
const PEN = { from: -320, to: 6460, start: 0.18, dur: 1.3 };

export interface WordmarkHandle {
  /** Viewport position of the A's origin on the baseline, and px per glyph unit. */
  measure: () => { x: number; y: number; unit: number } | null;
  el: () => SVGSVGElement | null;
}

const at = (x: number) => PEN.start + (PEN.dur * (x - PEN.from)) / (PEN.to - PEN.from);

export const Wordmark = forwardRef<WordmarkHandle, { className?: string }>(function Wordmark({ className = '' }, ref) {
  const svgRef = useRef<SVGSVGElement>(null);
  useImperativeHandle(ref, () => ({
    el: () => svgRef.current,
    measure: () => {
      const svg = svgRef.current;
      if (!svg) return null;
      const r = svg.getBoundingClientRect();
      if (!r.width) return null;
      const unit = r.width / VB.w;
      return { x: r.left - VB.x * unit, y: r.top - VB.y * unit, unit };
    },
  }), []);

  return (
    <svg
      ref={svgRef}
      className={`wm ${className}`}
      viewBox={`${VB.x} ${VB.y} ${VB.w} ${VB.h}`}
      role="img"
      aria-label="AMADEUS"
      style={{ '--pen-start': `${PEN.start}s`, '--pen-dur': `${PEN.dur}s`, '--pen-from': `${PEN.from}px`, '--pen-to': `${PEN.to}px` } as CSSProperties}
    >
      {/* construction: baseline from the left edge of the sheet, cap line back from the right */}
      <g className="wm-guides" aria-hidden="true">
        <line className="g-base" x1={-14000} x2={WORD_INK.x1 + 260} y1={0} y2={0} />
        <line className="g-cap" x1={WORD_INK.x0 - 260} x2={20000} y1={-WORD_CAP} y2={-WORD_CAP} />
        {WORD_GLYPHS.map((g, i) => (
          <line key={i} className="g-side" x1={g.x0} x2={g.x0} y1={-WORD_CAP - 150} y2={110} style={{ '--t': `${at(g.x0).toFixed(3)}s` } as CSSProperties} />
        ))}
      </g>
      <g className="wm-ink" aria-hidden="true">
        {WORD_GLYPHS.map((g, i) => (
          <g key={i} className="wm-glyph" transform={`translate(${g.x} 0)`} style={{ '--t': `${at(g.x0).toFixed(3)}s` } as CSSProperties}>
            <path className="o" d={g.d} pathLength={1} />
            <path className="f" d={g.d} />
          </g>
        ))}
      </g>
      <text className="wm-tag" x={WORD_INK.x1} y={-WORD_CAP - 120} textAnchor="end" aria-hidden="true">Observation Terminal</text>
      <g className="wm-pen" aria-hidden="true">
        <line x1={0} x2={0} y1={-16000} y2={16000} />
        <circle r={34} cy={0} />
      </g>
    </svg>
  );
});
