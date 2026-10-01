import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';

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
    setQuery('');
    if (focusTrigger) triggerRef.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    const selected = Math.max(0, shown.findIndex((option) => option.value === value));
    setActive(selected);
    searchRef.current?.focus();
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
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); setActive((i) => Math.min(shown.length - 1, i + 1)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (event.key === 'Enter') { event.preventDefault(); choose(shown[active]); }
  };

  return (
    <div className={`ws2-picker${open ? ' open' : ''}`} ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        className="ws2-picker-trigger"
        aria-label="模型"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        title={title}
        onClick={() => (open ? close() : setOpen(true))}
      >
        {/* Trigger shows the bare model id; lifecycle/evaluation notes stay in the list and tooltip. */}
        <span className="name">{current?.value ?? (value || '选择模型')}</span>
        <span className="caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="ws2-picker-pop" onKeyDown={onKeyDown}>
          <input
            ref={searchRef}
            className="ws2-picker-search"
            aria-label="搜索模型"
            placeholder="搜索模型或连接"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
          />
          <div className="ws2-picker-group">{sourceMark}{sourceName}</div>
          <ul id={listId} role="listbox" aria-label="模型" className="ws2-picker-list" tabIndex={-1} onKeyDown={onKeyDown}>
            {shown.length === 0 && <li className="ws2-picker-empty" role="presentation">没有匹配的模型</li>}
            {shown.map((option, i) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled ? 'true' : undefined}
                title={option.title}
                className={`${i === active ? 'active' : ''}${option.disabled ? ' disabled' : ''}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(option)}
              >
                {option.label}
                {option.value === value && <span className="check" aria-hidden="true" />}
              </li>
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
