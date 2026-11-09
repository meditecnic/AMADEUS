import type { CSSProperties, ReactNode } from 'react';

/*
 * A rubber stamp on the sheet: a word in a double-ruled box, struck at a slant in pencil-red ink with the paper's pits
 * showing through (styles in workstation.css). Used where something has to read as a verdict on the whole sheet, such
 * as the link being down, or as the weight of an act that cannot be undone. It only repeats what is said in words
 * elsewhere (the meter, a dialog's title), so assistive technology skips it.
 */
export function Stamp({ children, sub, tilt = -7, className = '' }: {
  children: ReactNode;
  sub?: ReactNode;
  tilt?: number;
  className?: string;
}) {
  return (
    <div className={`ws2-stamp ${className}`} style={{ '--tilt': `${tilt}deg` } as CSSProperties} aria-hidden="true">
      <span className="ws2-stamp-word">{children}</span>
      {sub ? <span className="ws2-stamp-sub">{sub}</span> : null}
    </div>
  );
}
