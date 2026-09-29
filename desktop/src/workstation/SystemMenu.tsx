import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { DraftWord } from './DraftWord';
import { DIVERGENCE, Nixie, useTunedValue } from './Nixie';

export type MenuSection = 'worldline' | 'connections' | 'sound' | 'character';
type Worldline = 'steins_gate' | 'beta';

export const MENU_SECTIONS: { key: MenuSection; zh: string; en: string }[] = [
  { key: 'worldline', zh: '世界线', en: 'WORLDLINE' },
  { key: 'connections', zh: '连接', en: 'CONNECTIONS' },
  { key: 'sound', zh: '声音', en: 'SOUND' },
  { key: 'character', zh: '角色与对话', en: 'CHARACTER' },
];

function Trace({ className }: { className: string }) {
  return (
    <svg className={`ws2-trace ${className}`} viewBox="0 0 150 120" width="150" height="120" aria-hidden="true">
      <path pathLength={1} d="M0 18 H52 L70 36 V88" /><circle cx="70" cy="92" r="3.2" />
      <path pathLength={1} d="M0 30 H40 L56 46 V110" /><circle cx="56" cy="114" r="2.4" />
      <path pathLength={1} d="M0 42 H22 L34 54 V70 H118" /><circle cx="122" cy="70" r="3.2" />
      <path pathLength={1} d="M84 0 V14 L98 28 H150" />
    </svg>
  );
}

/** Signal traces grow out of the SYSTEM button and become the menu's structure. The conversation stays mounted underneath. */
export function SystemMenu({
  open, section, onSection, onClose, returnFocusRef, children,
}: {
  open: boolean;
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
      <div className="ws2-sys-giant" key={`g-${section}-${open}`} aria-hidden="true">{MENU_SECTIONS[cur].en}</div>
      <div className="ws2-trace-feed" aria-hidden="true"><i /></div>
      <Trace className="deco-a" />
      <nav className="ws2-sys-index" aria-label="系统菜单">
        <div className="ws2-trace-bus" aria-hidden="true" />
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
            <span className="stub" /><span className="pad" />
            <span className="lbl"><span className="zh">{s.zh}</span><span className="en">{s.en}</span></span>
            <span className="lead"><i /></span>
          </button>
        ))}
        <button
          type="button"
          className="ws2-sys-item back"
          style={{ '--i': MENU_SECTIONS.length } as CSSProperties}
          onClick={() => { onClose(); returnFocusRef?.current?.focus(); }}
        >
          <span className="stub" /><span className="pad" />
          <span className="lbl"><span className="zh">返回对话</span><span className="en">CLOSE · ESC</span></span>
        </button>
      </nav>
      <section className="ws2-sys-body" key={`b-${section}-${open}`}>
        <header>
          <span className="no">0{cur + 1}</span>
          <h2>{open ? <DraftWord text={MENU_SECTIONS[cur].zh} step={110} /> : MENU_SECTIONS[cur].zh}</h2>
          <span className="en">{MENU_SECTIONS[cur].en}</span>
        </header>
        {children}
      </section>
    </div>
  );
}

function WorldlineDiagram({ worldline, target, onTarget }: { worldline: Worldline; target: Worldline; onTarget: (w: Worldline) => void }) {
  // SG sits outside any attractor field: one clean line. β lives inside a field: a converging bundle.
  const field = [-10, -5, 0, 5, 10];
  const end = (w: Worldline) => (w === 'steins_gate' ? 'translate(600 36)' : 'translate(600 128)');
  const line = (w: Worldline) => `ws2-wl-line ${w === 'beta' ? 'beta' : 'sg'}${worldline === w ? ' cur' : ''}${target === w ? ' sel' : ''}`;
  return (
    <svg className="ws2-wl-diagram" viewBox="0 0 640 170" role="radiogroup" aria-label="选择世界线">
      <g className="grid">{Array.from({ length: 9 }, (_, i) => <line key={i} x1={40 + i * 70} x2={40 + i * 70} y1="14" y2="156" />)}</g>
      <circle cx="40" cy="85" r="3" className="origin" />
      <g className={line('beta')} role="radio" aria-checked={target === 'beta'} aria-label="β 世界线" tabIndex={0}
        onClick={() => onTarget('beta')} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onTarget('beta'); } }}>
        {field.map((o) => <path key={o} d={`M40 85 C 200 85, 260 ${124 + o}, 600 ${128 + o * 0.35}`} className={o === 0 ? 'main' : 'field'} />)}
        <path d="M40 85 C 200 85, 260 124, 600 128" className="flow" />
        <path d="M40 85 C 200 85, 260 124, 600 128" className="hit" />
        <text x="574" y="152" textAnchor="end">β · {DIVERGENCE.beta}%</text>
      </g>
      <g className={line('steins_gate')} role="radio" aria-checked={target === 'steins_gate'} aria-label="STEINS;GATE 世界线" tabIndex={0}
        onClick={() => onTarget('steins_gate')} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onTarget('steins_gate'); } }}>
        <path d="M40 85 C 200 85, 270 40, 600 36" className="main" />
        <path d="M40 85 C 200 85, 270 40, 600 36" className="flow" />
        <path d="M40 85 C 200 85, 270 40, 600 36" className="hit" />
        <text x="574" y="24" textAnchor="end">STEINS;GATE · {DIVERGENCE.steins_gate}%</text>
      </g>
      <g className="here" transform={end(worldline)}>
        <circle r="9" className="ring" /><circle r="3.5" />
      </g>
      {target !== worldline && (
        <g key={target} className="aim" transform={end(target)} aria-hidden="true">
          <g className="aim-lock">
            <circle r="13" /><path d="M-22 0H-10M10 0H22M0-22V-10M0 10V22" />
            <path className="brk" d="M-17-9V-17H-9M9-17H17V-9M17 9V17H9M-9 17H-17V9" />
          </g>
        </g>
      )}
    </svg>
  );
}

