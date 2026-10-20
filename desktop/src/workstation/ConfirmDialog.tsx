import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { focusableTarget } from './focusHandoff';

export function ConfirmDialog({
  title, children, confirmLabel, busy = false, error, danger = false, focusCancel = true, leaving = false, onExited, returnFocus,
  onConfirm, onCancel,
}: {
  /** false focuses the first field in the body (e.g. rename) instead of 取消. */
  focusCancel?: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  danger?: boolean;
  /** Exit ghost: the outcome is already committed; the dialog is inert and only plays its (faster) exit. */
  leaving?: boolean;
  onExited?: () => void;
  /** Surviving controls to hand focus to when the opener is gone, disabled or was never a control. */
  returnFocus?: () => ReadonlyArray<HTMLElement | null | undefined>;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const handedBack = useRef(false);
  const latest = useRef({ busy, leaving, onCancel, returnFocus });
  latest.current = { busy, leaving, onCancel, returnFocus };

  // The dialog lives inside the clipped `.ws2` root; a plain focus() makes the browser scroll that root
  // and the header slides out of view, so every focus here (and the hand-back) skips scrolling.
  const focusStart = () => {
    const start = focusCancel ? cancelRef.current : bodyRef.current?.querySelector<HTMLElement>('input, textarea');
    (focusableTarget([start]) ?? dialogRef.current)?.focus({ preventScroll: true });
  };
  const handBack = () => {
    if (handedBack.current) return;
    handedBack.current = true;
    const inside = (node: HTMLElement) => Boolean(dialogRef.current?.contains(node));
    const candidates = [opener.current, ...(latest.current.returnFocus?.() ?? [])].filter(node => !node || !inside(node));
    focusableTarget(candidates)?.focus({ preventScroll: true });
  };

  // Mount only: busy flips must not hand focus to the page behind and back again. Runs first, so the opener
  // is read before anything in the dialog takes focus.
  useLayoutEffect(() => {
    handedBack.current = false; // StrictMode replays mount after a simulated unmount
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
    focusStart();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229 || latest.current.leaving) return;
      event.preventDefault();
      event.stopPropagation();
      if (!latest.current.busy) latest.current.onCancel();
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      handBack();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Busy disables both actions: keep focus on the dialog itself (never the disabled 取消, never the page),
  // and give it back to the first control once the request settles (e.g. a failed delete). Layout timing moves it
  // before the browser's own fix-up can drop a just-disabled button's focus onto the page.
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || leaving) return;
    const active = document.activeElement;
    const lost = !(active instanceof HTMLElement) || !dialog.contains(active) || active.matches(':disabled');
    if (busy) { if (lost) dialog.focus({ preventScroll: true }); }
    else if (lost || active === dialog) focusStart();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  // The outcome is committed when the exit starts, so focus moves on now rather than after the animation.
  useLayoutEffect(() => { if (leaving) handBack(); });

  return (
    <div
      className={`ws2-dialog-scrim${leaving ? ' leaving' : ''}`}
      inert={leaving}
      aria-hidden={leaving || undefined}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !busy && !leaving) onCancel(); }}
      onAnimationEnd={(e) => { if (leaving && e.animationName === 'ws2-dialog-out') onExited?.(); }}
    >
      <div className="ws2-dialog" ref={dialogRef} tabIndex={-1} role="alertdialog" aria-modal="true" aria-labelledby="ws2-dialog-title" aria-busy={busy || undefined}>
        <h3 id="ws2-dialog-title">{title}</h3>
        <div className="ws2-dialog-body" ref={bodyRef}>{children}</div>
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
