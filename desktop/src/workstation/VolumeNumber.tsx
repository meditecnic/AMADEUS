import { useState } from 'react';

/*
 * A volume as a number you can type (U, 11-09: "用键盘精准编辑，050 就叫 50"). Plain digits, no padding. Click or Tab in
 * selects it; type 0–100 and Enter (or leave the field) to set it; Esc puts the old value back; ↑ ↓ step by 1, with
 * Shift by 10. Muted channels show a dash and are not editable until unmuted.
 */
export function VolumeNumber({ value, onChange, label, disabled = false, mutedText = '—', className }: {
  /** 0..100 */
  value: number;
  onChange: (value: number) => void;
  label: string;
  disabled?: boolean;
  mutedText?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const clamp = (n: number) => Math.max(0, Math.min(100, Math.round(n)));
  const commit = () => {
    if (draft === null) return;
    const n = Number.parseInt(draft, 10);
    if (Number.isFinite(n) && clamp(n) !== value) onChange(clamp(n));
    setDraft(null);
  };
  return (
    <input
      className={`ws2-num${className ? ` ${className}` : ''}`}
      type="text"
      inputMode="numeric"
      autoComplete="off"
      spellCheck={false}
      size={3}
      maxLength={3}
      aria-label={label}
      title={disabled ? undefined : '输入 0–100，回车确认；↑ ↓ 微调'}
      disabled={disabled}
      value={disabled ? mutedText : (draft ?? String(value))}
      onFocus={(e) => e.currentTarget.select()}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').slice(0, 3))}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.nativeEvent.isComposing) return;
        if (e.key === 'Enter') { e.preventDefault(); commit(); e.currentTarget.select(); }
        else if (e.key === 'Escape') { e.preventDefault(); setDraft(null); e.currentTarget.blur(); }
        else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault();
          const step = (e.shiftKey ? 10 : 1) * (e.key === 'ArrowUp' ? 1 : -1);
          const base = draft !== null && Number.isFinite(Number.parseInt(draft, 10)) ? Number.parseInt(draft, 10) : value;
          setDraft(null);
          onChange(clamp(base + step));
          requestAnimationFrame(() => e.currentTarget?.select?.());
        }
      }}
    />
  );
}
