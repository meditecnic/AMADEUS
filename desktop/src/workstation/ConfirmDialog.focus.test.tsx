import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { StrictMode, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConfirmDialog } from './ConfirmDialog';
import { focusableTarget, sessionDialogFallback } from './focusHandoff';
import { EXIT_FALLBACK_MS, useExitPresence } from './useExitPresence';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function Shell({ dialog, row = true, extra }: { dialog?: ReactNode; row?: boolean; extra?: ReactNode }) {
  return (
    <div className="ws2">
      {row && <article className="conversation-item" data-conversation-id="c1"><button type="button" className="conversation-more">更多</button></article>}
      <button type="button" className="history-new-btn">新建会话</button>
      <textarea className="chat-input-field" aria-label="输入消息" />
      {extra}
      {dialog}
    </div>
  );
}

const dialog = (props: { busy?: boolean; error?: string | null; leaving?: boolean; onExited?: () => void; onCancel?: () => void; returnFocus?: () => HTMLElement[] }) => (
  <ConfirmDialog title="删除这段对话？" confirmLabel="删除会话" danger onConfirm={() => {}} onCancel={props.onCancel ?? (() => {})} {...props}>
    <p>body</p>
  </ConfirmDialog>
);

describe('VAL-WS-017 busy delete dialog keeps a valid focus', () => {
  it('stays on the dialog while busy (never the page behind, never the disabled 取消) and returns to 取消 after a failure', () => {
    const { rerender } = render(<Shell />);
    screen.getByRole('button', { name: '更多' }).focus();
    rerender(<Shell dialog={dialog({})} />);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '取消' }));

    screen.getByRole('button', { name: '删除会话' }).focus();
    rerender(<Shell dialog={dialog({ busy: true })} />);
    expect(document.activeElement).toBe(screen.getByRole('alertdialog'));
    expect(document.activeElement).not.toBe(screen.getByRole('button', { name: '更多' }));

    rerender(<Shell dialog={dialog({ busy: false, error: '删除失败，会话仍然保留。请稍后重试。' })} />);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '取消' }));
  });

  it('after a successful delete hands focus to a surviving enabled control, not the removed trigger or body', () => {
    // StrictMode replays the mount effect; the hand-back must still happen once the exit starts.
    const { rerender } = render(<Shell />, { wrapper: StrictMode });
    screen.getByRole('button', { name: '更多' }).focus();
    const fallback = () => sessionDialogFallback(document.querySelector('.ws2'), 'c1', true);
    rerender(<Shell dialog={dialog({ returnFocus: fallback })} />);
    rerender(<Shell dialog={dialog({ busy: true, returnFocus: fallback })} />);
    // DELETE acknowledged: the row and its trigger are gone; the exit ghost is inert and hands focus on at once.
    rerender(<Shell row={false} dialog={dialog({ busy: true, leaving: true, returnFocus: fallback })} />);
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: '输入消息' }));
    expect(document.activeElement).not.toBe(document.body);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('skips a disabled landing control and falls through to the next survivor', () => {
    const { rerender } = render(<Shell />);
    const textarea = screen.getByRole('textbox', { name: '输入消息' }) as HTMLTextAreaElement;
    textarea.disabled = true;
    rerender(<Shell dialog={dialog({ returnFocus: () => sessionDialogFallback(document.querySelector('.ws2'), 'c1', true) })} />);
    rerender(<Shell row={false} />);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '新建会话' }));
  });

  it('returns to the surviving row trigger on cancel, and the ghost ignores Escape and reports its exit', () => {
    const onCancel = vi.fn();
    const onExited = vi.fn();
    const { rerender, container } = render(<Shell />);
    rerender(<Shell dialog={dialog({ onCancel, returnFocus: () => sessionDialogFallback(document.querySelector('.ws2'), 'c1', false) })} />);
    rerender(<Shell dialog={dialog({ onCancel, onExited, leaving: true, returnFocus: () => sessionDialogFallback(document.querySelector('.ws2'), 'c1', false) })} />);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '更多' }));
    const scrim = container.querySelector('.ws2-dialog-scrim')!;
    expect(scrim).toHaveAttribute('inert');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
    // jsdom has no AnimationEvent, so React listens under the vendor-prefixed name; it reads animationName from the native event.
    act(() => {
      for (const type of ['animationend', 'webkitAnimationEnd']) {
        scrim.dispatchEvent(Object.assign(new Event(type, { bubbles: true }), { animationName: 'ws2-dialog-out' }));
      }
    });
    expect(onExited).toHaveBeenCalledTimes(1);
  });

  it('accepts only connected, enabled, non-inert candidates', () => {
    render(<Shell extra={<div inert><button type="button">inert</button></div>} />);
    const detached = document.createElement('button');
    const inert = screen.getByText('inert');
    const fresh = screen.getByRole('button', { name: '新建会话' });
    expect(focusableTarget([null, detached, inert, document.body, fresh])).toBe(fresh);
  });
});

describe('exit presence', () => {
  it('keeps the last value only while leaving, drops it on done/fallback, reopen or reduced motion', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value, reduced }) => useExitPresence(value, reduced), { initialProps: { value: 'a' as string | null, reduced: false } });
    expect(result.current).toMatchObject({ shown: 'a', leaving: false });
    rerender({ value: null, reduced: false });
    expect(result.current).toMatchObject({ shown: 'a', leaving: true });
    act(() => result.current.done());
    expect(result.current).toMatchObject({ shown: null, leaving: false });

    rerender({ value: 'b', reduced: false });
    rerender({ value: null, reduced: false });
    act(() => { vi.advanceTimersByTime(EXIT_FALLBACK_MS); });
    expect(result.current.shown).toBeNull();

    rerender({ value: 'c', reduced: false });
    rerender({ value: null, reduced: false });
    rerender({ value: 'd', reduced: false });
    expect(result.current).toMatchObject({ shown: 'd', leaving: false });

    rerender({ value: null, reduced: true });
    expect(result.current).toMatchObject({ shown: null, leaving: false });
  });
});
