import { useId } from 'react';
import './SignGate.css';

/** The terminal's optical relief: a dark gate with one lit opening. Original
 * geometry only, so the access terminal never loads an image asset. */
export function SignGate({ className, paused }: { className?: string; paused?: boolean }) {
  const id = useId();
  return (
    <svg
      className={className ? `sign-gate ${className}` : 'sign-gate'}
      viewBox="0 0 560 900"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      data-paused={paused || undefined}
    >
      <defs>
        <linearGradient id={`${id}-halo`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="var(--accent-secondary)" stopOpacity="0" />
          <stop offset=".42" stopColor="var(--accent-secondary)" stopOpacity=".14" />
          <stop offset=".5" stopColor="var(--ink-primary)" stopOpacity=".24" />
          <stop offset=".58" stopColor="var(--accent-secondary)" stopOpacity=".14" />
          <stop offset="1" stopColor="var(--accent-secondary)" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}-shaft`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--ink-primary)" stopOpacity="0" />
          <stop offset=".14" stopColor="var(--ink-primary)" stopOpacity=".72" />
          <stop offset=".44" stopColor="var(--ink-primary)" stopOpacity=".88" />
          <stop offset=".72" stopColor="var(--accent-secondary)" stopOpacity=".5" />
          <stop offset="1" stopColor="var(--accent-secondary)" stopOpacity="0" />
        </linearGradient>
        <radialGradient id={`${id}-core`}>
          <stop offset="0" stopColor="var(--ink-primary)" stopOpacity=".62" />
          <stop offset=".44" stopColor="var(--accent-secondary)" stopOpacity=".2" />
          <stop offset="1" stopColor="var(--accent-secondary)" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={`${id}-beam`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--ink-primary)" stopOpacity=".4" />
          <stop offset=".6" stopColor="var(--ink-primary)" stopOpacity=".12" />
          <stop offset="1" stopColor="var(--ink-primary)" stopOpacity="0" />
        </linearGradient>
      </defs>

      <rect className="gate-slab" x="26" y="20" width="508" height="864" />
      <g className="gate-rules">
        <path d="M62 210H152M408 210H498M62 430H152M408 430H498M62 650H152M408 650H498" />
      </g>
      <rect className="gate-door" x="186" y="146" width="188" height="640" />
      <rect className="gate-halo" x="196" y="150" width="168" height="636" fill={`url(#${id}-halo)`} />
      <path className="gate-shaft" d="M262 160H298L320 780H240Z" fill={`url(#${id}-shaft)`} />
      <ellipse className="gate-core" cx="280" cy="288" rx="104" ry="182" fill={`url(#${id}-core)`} />
      <g className="gate-beam"><path d="M258 182H302L336 784H226Z" fill={`url(#${id}-beam)`} /></g>
      <g className="gate-bars">
        {[286, 442, 578, 690].map(y => (
          <rect key={y} className="gate-bar" x="186" y={y} width="188" height={6 + (y - 286) / 68} />
        ))}
      </g>
      <ellipse className="gate-spill" cx="280" cy="790" rx="118" ry="32" fill={`url(#${id}-core)`} />
      <g className="gate-frame">
        <path className="gate-lintel" d="M26 20H534V96H26Z" />
        <path className="gate-ledge" d="M26 742H534V786H26Z" />
        <path className="gate-edge" d="M186 146V786M374 146V786M186 146H374" />
        <path className="gate-frame-line" d="M26 96H534M26 786H534" />
      </g>
    </svg>
  );
}
