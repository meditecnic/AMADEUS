import { useEffect, type CSSProperties, type ReactNode } from 'react';
import { DraftWord } from './DraftWord';
import { PencilRing } from './TitleStrip';
import { VolumeNumber } from './VolumeNumber';

export type MenuSection = 'worldline' | 'connections' | 'sound' | 'character' | 'data' | 'usage' | 'about';

export const MENU_SECTIONS: { key: MenuSection; zh: string; en: string }[] = [
  { key: 'worldline', zh: '世界线', en: 'WORLDLINE' },
  { key: 'connections', zh: '连接', en: 'CONNECTIONS' },
  { key: 'sound', zh: '声音', en: 'SOUND' },
  { key: 'character', zh: '角色与对话', en: 'CHARACTER' },
  { key: 'data', zh: '数据', en: 'DATA' },
  { key: 'usage', zh: '用量', en: 'USAGE' },
  { key: 'about', zh: '关于', en: 'ABOUT' },
];

/** The system page: an index of its sections on the left (the current one's number ringed in pencil), the section's
 *  form on the right. The conversation stays mounted underneath. */
export function SystemMenu({
  open, section, onSection, onClose, returnFocusRef, marks, children,
}: {
  open: boolean;
  /** A pencil dot by a section that has news (a new version under 关于), with words for screen readers. */
  marks?: Partial<Record<MenuSection, string>>;
  section: MenuSection;
  onSection: (s: MenuSection) => void;
  onClose: () => void;
  returnFocusRef?: React.RefObject<HTMLButtonElement | null>;
  children: ReactNode;
}) {
  const cur = Math.max(0, MENU_SECTIONS.findIndex((s) => s.key === section));
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => {
      if (e.isComposing || e.defaultPrevented) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        returnFocusRef?.current?.focus();
      }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [open, onClose, returnFocusRef]);

  return (
    <div className={`ws2-sysmenu${open ? ' open' : ''}`} aria-hidden={!open} inert={!open} style={{ '--cur': cur } as CSSProperties}>
      <nav className="ws2-sys-index" aria-label="系统菜单">
        {MENU_SECTIONS.map((s, i) => (
          <button
            key={s.key}
            type="button"
            className={`ws2-sys-item${i === cur ? ' on' : ''}`}
            style={{ '--i': i } as CSSProperties}
            aria-current={i === cur ? 'page' : undefined}
            onClick={() => onSection(s.key)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
              e.preventDefault();
              const next = MENU_SECTIONS[(i + (e.key === 'ArrowDown' ? 1 : -1) + MENU_SECTIONS.length) % MENU_SECTIONS.length];
              onSection(next.key);
              (e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('.ws2-sys-item')[MENU_SECTIONS.indexOf(next)])?.focus();
            }}
          >
            <span className="no" aria-hidden="true">
              {String(i + 1).padStart(2, '0')}
              {i === cur && open ? <PencilRing key={s.key} seed={`system-${s.key}`} rx={13} ry={10} /> : null}
            </span>
            <span className="lbl"><span className="zh">{s.zh}</span><span className="en">{s.en}</span></span>
            {marks?.[s.key] ? <span className="ws2-news"><span className="ws2-sr-only">（{marks[s.key]}）</span></span> : null}
          </button>
        ))}
        <button
          type="button"
          className="ws2-sys-item back"
          style={{ '--i': MENU_SECTIONS.length } as CSSProperties}
          onClick={() => { onClose(); returnFocusRef?.current?.focus(); }}
        >
          <span className="no" aria-hidden="true" />
          <span className="lbl"><span className="zh">返回对话</span><span className="en">ESC</span></span>
        </button>
      </nav>
      <section className="ws2-sys-body" key={`b-${section}-${open}`}>
        <header>
          <span className="no">{String(cur + 1).padStart(2, '0')}</span>
          <h2>{open ? <DraftWord text={MENU_SECTIONS[cur].zh} step={110} /> : MENU_SECTIONS[cur].zh}</h2>
          <span className="en">{MENU_SECTIONS[cur].en}</span>
        </header>
        {children}
      </section>
    </div>
  );
}

function Ruler({ value, onChange, disabled, label }: { value: number; onChange: (v: number) => void; disabled?: boolean; label: string }) {
  return (
    <div className={`ws2-ruler${disabled ? ' off' : ''}`}>
      <input type="range" min={0} max={100} value={value} disabled={disabled} aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))} style={{ '--val': `${value}%` } as CSSProperties} />
      <div className="ticks" aria-hidden="true">{Array.from({ length: 11 }, (_, i) => <i key={i} className={i % 5 === 0 ? 'major' : ''} />)}</div>
    </div>
  );
}

/** Channel volumes are 0..1 in state; shown and typed as 0..100. */
export function SoundChannel({ name, hint, volume, onVolume, enabled, onEnabled }: {
  name: string; hint: string; volume: number; onVolume: (v: number) => void; enabled?: boolean; onEnabled?: (v: boolean) => void;
}) {
  const muted = enabled === false;
  const pct = Math.round(volume * 100);
  return (
    <div className={`ws2-m-row ws2-channel${muted ? ' muted' : ''}`}>
      <div className="ws2-m-label"><b>{name}</b><small>{hint}</small></div>
      {onEnabled
        ? <button type="button" className={`ws2-led${muted ? ' on' : ''}`} aria-pressed={muted} aria-label={`${name}静音`} onClick={() => onEnabled(muted)}><i />静音</button>
        : <span className="ws2-led-slot" />}
      <Ruler value={pct} onChange={(v) => onVolume(v / 100)} disabled={muted} label={`${name}音量`} />
      <VolumeNumber className="ws2-m-val" value={pct} onChange={(v) => onVolume(v / 100)} disabled={muted} label={`${name}音量（0–100）`} />
    </div>
  );
}
