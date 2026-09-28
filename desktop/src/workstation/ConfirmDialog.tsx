import { useEffect, useRef, type ReactNode } from 'react';

export function ConfirmDialog({
  title, children, confirmLabel, busy = false, error, danger = false, focusCancel = true, onConfirm, onCancel,
}: {
  /** false when the body holds its own autofocused field. */
  focusCancel?: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    if (focusCancel) cancelRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing) return;
      event.preventDefault();
      event.stopPropagation();
      if (!busy) onCancel();
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      previous?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);
  return (
    <div className="ws2-dialog-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}>
      <div className="ws2-dialog" role="alertdialog" aria-modal="true" aria-labelledby="ws2-dialog-title">
        <h3 id="ws2-dialog-title">{title}</h3>
        <div className="ws2-dialog-body">{children}</div>
        {error && <p className="ws2-dialog-error" role="alert">{error}</p>}
        <div className="ws2-dialog-actions">
          <button type="button" ref={cancelRef} className="ws2-dialog-cancel" onClick={onCancel} disabled={busy}>取消</button>
          <button type="button" className={`ws2-dialog-confirm${danger ? ' danger' : ''}`} onClick={onConfirm} disabled={busy}>
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