export function WorldlinePanel({ worldline, busy, onShift, onArm }: {
  worldline: Worldline;
  /** A shift is already running. */
  busy: boolean;
  /** The caller decides whether an active reply needs a stop-and-shift prompt first. */
  onShift: (to: Worldline) => void;
  /** A different target line was picked. */
  onArm?: () => void;
}) {
  const [target, setTarget] = useState<Worldline>(worldline);
  useEffect(() => { setTarget(worldline); }, [worldline]);
  const armed = target !== worldline;
  const tuned = useTunedValue(DIVERGENCE[target]);
  const rowRef = useRef<HTMLDivElement>(null);
  const name = (w: Worldline) => (w === 'steins_gate' ? 'STEINS;GATE' : 'β WORLDLINE');
  const pick = (w: Worldline) => {
    if (w === target) return;
    setTarget(w);
    onArm?.();
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    rowRef.current?.animate([
      { transform: 'translateX(-3px)', filter: 'drop-shadow(-3px 0 rgba(255,70,110,.75)) drop-shadow(3px 0 rgba(90,220,255,.75))' },
      { transform: 'translateX(2px)', filter: 'drop-shadow(2px 0 rgba(255,70,110,.5)) drop-shadow(-2px 0 rgba(90,220,255,.5))', offset: .35 },
      { transform: 'none', filter: 'none' },
    ], { duration: 240, easing: 'steps(4, end)' });
  };
  return (
    <div className={`ws2-wl-panel${armed ? ' armed' : ''}`} data-target={target === 'steins_gate' ? 'sg' : 'beta'}>
      <div className="ws2-wl-hero">
        <div className="tubes" ref={rowRef}>
          <Nixie value={tuned.text} locked={tuned.locked} label={DIVERGENCE[target]} size="lg" />
        </div>
        <div className="meta">
          <small>{armed ? 'TARGET WORLDLINE' : 'CURRENT WORLDLINE'}</small><b>{name(target)}</b>
          {armed && <span className="from">FROM {DIVERGENCE[worldline]}%</span>}
        </div>
      </div>
      <WorldlineDiagram worldline={worldline} target={target} onTarget={pick} />
      <div className="ws2-m-foot">
        <p className="ws2-m-note">两条世界线各自保存会话、草稿与记忆。目标线就绪后才算切换完成。</p>
        <button key={target} type="button" className={`ws2-m-btn${armed ? ' armed' : ''}`} disabled={!armed || busy} onClick={() => onShift(target)}>
          {target === worldline ? '当前所在' : `跃迁至 ${target === 'steins_gate' ? 'STEINS;GATE' : 'β'}`}
        </button>
      </div>
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

/** Channel volumes are 0..1 in state; shown as 000..100. */
export function SoundChannel({ name, hint, volume, onVolume, enabled, onEnabled }: {
  name: string; hint: string; volume: number; onVolume: (v: number) => void; enabled?: boolean; onEnabled?: (v: boolean) => void;
}) {
  const muted = enabled === false;
  const pct = Math.round(volume * 100);
  return (
    <div className={`ws2-m-row ws2-channel${muted ? ' muted' : ''}`}>
      <div className="ws2-m-label"><b>{name}</b><small>{hint}</small></div>
      {onEnabled
        ? <button type="button" className={`ws2-led${muted ? ' on' : ''}`} aria-pressed={muted} aria-label={`${name}静音`} onClick={() => onEnabled(muted)}><i />MUTE</button>
        : <span className="ws2-led-slot" />}
      <Ruler value={pct} onChange={(v) => onVolume(v / 100)} disabled={muted} label={`${name}音量`} />
      <span className="ws2-m-val">{muted ? '---' : String(pct).padStart(3, '0')}</span>
    </div>
  );
}
