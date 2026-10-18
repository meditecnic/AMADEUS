import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EntrySequence } from './EntrySequence';

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) =>
    element.classList.contains('entry-clock')
      ? { transitionDuration: '0.96s' } as CSSStyleDeclaration
      : computedStyle(element));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('visits power-on, prewarm, and a stable reading lock before completing once', () => {
  const done = vi.fn();
  const { container } = render(<EntrySequence onComplete={done} />);
  const entry = container.querySelector('.entry-sequence')!;
  expect(entry).toHaveAttribute('data-stage', 'power-on');
  act(() => vi.advanceTimersByTime(960));
  expect(entry).toHaveAttribute('data-stage', 'prewarm');
  act(() => vi.advanceTimersByTime(960));
  expect(entry).toHaveAttribute('data-stage', 'lock');
  expect(screen.getByRole('status')).toHaveTextContent('接入完成');
  expect(entry).toHaveAttribute('data-theme', 'terminal');
  expect(entry).not.toHaveAttribute('data-wl');
  act(() => vi.advanceTimersByTime(800));
  expect(entry).toHaveAttribute('data-stage', 'lock');
  expect(done).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(160));
  expect(done).toHaveBeenCalledOnce();
  fireEvent.click(entry);
  expect(done).toHaveBeenCalledOnce();
});

it.each(['power-on', 'prewarm', 'lock'])('click skips %s exactly once and cancels remaining work', (stage) => {
  const done = vi.fn();
  const { container } = render(<EntrySequence onComplete={done} />);
  const offset = ['power-on', 'prewarm', 'lock'].indexOf(stage) * 960;
  act(() => vi.advanceTimersByTime(offset));
  fireEvent.click(screen.getByRole('button', { name: /跳过/ }));
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

it('renders the unified static lock under reduced motion, with both skip paths available', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  const done = vi.fn();
  const { container } = render(<EntrySequence onComplete={done} />);
  expect(container.querySelector('.entry-sequence')).toHaveAttribute('data-stage', 'lock');
  expect(screen.getByRole('status')).toHaveTextContent('接入完成');
  expect(container.querySelector('.entry-reel')).not.toBeInTheDocument();
  fireEvent.keyDown(window, { key: 'Escape' });
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
