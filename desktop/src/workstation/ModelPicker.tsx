import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { useExitPresence } from './useExitPresence';
import { Tip } from './Tip';

/** Options past this index enter together, so a long catalog finishes its cascade promptly. */
export const PICKER_CASCADE_CAP = 8;

export interface PickerOption { value: string; label: string; disabled?: boolean; title?: string }
export interface PickerSource { id: string; name: string; model?: string; mark?: ReactNode; needsKey?: boolean }

/** Searchable model picker for the composer. Keeps the accessible contract of the old select
 *  (button「模型」→ listbox「模型」→ options named by label) and adds search, the model's source,
 *  a way to jump to another admitted connection and「管理连接」. */
export function ModelPicker({
  value, options, onChange, disabled = false, sourceName, sourceMark, otherSources = [], onSource, onManage, title,
}: {
  value: string;
  options: PickerOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  sourceName: string;
  sourceMark?: ReactNode;
  otherSources?: PickerSource[];
  onSource?: (id: string) => void;
  onManage?: () => void;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  // Typing filters in place; only the opening plays the option cascade.
  const [typed, setTyped] = useState(false);
  const reducedMotion = useReducedMotion();
  // Selection and Esc commit at once; the panel only plays a shorter, inert exit after them.
  const presence = useExitPresence(open ? true : null, reducedMotion);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const current = options.find((option) => option.value === value);
  const q = query.trim().toLocaleLowerCase();
  const shown = useMemo(() => options.filter((option) => !q || option.label.toLocaleLowerCase().includes(q) || option.value.toLocaleLowerCase().includes(q)), [options, q]);
  const sources = otherSources.filter((source) => !q || source.name.toLocaleLowerCase().includes(q));

  const close = (focusTrigger = true) => {
    setOpen(false);
    if (focusTrigger) triggerRef.current?.focus({ preventScroll: true });
  };
  const openPicker = () => {
    setQuery('');
    setTyped(false);
    setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    const selected = Math.max(0, shown.findIndex((option) => option.value === value));
    setActive(selected);
    // The picker sits inside the clipped `.ws2` root; a plain focus() would scroll that root.
    searchRef.current?.focus({ preventScroll: true });
    const onDown = (event: MouseEvent) => { if (!rootRef.current?.contains(event.target as Node)) close(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const choose = (option: PickerOption | undefined) => {
    if (!option || option.disabled) return;
    close();
    if (option.value !== value) onChange(option.value);
  };
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) {
      if (event.key === 'Enter' || event.key === 'Escape') event.stopPropagation();
      return;
    }
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); setActive((i) => Math.min(shown.length - 1, i + 1)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (event.key === 'Enter') { event.preventDefault(); choose(shown[active]); }
  };

  return (
    <div className={`ws2-picker${open ? ' open' : ''}`} ref={rootRef}>
      <Tip label={open ? undefined : title} side="top" align="start">
      <button
        type="button"
        ref={triggerRef}
        className="ws2-picker-trigger"
        aria-label="模型"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-description={title}
        disabled={disabled}
        onClick={() => (open ? close() : openPicker())}
      >
        {/* Trigger shows the bare model id; lifecycle/evaluation notes stay in the list and tooltip. */}
        <span className="name">{current?.value ?? (value || '选择模型')}</span>
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      </Tip>
      {presence.shown && (
        <div
          className={`ws2-picker-pop${presence.leaving ? ' leaving' : ''}${typed ? '' : ' cascade'}`}
          onKeyDown={onKeyDown}
          inert={presence.leaving}
          aria-hidden={presence.leaving || undefined}
          onAnimationEnd={(e) => { if (presence.leaving && e.animationName === 'ws2-pick-out') presence.done(); }}
        >
          <input
            ref={searchRef}
            className="ws2-picker-search"
            aria-label="搜索模型"
            placeholder="搜索模型或连接"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setTyped(true); setActive(0); }}
          />
          <div className="ws2-picker-group">{sourceMark}{sourceName}</div>
          <ul id={presence.leaving ? undefined : listId} role="listbox" aria-label="模型" className="ws2-picker-list" tabIndex={-1}>
            {shown.length === 0 && <li className="ws2-picker-empty" role="presentation">没有匹配的模型</li>}
            {shown.map((option, i) => (
              <Tip key={option.value} label={option.title} side="right" align="start">
              <li
                style={{ '--i': Math.min(i, PICKER_CASCADE_CAP) } as CSSProperties}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled ? 'true' : undefined}
                aria-description={option.title}
                className={`${i === active ? 'active' : ''}${option.disabled ? ' disabled' : ''}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(option)}
              >
                {option.label}
                {option.value === value && <span className="check" aria-hidden="true" />}
              </li>
              </Tip>
            ))}
          </ul>
          {sources.length > 0 && onSource && (
            <>
              <div className="ws2-picker-group">其他连接</div>
              <div className="ws2-picker-sources">
                {sources.map((source) => (
                  <button key={source.id} type="button" className={source.needsKey ? 'needs-key' : undefined} onClick={() => { close(false); onSource(source.id); }}>
                    {source.mark}
                    <span>{source.name}</span>
                    {source.needsKey ? <small>缺密钥 · 去配置</small> : source.model && <small>{source.model}</small>}
                  </button>
                ))}
              </div>
            </>
          )}
          {onManage && (
            <button type="button" className="ws2-picker-manage" onClick={() => { close(false); onManage(); }}>
              管理连接…
            </button>
          )}
        </div>
      )}
    </div>
  );
}
