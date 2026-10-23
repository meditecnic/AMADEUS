import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfirmDialog } from './ConfirmDialog';
import { ModelPicker } from './ModelPicker';

// jsdom has no layout, so it never scrolls. Real browsers scroll the nearest scrollable ancestor when an
// element is focused without `preventScroll`, and the clipped `.ws2` root is one: the header then slides
// out of view and stays there (assessment W1/M1). Count those focus calls as root scrolls.
let rootScrolls = 0;
const nativeFocus = HTMLElement.prototype.focus;

beforeEach(() => {
  rootScrolls = 0;
  vi.spyOn(HTMLElement.prototype, 'focus').mockImplementation(function focus(this: HTMLElement, options?: FocusOptions) {
    if (!options?.preventScroll && this.closest('.ws2')) rootScrolls += 1;
    nativeFocus.call(this, options);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function inRoot(children: ReactNode) {
  return <div className="ws2">{children}</div>;
}

describe('focus never scrolls the .ws2 root', () => {
  it('handles a listbox arrow once and ignores both IME guards for Enter and Escape', () => {
    const onChange = vi.fn();
    render(inRoot(<ModelPicker value="one" options={['one', 'two', 'three'].map(value => ({ value, label: value }))} onChange={onChange} sourceName="Mock" />));
    fireEvent.click(screen.getByRole('button', { name: '模型' }));
    const list = screen.getByRole('listbox');
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    expect(screen.getByRole('option', { name: 'two' })).toHaveClass('active');
    for (const guard of [{ isComposing: true }, { keyCode: 229 }]) {
      fireEvent.keyDown(list, { key: 'Enter', ...guard });
      fireEvent.keyDown(list, { key: 'Escape', ...guard });
      expect(screen.getByRole('listbox')).toBeInTheDocument();
      expect(onChange).not.toHaveBeenCalled();
    }
    fireEvent.keyDown(list, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledExactlyOnceWith('two');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('keeps a confirmation open for composition Escape including keyCode 229', () => {
    const onCancel = vi.fn();
    render(inRoot(<ConfirmDialog title="确认" confirmLabel="确认" onConfirm={vi.fn()} onCancel={onCancel}>body</ConfirmDialog>));
    fireEvent.keyDown(window, { key: 'Escape', isComposing: true });
    fireEvent.keyDown(window, { key: 'Escape', keyCode: 229 });
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('confirm dialog focuses cancel on open and returns focus on close without scrolling', () => {
    const { rerender } = render(inRoot(<button type="button">更多</button>));
    screen.getByRole('button', { name: '更多' }).focus({ preventScroll: true });

    rerender(inRoot(
      <>
        <button type="button">更多</button>
        <ConfirmDialog title="删除这段对话？" confirmLabel="删除会话" danger onConfirm={() => {}} onCancel={() => {}}>
          <p>body</p>
        </ConfirmDialog>
      </>,
    ));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '取消' }));

    rerender(inRoot(<button type="button">更多</button>));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '更多' }));
    expect(rootScrolls).toBe(0);
  });

  it('rename dialog focuses its own field without scrolling', () => {
    render(inRoot(
      <ConfirmDialog title="重命名会话" confirmLabel="保存" focusCancel={false} onConfirm={() => {}} onCancel={() => {}}>
        <input aria-label="会话标题" defaultValue="Default Conversation" />
      </ConfirmDialog>,
    ));
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: '会话标题' }));
    expect(rootScrolls).toBe(0);
  });

  it('model picker focuses search on open and the trigger on close without scrolling', () => {
    render(inRoot(
      <ModelPicker
        value="mock-kurisu"
        options={[{ value: 'mock-kurisu', label: 'mock-kurisu' }, { value: 'mock-lite', label: 'mock-lite' }]}
        onChange={() => {}}
        sourceName="SG 测试接入"
      />,
    ));
    fireEvent.click(screen.getByRole('button', { name: '模型' }));
    const search = screen.getByRole('textbox', { name: '搜索模型' });
    expect(document.activeElement).toBe(search);

    fireEvent.keyDown(search, { key: 'Escape' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '模型' }));
    expect(rootScrolls).toBe(0);
  });
});
