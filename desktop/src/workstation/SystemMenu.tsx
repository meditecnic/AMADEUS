import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { DraftWord } from './DraftWord';
import { DIVERGENCE, Nixie } from './Nixie';
import { PencilRing } from './TitleStrip';
import { VolumeNumber } from './VolumeNumber';
import { shortDate, type Ledger } from './worldlineLedger';

export type MenuSection = 'worldline' | 'connections' | 'sound' | 'character' | 'data' | 'usage' | 'about';
type Worldline = 'steins_gate' | 'beta';

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

/*
 * FIG. 1 — the two world lines plotted as a drawing (U 11-09: the old diagram read 太塑料: neon curves, a lock-on
 * reticle and marching dashes). Divergence runs up the left axis; both lines leave one branch point and run to their
 * readings. β ends inside its attractor field, hatched in pencil; STEINS;GATE ends outside any field. The line you
 * are on is inked in its colour and ringed in pencil; picking the other one dimensions the gap between them (Δ), the
 * way a drawing gives a size. The lines answer the pointer; the ledger rows under the figure are the radios.
 */
const AXIS = { lo: 1.0, hi: 1.16, top: 34, bottom: 188 };
const yOf = (v: number) => AXIS.bottom - ((v - AXIS.lo) / (AXIS.hi - AXIS.lo)) * (AXIS.bottom - AXIS.top);
const END_X = 424;
const ORIGIN = { x: 86, y: yOf(1.012) };
const TICKS = [1.0, 1.04, 1.08, 1.12, 1.16];
const MINOR = Array.from({ length: 17 }, (_, i) => Math.round((1.0 + i * 0.01) * 100) / 100).filter((t) => !TICKS.includes(t));

function WorldlineDiagram({ worldline, target, aiming, onTarget }: {
  worldline: Worldline;
  target: Worldline;
  aiming: boolean;
  onTarget: (w: Worldline) => void;
}) {
  const y = { steins_gate: yOf(Number(DIVERGENCE.steins_gate)), beta: yOf(Number(DIVERGENCE.beta)) };
  const armed = target !== worldline;
  const path = (w: Worldline, bend = 0) => {
    const end = y[w] + bend;
    return `M${ORIGIN.x} ${ORIGIN.y} C ${ORIGIN.x + 130} ${ORIGIN.y}, ${ORIGIN.x + 140} ${end}, ${END_X} ${end}`;
  };
  const cls = (w: Worldline) => `ws2-wl-line ${w === 'beta' ? 'beta' : 'sg'}${worldline === w ? ' cur' : ''}${target === w ? ' sel' : ''}`;
  const field = { top: yOf(1.152), bottom: yOf(1.112) };
  const delta = Math.abs(Number(DIVERGENCE.beta) - Number(DIVERGENCE.steins_gate)).toFixed(6);
  const lo = Math.max(y.steins_gate, y.beta);
  const hi = Math.min(y.steins_gate, y.beta);
  const up = worldline === 'beta';
  return (
    <svg className="ws2-wl-diagram" viewBox="0 0 560 214" aria-hidden="true">
      <defs>
        <pattern id="ws2-wl-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-38)">
          <line x1="0" y1="0" x2="0" y2="6" />
        </pattern>
      </defs>
      <text className="cap" x="20" y="16">FIG. 1 — DIVERGENCE</text>
      <text className="cap zh" x="548" y="16" textAnchor="end">世界线变动率 %</text>
      <g className="axis">
        <line x1="64" y1={AXIS.top - 8} x2="64" y2={AXIS.bottom + 6} />
        {TICKS.map((t) => (
          <g key={t}>
            <line x1="58" x2="64" y1={yOf(t)} y2={yOf(t)} />
            <line className="rule" x1="66" x2="520" y1={yOf(t)} y2={yOf(t)} />
            <text x="54" y={yOf(t) + 3.5} textAnchor="end">{t.toFixed(2)}</text>
          </g>
        ))}
        {MINOR.map((t) => <line key={t} className="minor" x1="61" x2="64" y1={yOf(t)} y2={yOf(t)} />)}
      </g>
      <g className="field">
        <rect x="250" y={field.top} width="196" height={field.bottom - field.top} fill="url(#ws2-wl-hatch)" />
        <path d={`M250 ${field.top}H446M250 ${field.bottom}H446`} />
        <text x="254" y={field.top - 6}>β ATTRACTOR FIELD · 收束场</text>
      </g>
      <circle className="origin" cx={ORIGIN.x} cy={ORIGIN.y} r="2.6" />
      <text className="note" x={ORIGIN.x - 6} y={ORIGIN.y + 18}>分歧点</text>
      <g className={cls('beta')} onClick={() => onTarget('beta')}>
        {[-9, -4.5, 4.5, 9].map((o) => <path key={o} className="strand" d={path('beta', o * 0.35)} pathLength={1} />)}
        <path className="main" d={path('beta')} pathLength={1} />
        <path className="hit" d={path('beta')} />
        <circle className="end" cx={END_X} cy={y.beta} r="2.4" />
        <text className="name" x={END_X + 12} y={y.beta - 6}>β</text>
        <text className="val" x={END_X + 12} y={y.beta + 9}>{DIVERGENCE.beta}</text>
      </g>
      <g className={cls('steins_gate')} onClick={() => onTarget('steins_gate')}>
        <path className="main" d={path('steins_gate')} pathLength={1} />
        <path className="hit" d={path('steins_gate')} />
        <circle className="end" cx={END_X} cy={y.steins_gate} r="2.4" />
        <text className="name" x={END_X + 12} y={y.steins_gate - 6}>STEINS;GATE</text>
        <text className="val" x={END_X + 12} y={y.steins_gate + 9}>{DIVERGENCE.steins_gate}</text>
      </g>
      <g className="here" key={`here-${worldline}`} transform={`translate(${END_X} ${y[worldline]})`}>
        <path className="ring" d="M-13 -1 C -12 -9, 9 -10, 12 -2 C 14 6, -2 10, -9 7 C -14 4, -13 -3, -6 -6" pathLength={1} />
        <path className="arrow" d={up ? 'M-62 34 C -44 30, -30 22, -16 9 M-16 9 l-7 1.5 M-16 9 l-1.5 7' : 'M-62 -34 C -44 -30, -30 -22, -16 -9 M-16 -9 l-7 -1.5 M-16 -9 l-1.5 -7'} pathLength={1} />
        <text className="note" x="-66" y={up ? 46 : -40} textAnchor="middle">现在</text>
      </g>
      {armed && (
        <g className={`dim${aiming ? ' is-aimed' : ''}`} key={`dim-${target}`}>
          <path className="ext" d={`M${END_X + 82} ${y.steins_gate}H${END_X + 102}M${END_X + 82} ${y.beta}H${END_X + 102}`} />
          <path className="line" d={`M${END_X + 96} ${hi + 1}V${lo - 1}`} pathLength={1} />
          <path className="tick" d={`M${END_X + 92} ${hi + 5}l4 -4 4 4M${END_X + 92} ${lo - 5}l4 4 4 -4`} />
          <text className="d" transform={`translate(${END_X + 113} ${(hi + lo) / 2}) rotate(-90)`} textAnchor="middle">Δ {delta}</text>
        </g>
      )}
    </svg>
  );
}

