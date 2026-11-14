import type { CSSProperties } from 'react';
import { gearOutline } from './drafting';

/*
 * The send key, one for each sheet (the login keeps its own dial):
 *  - Steins;Gate: a clock hand on a toothed bezel. Every key you press winds the bezel on by one tooth, a ratchet
 *    rather than a count; hovering nudges the hand, pressing lets it tick forward.
 *  - β: a Nixie tube seen end on. The arrow is a filament behind the anode mesh: cold while there is nothing to send,
 *    warming as the line grows, flickering once when pressed.
 * Styles in log.css. `wound` is the number of keys pressed in this composer; `length` the characters in the line.
 */
const BEZEL = gearOutline(24, 26.5);

export function SendKey({ worldline, wound, length }: { worldline: 'steins_gate' | 'beta'; wound: number; length: number }) {
  if (worldline === 'beta') {
    const warm = length ? Math.min(1, 0.45 + length / 40) : 0;
    return (
      <svg viewBox="0 0 64 64" className="ws2-key is-tube" aria-hidden="true" style={{ '--warm': warm } as CSSProperties}>
        <defs>
          <clipPath id="ws2-tube-face"><circle cx="32" cy="32" r="22.5" /></clipPath>
        </defs>
        <circle cx="32" cy="32" r="29.5" className="glass" />
        <circle cx="32" cy="32" r="26" className="glass inner" />
        <g clipPath="url(#ws2-tube-face)" className="mesh">
          {Array.from({ length: 13 }, (_, i) => {
            const o = -20 + i * 7;
            return <path key={i} d={`M${o} 0L${o + 64} 64M${o + 64} 0L${o} 64`} />;
          })}
        </g>
        <path className="filament cold" d="M19.5 32H44M37 25l7 7-7 7" />
        <path className="filament glow" d="M19.5 32H44M37 25l7 7-7 7" />
        <path className="filament hot" d="M19.5 32H44M37 25l7 7-7 7" />
        <path className="shine" d="M14.6 24.2A19 19 0 0 1 24.4 14.4" />
        <path className="pins" d="M27 55.5v3M32 55.5v3M37 55.5v3" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 64 64" className="ws2-key is-clock" aria-hidden="true">
      <circle cx="32" cy="32" r="31" className="halo" />
      <g transform="translate(32 32)">
        <g className="bezel" style={{ '--wind': `${wound * 15}deg` } as CSSProperties}><path d={BEZEL} /></g>
        <circle r="19.5" className="face" />
        {/* XII, VI and IX; III is where the hand points */}
        <path className="ticks" d="M0 -17.5V-14.5M0 14.5V17.5M-17.5 0H-14.5" />
      </g>
      {/* the stage clock's hour hand, a tapered blade, pointing at III */}
      <g className="hand">
        <path d="M17.5 29.7L41 30.5L49.5 32L41 33.5L17.5 34.3Z" />
        <circle cx="24.5" cy="32" r="3.4" className="pivot" />
      </g>
    </svg>
  );
}
