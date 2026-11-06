/**
 * Giant type used as architecture (design/references.md §2.2): set huge, skewed, cropped by its frame,
 * printed through the hw-scorch filter. Always decoration behind content, never the only carrier of meaning.
 */
interface Props {
  text: string;
  lang?: string;
  className?: string;
  /** Outline only (1px hot stroke), else a low-alpha fill. */
  outline?: boolean;
  /** Vertical writing (CJK names). */
  vertical?: boolean;
}

export function GiantWord({ text, lang, className, outline = false, vertical = false }: Props) {
  return (
    <div
      className={`hw-giant${outline ? ' is-outline' : ''}${vertical ? ' is-vertical' : ''}${className ? ` ${className}` : ''}`}
      lang={lang}
      aria-hidden="true"
    >
      <span className="hw-giant-ink">{text}</span>
    </div>
  );
}
