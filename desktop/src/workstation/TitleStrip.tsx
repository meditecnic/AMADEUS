import { useMemo } from 'react';
import { gearOutline, pencilLoop } from './drafting';

/*
 * Parts of the header band, the sheet's top margin (styles in workstation.css, "top bar").
 * MEMORY and SYSTEM are one pair of keys: a drawn glyph and a word. MEMORY keeps the approved engram; SYSTEM is the
 * original settings page's gear dial. The key whose page is open is ringed in pencil (PencilRing).
 */

/** The approved memory mark: two broken rings and a point. */
export function Engram() {
  return (
    <svg className="ws2-engram" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <g className="eg-a"><circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="62 20" transform="rotate(-40 16 16)" /></g>
      <g className="eg-b"><circle cx="16" cy="16" r="8.5" fill="none" stroke="var(--ws-accent)" strokeWidth="1.2" strokeDasharray="36 18" transform="rotate(130 16 16)" /></g>
      <circle cx="16" cy="16" r="2.2" fill="var(--ws-accent-2)" />
    </svg>
  );
}

const DIAL = gearOutline(16, 11.2);

/** The settings dial: a toothed ring that turns a tooth when pointed at, a face and its index. */
export function GearDial() {
  return (
    <svg className="ws2-dial" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <g transform="translate(16 16)">
        <g className="dl-teeth"><path d={DIAL} fill="none" stroke="currentColor" strokeWidth="1" /></g>
        <circle r="6" fill="none" stroke="var(--ws-accent)" strokeWidth="1.2" />
        <path d="M0 -1.5V-6" stroke="var(--ws-accent)" strokeWidth="1.2" strokeLinecap="round" />
        <circle r="1.6" fill="currentColor" />
      </g>
    </svg>
  );
}

/** Disconnect, as a circuit draws it: a switch standing open between two contacts. */
export function OpenSwitch() {
  return (
    <svg className="ws2-switch" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="lead" d="M1 16.5H4.8M19.2 16.5H23" />
      <circle cx="6.5" cy="16.5" r="1.7" />
      <circle cx="17.5" cy="16.5" r="1.7" />
      <path className="blade" d="M7.9 15.4L18.6 7.4" />
    </svg>
  );
}

/**
 * A pencil loop round what is current: the glyph of the open page here, the number of the open conversation in the
 * list. Centred on its parent, one unit to the pixel; drawn in once on arrival.
 */
export function PencilRing({ seed, rx = 15.5, ry = 13.5 }: { seed: string; rx?: number; ry?: number }) {
  const d = useMemo(() => pencilLoop(seed, rx, ry), [seed, rx, ry]);
  const w = Math.ceil(rx + 6);
  const h = Math.ceil(ry + 6);
  return (
    <svg className="ws2-ring" width={w * 2} height={h * 2} viewBox={`${-w} ${-h} ${w * 2} ${h * 2}`} aria-hidden="true" focusable="false">
      <path d={d} pathLength={1} />
    </svg>
  );
}

/** A new node on the worldline: a ring on the spine with a cross in it. */
export function NewNode() {
  return (
    <svg className="ws2-new-node" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 6.2V13.8M6.2 10H13.8" />
    </svg>
  );
}

/** The search glyph, drawn like the rest: a lens and its handle. */
export function Lens() {
  return (
    <svg className="ws2-lens" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="6.8" cy="6.8" r="4.6" />
      <path d="M10.3 10.3L14 14" />
    </svg>
  );
}
