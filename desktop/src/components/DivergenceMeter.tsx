import React from 'react';

interface DivergenceMeterProps {
  worldline: 'steins_gate' | 'beta';
  value?: string;
}

const VALUES: Record<DivergenceMeterProps['worldline'], string> = {
  steins_gate: '1.048596%',
  beta: '1.130205%',
};

const ADVANCE: Record<string, number> = {
  '.': 22,
  '%': 66,
};

function glyphAdvance(character: string) {
  return ADVANCE[character] ?? 66;
}

/**
 * A compact, purpose-drawn digit set based on the world-line meter reference:
 * thin geometric strokes, central construction lines and softly rounded loops.
 * Keeping the value in SVG makes the reading exact while the surrounding plate
 * remains a single raster instrument asset.
 */
const WorldlineGlyphs: React.FC<{ value: string; idPrefix: string }> = ({ value, idPrefix }) => {
  const rawWidth = [...value].reduce((sum, character) => sum + glyphAdvance(character), 0) - 8;
  const scale = Math.min(1.54, 820 / rawWidth);
  const start = 600 - (rawWidth * scale) / 2;

  return (
    <g className="divergence-meter-glyphs" transform={`translate(${start} 52) scale(${scale})`}>
      {[...value].map((character, index) => {
        const transform = `translate(${index === 0 ? 0 : [...value].slice(0, index).reduce((sum, item) => sum + glyphAdvance(item), 0)})`;
        return <use key={`${character}-${index}`} href={`#${idPrefix}-${character === '.' ? 'dot' : character === '%' ? 'percent' : character}`} transform={transform} />;
      })}
    </g>
  );
};

export const DivergenceMeter: React.FC<DivergenceMeterProps> = ({ worldline, value }) => {
  const displayValue = value ?? VALUES[worldline];
  const meterId = `divergence-glyph-${worldline}`;
  // Meter readout color is CSS-only: --wl-meter-accent → .divergence-meter → currentColor.
  // Ghost (beta) uses --wl-accent via CSS; no hex in TSX.

  return (
    <div
      className={`divergence-meter ${worldline === 'beta' ? 'divergence-meter-beta' : 'divergence-meter-sg'}`}
      role="img"
      aria-label={`World line divergence ${displayValue}`}
    >
      <img
        className="divergence-meter-plate"
        src="/assets/ui/worldline_meter_frame_v3.png"
        alt=""
        aria-hidden="true"
      />
      <svg className="divergence-meter-value" viewBox="0 0 1200 260" aria-hidden="true">
        <defs>
          <filter id={`${meterId}-glow`} x="-18%" y="-36%" width="136%" height="172%">
            <feGaussianBlur stdDeviation="2.3" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <g id={`${meterId}-0`}><ellipse cx="28" cy="47" rx="23" ry="43" /><path d="M28 4v86M5 47h46" /></g>
          <g id={`${meterId}-1`}><path d="M11 20 29 4v86M7 90h44" /></g>
          <g id={`${meterId}-2`}><path d="M7 23Q9 4 29 4q22 0 22 20Q51 40 7 90h46M29 4v86" /></g>
          <g id={`${meterId}-3`}><path d="M7 7h28q17 0 17 18 0 17-19 22 21 3 19 23 0 20-22 20H8M29 4v86M7 47h41" /></g>
          <g id={`${meterId}-4`}><path d="M47 4v86M6 64h54L35 18 6 64M35 4v86" /></g>
          <g id={`${meterId}-5`}><path d="M52 7H9v37h23q21 0 21 23 0 23-23 23Q10 90 7 70M30 4v86M9 47h43" /></g>
          <g id={`${meterId}-6`}><path d="M49 9Q40 3 29 4 8 6 8 47q0 43 22 43 23 0 23-23 0-23-23-23H8M30 4v86M8 47h45" /></g>
          <g id={`${meterId}-8`}><path d="M29 4q22 0 22 20 0 16-13 23 15 7 15 23 0 20-24 20T5 70q0-16 15-23Q7 40 7 24 7 4 29 4ZM29 47v43M6 47h46" /></g>
          <g id={`${meterId}-9`}><path d="M9 85q9 6 20 5 23-2 23-43Q52 4 29 4 7 4 7 27q0 23 22 23h23M29 4v86M7 27h45" /></g>
          <g id={`${meterId}-dot`}><circle cx="10" cy="82" r="5" fill="currentColor" stroke="none" /></g>
          <g id={`${meterId}-percent`}><path d="M8 90 56 4" /><circle cx="14" cy="20" r="10" /><circle cx="50" cy="74" r="10" /></g>
        </defs>
        {worldline === 'beta' && (
          <g className="divergence-meter-glyph-ghost" fill="none" strokeWidth="2.65" strokeLinecap="round" strokeLinejoin="round">
            <WorldlineGlyphs value={displayValue} idPrefix={meterId} />
          </g>
        )}
        <g className="divergence-meter-glyph-primary" filter={`url(#${meterId}-glow)`} fill="none" strokeWidth="2.65" strokeLinecap="round" strokeLinejoin="round">
          <WorldlineGlyphs value={displayValue} idPrefix={meterId} />
        </g>
      </svg>
    </div>
  );
};

export default DivergenceMeter;
