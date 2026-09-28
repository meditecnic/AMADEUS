import type { CSSProperties } from 'react';

/* Hand-drawn nixie numerals in a 12×20 box. The anime meter uses 8 tubes and the decimal point owns tube #2. */
const DIGITS: Record<string, string> = {
  '0': 'M6 2.5C9 2.5 10.2 6 10.2 10S9 17.5 6 17.5 1.8 14 1.8 10 3 2.5 6 2.5Z',
  '1': 'M6 2.5V17.5M4.1 4.6 6 2.5',
  '2': 'M2.2 6C2.2 3.8 3.8 2.5 6 2.5 8.3 2.5 9.8 4 9.8 6.2 9.8 9 6.5 11 2.2 17.5H10',
  '3': 'M2.4 4C3.3 3 4.5 2.5 6 2.5 8.3 2.5 9.6 3.9 9.6 5.8 9.6 8 7.8 9.4 5.4 9.6 8.2 9.7 10 11.3 10 13.7 10 16 8.3 17.5 6 17.5 4.3 17.5 3 16.9 2 15.8',
  '4': 'M8 17.5V2.5L1.8 12.8H10.4',
  '5': 'M9.6 2.5H3.2L2.6 9.2C3.5 8.4 4.7 8 6 8 8.4 8 10 9.8 10 12.6 10 15.6 8.3 17.5 5.8 17.5 4.2 17.5 2.9 16.8 2 15.6',
  '6': 'M9 3.4C8.2 2.8 7.2 2.5 6.2 2.5 3.6 2.5 2 5.5 2 10.4 2 15 3.6 17.5 6 17.5 8.4 17.5 10 15.6 10 12.9 10 10.2 8.4 8.4 6.1 8.4 4.3 8.4 2.8 9.4 2.1 11',
  '7': 'M2 2.5H10L4.6 17.5',
  '8': 'M6 9.6C3.9 9.6 2.6 8.2 2.6 6 2.6 3.9 4 2.5 6 2.5 8 2.5 9.4 3.9 9.4 6 9.4 8.2 8.1 9.6 6 9.6 3.6 9.6 2 11.3 2 13.6 2 16 3.7 17.5 6 17.5 8.3 17.5 10 16 10 13.6 10 11.3 8.4 9.6 6 9.6Z',
  '9': 'M3 16.6C3.8 17.2 4.8 17.5 5.8 17.5 8.4 17.5 10 14.5 10 9.6 10 5 8.4 2.5 6 2.5 3.6 2.5 2 4.4 2 7.1 2 9.8 3.6 11.6 5.9 11.6 7.7 11.6 9.2 10.6 9.9 9',
};
const GHOST = Object.values(DIGITS);

export const DIVERGENCE: Record<'steins_gate' | 'beta', string> = {
  steins_gate: '1.048596',
  beta: '1.130205',
};

function Tube({ c, n }: { c: string; n: number }) {
  return (
    <span className="nixie-tube" style={{ '--n': n } as CSSProperties}>
      <svg viewBox="0 0 12 20" className="nx-ghost" aria-hidden="true">
        {GHOST.map((d, i) => <path key={i} d={d} />)}
        <path d="M6 1.2V18.8M1.4 10H10.6M2.6 3.4 9.4 16.6M9.4 3.4 2.6 16.6" className="nx-star" />
      </svg>
      <svg viewBox="0 0 12 20" className="nx-lit" aria-hidden="true">
        {c === '.' ? <circle cx="3.2" cy="17.2" r="1.1" /> : <path d={DIGITS[c] ?? ''} />}
      </svg>
    </span>
  );
}

export function Nixie({ value, size = 'md', base = false }: { value: string; size?: 'sm' | 'md' | 'lg' | 'xl'; base?: boolean }) {
  return (
    <div className={`nixie nixie-${size}${base ? ' with-base' : ''}`} role="img" aria-label={`World line divergence ${value}%`}>
      <div className="nixie-row">{value.split('').map((c, i) => <Tube key={i} c={c} n={i} />)}</div>
      {base && <div className="nixie-base"><i /><i /><i /></div>}
    </div>
  );
}
