import type { CSSProperties } from 'react';

/** Each glyph is drafted as an outline on construction lines, then filled. */
export function DraftWord({ text, className = '', step = 70 }: { text: string; className?: string; step?: number }) {
  return (
    <span className={`draft ${className}`} style={{ '--step': `${step}ms`, '--len': text.length } as CSSProperties}>
      <span className="ws2-sr-only">{text}</span>
      {[...text].map((c, i) => (
        <span key={i} aria-hidden="true" className="ch" style={{ '--i': i } as CSSProperties}>{c === ' ' ? '\u00a0' : c}</span>
      ))}
    </span>
  );
}
