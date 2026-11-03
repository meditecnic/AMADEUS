import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RecordedMarks, ReferencedMarks } from './ChatMarks';
import type { MemoryChange, MemoryRefs } from './chatMarkers';

afterEach(cleanup);

const AT = '2026-10-12T08:01:22.123456+00:00';
const change = (id: string, kind: MemoryChange['kind'], kindOfChange: MemoryChange['change']): MemoryChange => (
  { kind, id, change: kindOfChange, label: `记忆 ${id}`, createdAt: AT }
);
const refs: MemoryRefs = {
  at: new Date(2026, 10, 3, 21, 40).getTime(),
  items: [
    { kind: 'fact', id: 'f1', via: 'supplement', label: '你养了一只猫', createdAt: AT },
    { kind: 'experience', id: 'x1', via: 'recall', label: '上周去了海边', createdAt: '2026-09-03T01:00:00+00:00' },
  ],
};

describe('RecordedMarks', () => {
  it('shows nothing without changes', () => {
    const { container } = render(<RecordedMarks messageId="u1" changes={undefined} onOpen={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('counts creates as 记下了 and refine + supersede as 更新了, side by side', () => {
    render(<RecordedMarks messageId="u1" changes={[
      change('a', 'fact', 'create'), change('b', 'experience', 'create'), change('c', 'fact', 'refine'), change('d', 'fact', 'supersede'),
    ]} onOpen={vi.fn()} />);
    expect(screen.getByTestId('memory-mark-created')).toHaveTextContent('记下了 2 条 ↗');
    expect(screen.getByTestId('memory-mark-updated')).toHaveTextContent('更新了 2 条 ↗');
    expect(screen.getByRole('button', { name: '这句话记下了 2 条记忆，在星图中看' })).toBeInTheDocument();
  });

  it('opens the group: first id is selected, the rest are marked, no hint', () => {
    const onOpen = vi.fn();
    render(<RecordedMarks messageId="u1" changes={[change('a', 'fact', 'create'), change('b', 'fact', 'create'), change('c', 'fact', 'supersede')]} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('memory-mark-created'));
    expect(onOpen).toHaveBeenLastCalledWith({ origin: 'u1:created', select: ['a', 'b'], mark: ['b'], hintAt: null });
    fireEvent.click(screen.getByTestId('memory-mark-updated'));
    expect(onOpen).toHaveBeenLastCalledWith({ origin: 'u1:updated', select: ['c'], mark: [], hintAt: null });
  });

  it('is reachable by keyboard: a native button with the mark key for focus return', () => {
    render(<RecordedMarks messageId="u1" changes={[change('a', 'fact', 'create')]} onOpen={vi.fn()} />);
    const button = screen.getByTestId('memory-mark-created');
    expect(button.tagName).toBe('BUTTON');
    expect(button).toHaveAttribute('data-memory-mark', 'u1:created');
    button.focus();
    expect(document.activeElement).toBe(button);
  });
});

describe('ReferencedMarks', () => {
  it('starts collapsed, expands to label and MM/DD per record, collapses again', () => {
    render(<ReferencedMarks messageId="a1" refs={refs} onOpen={vi.fn()} />);
    const toggle = screen.getByTestId('memory-mark-refs');
    expect(toggle).toHaveTextContent('参照 2 条 ▾');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('memory-refs-list')).toBeNull();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const list = screen.getByTestId('memory-refs-list');
    const rows = list.querySelectorAll('li');
    expect(rows[0]).toHaveTextContent('认知');
    expect(rows[0]).toHaveTextContent('你养了一只猫');
    expect(rows[0]).toHaveTextContent(/\d\d\/\d\d$/);
    expect(rows[1]).toHaveTextContent('片段');
    expect(rows[1]).toHaveTextContent('上周去了海边');
    expect(screen.getByTestId('memory-refs-open')).toHaveTextContent('在星图中看这一轮参照的记忆 ↗');
    fireEvent.click(toggle);
    expect(screen.queryByTestId('memory-refs-list')).toBeNull();
  });

  it('Esc inside the open list collapses it', () => {
    render(<ReferencedMarks messageId="a1" refs={refs} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByTestId('memory-mark-refs'));
    fireEvent.keyDown(screen.getByTestId('memory-refs-open'), { key: 'Escape' });
    expect(screen.queryByTestId('memory-refs-list')).toBeNull();
  });

  it('Esc from the link at the bottom of the list collapses it, returns focus to the button, and stays inside', () => {
    const outer = vi.fn();
    render(<div onKeyDown={outer}><ReferencedMarks messageId="a1" refs={refs} onOpen={vi.fn()} /></div>);
    const toggle = screen.getByTestId('memory-mark-refs');
    fireEvent.click(toggle);
    const link = screen.getByTestId('memory-refs-open');
    link.focus();
    expect(document.activeElement).toBe(link);
    fireEvent.keyDown(link, { key: 'Escape' });
    expect(screen.queryByTestId('memory-refs-list')).toBeNull();
    expect(document.activeElement).toBe(toggle);
    expect(outer).not.toHaveBeenCalled();
  });

  it('opens the plate with nothing selected, every referenced id marked, and the hint time', () => {
    const onOpen = vi.fn();
    render(<ReferencedMarks messageId="a1" refs={refs} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('memory-mark-refs'));
    fireEvent.click(screen.getByTestId('memory-refs-open'));
    expect(onOpen).toHaveBeenCalledWith({ origin: 'a1:refs', select: [], mark: ['f1', 'x1'], hintAt: refs.at });
  });
});
