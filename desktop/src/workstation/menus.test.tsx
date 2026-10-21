import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { SelectionCopyMenu, TextFieldMenu } from './menus';

afterEach(cleanup);

function Field({ value = '观测记录' }: { value?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <TextFieldMenu fieldRef={ref}>
      <input ref={ref} aria-label="字段" defaultValue={value} />
    </TextFieldMenu>
  );
}

describe('workstation text menus', () => {
  it('a text field gets cut / copy / paste / select all, enabled by what the field holds', () => {
    render(<Field />);
    const input = screen.getByRole('textbox', { name: '字段' }) as HTMLInputElement;
    input.setSelectionRange(0, 0);
    fireEvent.contextMenu(input, { clientX: 10, clientY: 10 });
    const item = (name: RegExp) => screen.getByRole('menuitem', { name });
    expect(item(/^剪切/)).toHaveAttribute('data-disabled');
    expect(item(/^复制/)).toHaveAttribute('data-disabled');
    expect(item(/^全选/)).not.toHaveAttribute('data-disabled');
    fireEvent.click(item(/^全选/));
    expect(screen.queryByRole('menu')).toBeNull();
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, 4]);

    fireEvent.contextMenu(input, { clientX: 10, clientY: 10 });
    expect(item(/^剪切/)).not.toHaveAttribute('data-disabled');
    expect(item(/^复制/)).not.toHaveAttribute('data-disabled');
  });

  it('the transcript offers 复制 only over a selection, and no menu at all otherwise', () => {
    render(
      <SelectionCopyMenu>
        <div data-testid="log"><p>記録は順調よ。</p></div>
      </SelectionCopyMenu>,
    );
    const log = screen.getByTestId('log');
    window.getSelection()?.removeAllRanges();
    fireEvent.contextMenu(log);
    expect(screen.queryByRole('menu')).toBeNull();

    const range = document.createRange();
    range.selectNodeContents(log.querySelector('p')!);
    window.getSelection()?.addRange(range);
    fireEvent.contextMenu(log);
    expect(screen.getByRole('menuitem', { name: /^复制/ })).toBeInTheDocument();
  });
});
