import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EntrySequence } from './EntrySequence';

// The timeline length is a tunable token; the component must resolve it by reading
// the clock element's computed duration instead of hardcoding a number.
const FULL_TIMELINE = 2750;
const REDUCED_FADE = 120;
let clockMs = FULL_TIMELINE;

beforeEach(() => {
  clockMs = FULL_TIMELINE;
  vi.useFakeTimers();
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) =>
    element.classList.contains('entry-clock')
      ? { transitionDuration: `${clockMs}ms` } as CSSStyleDeclaration
      : computedStyle(element));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('carries the terminal screen itself and completes once when the timeline ends', () => {
  const done = vi.fn();
  const { container } = render(<EntrySequence onComplete={done} />);
  const entry = container.querySelector('.entry-sequence')!;
  expect(entry).toHaveAttribute('data-theme', 'terminal');
  expect(entry).not.toHaveAttribute('data-wl');
  expect(entry).toHaveAttribute('data-motion', 'full');
  // Both travelling panes carry the same screen; the seam is the connection signal.
  expect(container.querySelectorAll('.entry-screen')).toHaveLength(2);
  expect(container.querySelectorAll('.entry-gate')).toHaveLength(2);
  expect(container.querySelector('.entry-seam')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('正在接入工作台');
  act(() => vi.advanceTimersByTime(FULL_TIMELINE - 1));
  expect(done).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1));
  expect(done).toHaveBeenCalledOnce();
  fireEvent.click(entry);
  expect(done).toHaveBeenCalledOnce();
});

it.each([0, 900, FULL_TIMELINE - 100])('skips at %sms by click or Escape exactly once', (offset) => {
  const done = vi.fn();
  const { container } = render(<EntrySequence onComplete={done} />);
  act(() => vi.advanceTimersByTime(offset));
  fireEvent.click(screen.getByRole('button', { name: /跳过/ }));
  fireEvent.keyDown(window, { key: 'Escape', cancelable: true });
  fireEvent.click(container.querySelector('.entry-sequence')!);
  act(() => vi.runAllTimers());
  expect(done).toHaveBeenCalledOnce();
});

it('consumes Escape in capture before any workstation listener and cleans up on unmount', () => {
  const done = vi.fn();
  const leaked = vi.fn();
  window.addEventListener('keydown', leaked);
  const { unmount } = render(<EntrySequence onComplete={done} />);
  expect(fireEvent.keyDown(window, { key: 'Escape', cancelable: true })).toBe(false);
  expect(done).toHaveBeenCalledOnce();
  expect(leaked).not.toHaveBeenCalled();
  unmount();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(leaked).toHaveBeenCalledOnce();
  window.removeEventListener('keydown', leaked);
  act(() => vi.runAllTimers());
  expect(done).toHaveBeenCalledOnce();
});

it('reveals statically through the short fade token under reduced motion', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  clockMs = REDUCED_FADE;
  const done = vi.fn();
  const { container } = render(<EntrySequence onComplete={done} />);
  const entry = container.querySelector('.entry-sequence')!;
  expect(entry).toHaveAttribute('data-motion', 'reduced');
  act(() => vi.advanceTimersByTime(REDUCED_FADE - 1));
  expect(done).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(1));
  expect(done).toHaveBeenCalledOnce();
  fireEvent.keyDown(window, { key: 'Escape', cancelable: true });
  fireEvent.click(screen.getByRole('button', { name: /跳过/ }));
  expect(done).toHaveBeenCalledOnce();
});

it('cancels the timeline on unmount without calling completion', () => {
  const done = vi.fn();
  const { unmount } = render(<EntrySequence onComplete={done} />);
  unmount();
  act(() => vi.runAllTimers());
  expect(done).not.toHaveBeenCalled();
});
