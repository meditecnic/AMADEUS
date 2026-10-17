import { act, cleanup, renderHook } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useReducedMotion } from './useReducedMotion';

function mockPreference(initial: boolean, legacy = false) {
  const listeners = new Set<() => void>();
  const add = vi.fn((...args: unknown[]) => listeners.add(args.at(-1) as () => void));
  const remove = vi.fn((...args: unknown[]) => listeners.delete(args.at(-1) as () => void));
  const media = {
    matches: initial,
    ...(legacy
      ? { addListener: add, removeListener: remove }
      : { addEventListener: add, removeEventListener: remove }),
  };
  const matchMedia = vi.fn(() => media);
  vi.stubGlobal('matchMedia', matchMedia);
  return {
    add, remove, matchMedia,
    change(matches: boolean) {
      media.matches = matches;
      act(() => listeners.forEach((listener) => listener()));
    },
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('useReducedMotion', () => {
  it.each([false, true])('reads the initial preference (%s)', (matches) => {
    const preference = mockPreference(matches);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(matches);
    expect(preference.matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)');
    expect(preference.add).toHaveBeenCalledWith('change', expect.any(Function));
  });

  it('shares a subscription and removes it after the last consumer unmounts', () => {
    const preference = mockPreference(false);
    const first = renderHook(() => useReducedMotion());
    const second = renderHook(() => useReducedMotion());
    expect(preference.add).toHaveBeenCalledTimes(1);
    first.unmount();
    expect(preference.remove).not.toHaveBeenCalled();
    preference.change(true);
    expect(second.result.current).toBe(true);
    second.unmount();
    expect(preference.remove).toHaveBeenCalledOnce();
    expect(preference.remove).toHaveBeenCalledWith('change', preference.add.mock.calls[0][1]);
  });

  it('updates all consumers when the preference is enabled and disabled', () => {
    const preference = mockPreference(false);
    const first = renderHook(() => useReducedMotion());
    const second = renderHook(() => useReducedMotion());
    preference.change(true);
    expect([first.result.current, second.result.current]).toEqual([true, true]);
    preference.change(false);
    expect([first.result.current, second.result.current]).toEqual([false, false]);
  });

  it('reads the current preference again after the subscription is released', () => {
    mockPreference(false);
    renderHook(() => useReducedMotion()).unmount();
    mockPreference(true);
    expect(renderHook(() => useReducedMotion()).result.current).toBe(true);
  });

  it('supports older WebViews with the legacy listener API', () => {
    const preference = mockPreference(false, true);
    const { result, unmount } = renderHook(() => useReducedMotion());
    expect(preference.add).toHaveBeenCalledWith(expect.any(Function));
    preference.change(true);
    expect(result.current).toBe(true);
    unmount();
    expect(preference.remove).toHaveBeenCalledWith(preference.add.mock.calls[0][0]);
  });

  it('uses an explicit fallback when matchMedia is unavailable', () => {
    vi.stubGlobal('matchMedia', undefined);
    expect(renderHook(() => useReducedMotion()).result.current).toBe(false);
    expect(renderHook(() => useReducedMotion(true)).result.current).toBe(true);
  });

  it('uses the server fallback without reading browser APIs', () => {
    const matchMedia = vi.fn(() => { throw new Error('browser API used during SSR'); });
    vi.stubGlobal('matchMedia', matchMedia);
    function Probe() {
      return <span>{String(useReducedMotion(true))}</span>;
    }
    expect(renderToString(<Probe />)).toBe('<span>true</span>');
    expect(matchMedia).not.toHaveBeenCalled();
  });
});
