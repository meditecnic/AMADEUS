import type { KeyboardEvent } from 'react';

/** A choice among a few words, the chosen one ringed in pencil (as the identity choice is). Arrow keys move it. */
export function Choice<T extends string>({ label, value, options, onChange, disabled = false }: {
  label: string;
  value: T;
  options: { value: T; label: string; disabled?: boolean }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  const open = options.filter((option) => !option.disabled);
  const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (!step || disabled || !open.length) return;
    event.preventDefault();
    const at = open.findIndex((option) => option.value === value);
    const next = open[(at + step + open.length) % open.length];
    onChange(next.value);
    const group = event.currentTarget.parentElement;
    window.requestAnimationFrame(() => group?.querySelectorAll<HTMLButtonElement>('button')[options.indexOf(next)]?.focus());
  };
  return (
    <div className="ws2-choice" role="radiogroup" aria-label={label}>
      {options.map((option) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={on}
            className={on ? 'on' : undefined}
            tabIndex={on ? 0 : -1}
            disabled={disabled || option.disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={onKey}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
