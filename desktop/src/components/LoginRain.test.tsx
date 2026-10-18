import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { createRainColumns, LoginRain } from './LoginRain';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('builds reproducible rain columns from a seed and the available width', () => {
  const columns = createRainColumns(144, 42);
  expect(columns).toEqual(createRainColumns(144, 42));
  expect(columns).not.toEqual(createRainColumns(144, 43));
  expect(columns.length).toBeGreaterThan(0);
  expect(columns.every(column => column.x >= 0 && column.x < 144)).toBe(true);
  expect(createRainColumns(0, 42)).toEqual([]);
});

it('keeps the static signal surface and removes the canvas work in reduced motion', () => {
  const context = { clearRect: vi.fn(), fillText: vi.fn(), setTransform: vi.fn() };
  const getContext = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 144, height: 160 } as DOMRect);
  const raf = vi.fn();
  vi.stubGlobal('requestAnimationFrame', raf);
  const view = render(<LoginRain reducedMotion paused={false} />);
  expect(view.container.querySelector('.login-gate')).toBeInTheDocument();
  expect(view.container.querySelector('canvas')).not.toBeInTheDocument();
  expect(getContext).not.toHaveBeenCalled();
  expect(raf).not.toHaveBeenCalled();
});

it('cancels the running frame on preference change and unmount', () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    clearRect: vi.fn(), fillText: vi.fn(), setTransform: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 144, height: 160 } as DOMRect);
  const raf = vi.fn().mockReturnValue(71);
  const cancel = vi.fn();
  vi.stubGlobal('requestAnimationFrame', raf);
  vi.stubGlobal('cancelAnimationFrame', cancel);
  const view = render(<LoginRain reducedMotion={false} paused={false} />);
  expect(raf).toHaveBeenCalledOnce();
  view.rerender(<LoginRain reducedMotion paused={false} />);
  expect(cancel).toHaveBeenCalledWith(71);
  expect(raf).toHaveBeenCalledOnce();
  view.unmount();
});

it('freezes at the current rain position during an interaction instead of jumping to the first frame', () => {
  const context = { clearRect: vi.fn(), fillText: vi.fn(), setTransform: vi.fn() };
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 144, height: 160 } as DOMRect);
  vi.spyOn(window, 'getComputedStyle').mockReturnValue({
    color: 'white', fontFamily: 'monospace', animationDuration: '1000ms',
  } as CSSStyleDeclaration);
  const callbacks: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { callbacks.push(callback); return callbacks.length; }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const view = render(<LoginRain reducedMotion={false} paused={false} />);
  callbacks[0](performance.now() + 500);
  const lastPosition = context.fillText.mock.calls.at(-1);
  view.rerender(<LoginRain reducedMotion={false} paused />);
  expect(context.fillText.mock.calls.at(-1)).toEqual(lastPosition);
});
