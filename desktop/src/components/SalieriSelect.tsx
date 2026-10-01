import React, {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

export interface SalieriSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
  title?: string;
  /** Decorative mark shown before the label in the list and on the trigger. */
  icon?: React.ReactNode;
}

export interface SalieriSelectProps {
  value: string;
  options: SalieriSelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  'aria-label': string;
  className?: string;
  triggerClassName?: string;
  listClassName?: string;
  placeholder?: string;
  /** Optional prefix rendered before the selected label (e.g. "推理强度 · "). */
  triggerPrefix?: string;
  title?: string;
}

interface MenuGeometry {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  openUp: boolean;
}

function firstEnabledIndex(options: SalieriSelectOption[], from = 0, step = 1): number {
  if (options.length === 0) return -1;
  let index = from;
  for (let i = 0; i < options.length; i += 1) {
    if (index < 0) index = options.length - 1;
    if (index >= options.length) index = 0;
    if (!options[index]?.disabled) return index;
    index += step;
  }
  return -1;
}

export const SalieriSelect: React.FC<SalieriSelectProps> = ({
  value,
  options,
  onChange,
  disabled = false,
  'aria-label': ariaLabel,
  className = '',
  triggerClassName = '',
  listClassName = '',
  placeholder = '选择…',
  triggerPrefix = '',
  title,
}) => {
  const listboxId = useId();
  const optionIdPrefix = useId();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [geometry, setGeometry] = useState<MenuGeometry | null>(null);

  const selectedIndex = useMemo(
    () => options.findIndex((option) => option.value === value),
    [options, value],
  );
  const selectedLabel = selectedIndex >= 0
    ? options[selectedIndex].label
    : (value || placeholder);
  const triggerText = triggerPrefix
    ? `${triggerPrefix}${selectedLabel}`
    : selectedLabel;

  const optionDomId = useCallback(
    (index: number) => `${optionIdPrefix}-opt-${index}`,
    [optionIdPrefix],
  );
  const activeDescendantId = activeIndex >= 0 ? optionDomId(activeIndex) : undefined;

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    setActiveIndex(-1);
    setGeometry(null);
    if (restoreFocus) {
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }, []);

  const measure = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewportH = window.innerHeight;
    const spaceBelow = viewportH - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const openUp = spaceBelow < 160 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(120, Math.min(280, openUp ? spaceAbove : spaceBelow));
    setGeometry({
      top: openUp ? rect.top - 4 : rect.bottom + 4,
      left: rect.left,
      width: Math.max(rect.width, 120),
      maxHeight,
      openUp,
    });
  }, []);

  const openMenu = useCallback(() => {
    if (disabled || options.length === 0) return;
    const initial = selectedIndex >= 0 && !options[selectedIndex]?.disabled
      ? selectedIndex
      : firstEnabledIndex(options);
    setActiveIndex(initial);
    setOpen(true);
  }, [disabled, options, selectedIndex]);

  useLayoutEffect(() => {
    if (!open) return;
    measure();
  }, [open, measure, options]);

  useEffect(() => {
    if (!open) return;
    const onReposition = () => measure();
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
    return () => {
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    };
  }, [open, measure]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (listRef.current?.contains(target)) return;
      close(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open, close]);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    const item = listRef.current?.querySelector<HTMLElement>(
      `[data-salieri-option-index="${activeIndex}"]`,
    );
    if (item && typeof item.scrollIntoView === 'function') {
      item.scrollIntoView({ block: 'nearest' });
    }
  }, [open, activeIndex]);

  const commit = useCallback((index: number) => {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange(option.value);
    close(true);
  }, [close, onChange, options]);

  const onTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!open) {
        openMenu();
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        if (activeIndex >= 0) commit(activeIndex);
      }
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      close(true);
    }
  };

  const onListKeyDown = (event: React.KeyboardEvent<HTMLUListElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (activeIndex >= 0) commit(activeIndex);
      return;
    }
    if (event.key === 'Home') {
      event.preventDefault();
      setActiveIndex(firstEnabledIndex(options, 0, 1));
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      setActiveIndex(firstEnabledIndex(options, options.length - 1, -1));
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((current) => firstEnabledIndex(
        options,
        current < 0 ? 0 : current + 1,
        1,
      ));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((current) => firstEnabledIndex(
        options,
        current < 0 ? options.length - 1 : current - 1,
        -1,
      ));
    }
  };

  const menu = open && geometry && typeof document !== 'undefined'
    ? createPortal(
      <ul
        ref={listRef}
        id={listboxId}
        role="listbox"
        tabIndex={-1}
        aria-label={ariaLabel}
        aria-activedescendant={activeDescendantId}
        className={`salieri-select-menu salieri-select-menu--portal ${listClassName}`.trim()}
        data-salieri-portal="true"
        style={{
          position: 'fixed',
          top: geometry.openUp ? undefined : geometry.top,
          bottom: geometry.openUp
            ? window.innerHeight - geometry.top
            : undefined,
          left: geometry.left,
          width: geometry.width,
          maxHeight: geometry.maxHeight,
          zIndex: 4000,
        }}
        onKeyDown={onListKeyDown}
      >
        {options.map((option, index) => {
          const selected = option.value === value;
          const active = index === activeIndex;
          return (
            <li
              key={option.value}
              id={optionDomId(index)}
              role="option"
              data-salieri-option-index={index}
              aria-selected={selected}
              aria-disabled={option.disabled || undefined}
              title={option.title}
              className={[
                'salieri-select-option',
                selected ? 'is-selected' : '',
                active ? 'is-active' : '',
                option.disabled ? 'is-disabled' : '',
              ].filter(Boolean).join(' ')}
              onMouseEnter={() => {
                if (!option.disabled) setActiveIndex(index);
              }}
              onMouseDown={(event) => {
                // Prevent trigger blur before click commits.
                event.preventDefault();
              }}
              onClick={() => commit(index)}
            >
              {option.icon}
              <span className="salieri-select-option-label">{option.label}</span>
              {selected ? <span className="salieri-select-check" aria-hidden>▸</span> : null}
            </li>
          );
        })}
      </ul>,
      // Inside the workstation the menu mounts on its root so it inherits the worldline palette; fixed positioning keeps it unclipped.
      rootRef.current?.closest<HTMLElement>('.ws2') ?? document.body,
    )
    : null;

  useEffect(() => {
    if (open) {
      window.requestAnimationFrame(() => listRef.current?.focus());
    }
  }, [open]);

  return (
    <div
      ref={rootRef}
      className={`salieri-select-root ${className}`.trim()}
      data-open={open ? 'true' : 'false'}
    >
      <button
        ref={triggerRef}
        type="button"
        className={`salieri-select-trigger ${triggerClassName}`.trim()}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        title={title}
        disabled={disabled}
        onClick={() => {
          if (open) close(true);
          else openMenu();
        }}
        onKeyDown={onTriggerKeyDown}
      >
        {selectedIndex >= 0 ? options[selectedIndex].icon : null}
        <span className="salieri-select-value">{triggerText}</span>
        <span className="salieri-select-caret" aria-hidden>▾</span>
      </button>
      {menu}
    </div>
  );
};

export default SalieriSelect;
