import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SendSignal } from './SendSignal';

afterEach(() => { cleanup(); vi.useRealTimers(); });

it('uses only explicit local send identities and cleans up completion, interruption and unmount', () => {
  vi.useFakeTimers();
  const root = document.createElement('div');
  root.innerHTML = '<button class="ws2-send"></button><div class="ws2-stage"></div>';
  vi.spyOn(root.querySelector('.ws2-send')!, 'getBoundingClientRect').mockReturnValue({ width: 20, height: 20, right: 100, top: 500 } as DOMRect);
  const rootRef = { current: root };
  const h = render(<SendSignal rootRef={rootRef} sendId={null} />);
  const signal = () => h.container.querySelector('.ws2-signal');
  expect(signal()).toBeNull();
  h.rerender(<SendSignal rootRef={rootRef} sendId="local-1" />);
  expect(signal()).not.toBeNull();
  act(() => vi.advanceTimersByTime(500));
  // Unrelated transcript/history rerenders cannot cancel its final cleanup.
  h.rerender(<SendSignal rootRef={rootRef} sendId="local-1" />);
  act(() => vi.advanceTimersByTime(800));
  expect(signal()).toBeNull();
  h.rerender(<SendSignal rootRef={rootRef} sendId="local-2" />);
  expect(signal()).not.toBeNull();
  h.rerender(<SendSignal rootRef={rootRef} sendId={null} />);
  expect(signal()).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
  h.rerender(<SendSignal rootRef={rootRef} sendId="local-3" />);
  h.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
