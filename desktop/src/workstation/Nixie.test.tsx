import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Nixie, useTunedValue } from './Nixie';

function Probe({ value, hold = false }: { value: string; hold?: boolean }) {
  const s = useTunedValue(value, { hold, duration: 1000 });
  return (
    <div data-testid="probe" data-text={s.text} data-tuning={String(s.tuning)} data-locked={s.locked.map(Number).join('')}
      data-volt={s.volt.every(v => v === 1) ? 'full' : 'flicker'}>
      <Nixie value={s.text} state={s} label={value} size="sm" />
    </div>
  );
}

const probe = () => screen.getByTestId('probe').dataset;
const frames = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

describe('divergence meter', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('draws eight tubes in a fixed box whatever the digits, with a settled accessible name', () => {
    const { container, rerender } = render(<Nixie value="1.048596" size="lg" />);
    const box = container.querySelector('svg')!.getAttribute('viewBox');
    expect(container.querySelectorAll('.nx-tube')).toHaveLength(8);
    expect(screen.getByRole('img', { name: 'World line divergence 1.048596%' })).toBeTruthy();
    expect(container.querySelector('.nx-blueprint')).toBeTruthy();
    rerender(<Nixie value="1.129848" size="lg" />);
    expect(container.querySelector('svg')!.getAttribute('viewBox')).toBe(box);
    rerender(<Nixie value="1.129848" size="md" unlit ariaLabel="目标预览：未选择目标世界线" />);
    expect(screen.getByRole('img', { name: '目标预览：未选择目标世界线' })).toHaveClass('is-unlit');
    expect(container.querySelector('.nx-glow')).toBeNull();
    expect(container.querySelector('.nx-blueprint')).toBeNull();
    // A labelled panel can draw the large meter without its blueprint, in the tighter box.
    rerender(<Nixie value="1.129848" size="lg" blueprint={false} />);
    expect(container.querySelector('.nx-blueprint')).toBeNull();
    expect(container.querySelector('svg')!.getAttribute('viewBox')).not.toBe(box);
    expect(container.querySelector('svg')!.getAttribute('viewBox')).toBe(
      (() => { const { container: c } = render(<Nixie value="1.129848" size="md" />); return c.querySelector('svg')!.getAttribute('viewBox'); })(),
    );
  });

  it('retunes with a decelerating scramble that locks left to right and ends steady on the exact reading', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const { rerender } = render(<Probe value="1.048596" />);
    expect(probe().text).toBe('1.048596');
    expect(probe().tuning).toBe('false');
    rerender(<Probe value="1.129848" />);
    frames(32);
    expect(probe().tuning).toBe('true');
    expect(probe().locked?.[1]).toBe('1');
    expect(probe().text?.[1]).toBe('.');
    expect(probe().text).not.toBe('1.129848');
    expect(probe().volt).toBe('flicker');
    // the accessible name stays on the settled target while the digits search
    expect(screen.getByRole('img', { name: 'World line divergence 1.129848%' })).toBeTruthy();
    frames(300);
    expect(probe().locked).toMatch(/^11000000$/);
    frames(400);
    expect(probe().locked).toMatch(/^1111[01]000$/);
    frames(400);
    expect(probe().text).toBe('1.129848');
    expect(probe().locked).toBe('11111111');
    expect(probe().tuning).toBe('false');
    expect(probe().volt).toBe('full');
    const settled = probe().text;
    frames(2000);
    expect(probe().text).toBe(settled);
  });

  it('holds the old reading while covered and tunes once released', () => {
    const { rerender } = render(<Probe value="1.048596" hold />);
    rerender(<Probe value="1.129848" hold />);
    frames(1500);
    expect(probe().text).toBe('1.048596');
    expect(probe().tuning).toBe('false');
    rerender(<Probe value="1.129848" />);
    frames(32);
    expect(probe().tuning).toBe('true');
    frames(1200);
    expect(probe().text).toBe('1.129848');
  });

  it('reduced motion changes the reading statically', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('reduce'), media: query, onchange: null,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList);
    const { rerender } = render(<Probe value="1.048596" />);
    rerender(<Probe value="1.129848" />);
    expect(probe().text).toBe('1.129848');
    expect(probe().tuning).toBe('false');
    expect(vi.getTimerCount()).toBe(0);
  });
});