export function WorldlinePanel({ worldline, busy, onShift, onArm, ledger = {} }: {
  worldline: Worldline;
  /** A shift is already running. */
  busy: boolean;
  /** The caller decides whether an active reply needs a stop-and-shift prompt first. */
  onShift: (to: Worldline) => void;
  /** A different target line was picked. */
  onArm?: () => void;
  /** What each line holds (conversations, memories, last used). */
  ledger?: Ledger;
}) {
  const [target, setTarget] = useState<Worldline>(worldline);
  const [aiming, setAiming] = useState(false);
  useEffect(() => { setTarget(worldline); }, [worldline]);
  const armed = target !== worldline;
  const name = (w: Worldline) => (w === 'steins_gate' ? 'STEINS;GATE' : 'β WORLDLINE');
  const pick = (w: Worldline) => {
    if (w === target) return;
    setTarget(w);
    onArm?.();
  };
  const aim = (on: boolean) => setAiming(on && armed && !busy);
  const lines: Worldline[] = ['steins_gate', 'beta'];
  const count = (n: number | null | undefined) => (n === null || n === undefined ? '—' : String(n));
  return (
    <div className={`ws2-wl-panel${armed ? ' armed' : ''}${aiming && armed && !busy ? ' aiming' : ''}`} data-target={target === 'steins_gate' ? 'sg' : 'beta'}>
      <div className="ws2-wl-hero">
        <div className="dial">
          <div className="meta"><small>CURRENT WORLDLINE<span className="zh">当前世界线</span></small><b>{name(worldline)}</b></div>
          <Nixie value={DIVERGENCE[worldline]} size="lg" blueprint={false} />
        </div>
      </div>
      <div className="ws2-wl-map">
        <WorldlineDiagram worldline={worldline} target={target} aiming={aiming && armed && !busy} onTarget={pick} />
      </div>
      <div className="ws2-wl-ledger" role="radiogroup" aria-label="选择世界线">
        {lines.map((w) => {
          const row = ledger[w];
          const here = w === worldline;
          return (
            <div
              key={w}
              role="radio"
              tabIndex={target === w ? 0 : -1}
              aria-checked={target === w}
              aria-label={w === 'beta' ? 'β 世界线' : 'STEINS;GATE 世界线'}
              className={`ws2-wl-row ${w === 'beta' ? 'beta' : 'sg'}${here ? ' cur' : ''}${target === w ? ' sel' : ''}`}
              onClick={() => pick(w)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(w); return; }
                if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
                e.preventDefault();
                const next: Worldline = w === 'beta' ? 'steins_gate' : 'beta';
                pick(next);
                e.currentTarget.parentElement?.querySelector<HTMLElement>(`.ws2-wl-row.${next === 'beta' ? 'beta' : 'sg'}`)?.focus();
              }}
            >
              <span className="mark" aria-hidden="true" />
              <span className="who"><b>{w === 'beta' ? 'β' : 'STEINS;GATE'}</b><small>{DIVERGENCE[w]}%</small></span>
              <span className="facts">
                <span><i>会话</i>{count(row?.conversations)}</span>
                <span><i>记忆</i>{count(row?.memories)}</span>
                <span><i>最近</i>{row ? shortDate(row.last) : '—'}</span>
              </span>
              <span className="tag">{here ? '当前' : target === w ? '目标' : ''}</span>
            </div>
          );
        })}
      </div>
      <p className="ws2-sr-only" aria-live="polite">{armed ? `目标世界线：${name(target)} · ${DIVERGENCE[target]}%` : ''}</p>
      <div className="ws2-m-foot">
        <p className="ws2-m-note">两条世界线各自保存会话、草稿与记忆，互不相通。目标线就绪后才算切换完成。</p>
        <button key={target} type="button" className={`ws2-m-btn${armed ? ' armed' : ''}`} disabled={!armed || busy} onClick={() => onShift(target)}
          onPointerEnter={() => aim(true)} onPointerLeave={() => aim(false)} onFocus={() => aim(true)} onBlur={() => aim(false)}>
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
