import { act, cleanup, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { plateGeometry } from '../chartCoordinates';
import { ymd } from '../chartFormat';
import { MemoryCone, type ConeControls, type MemoryConeProps } from './MemoryCone';
import { modelOf } from './coneFixtures';
import type { ConeCallbacks } from './coneRenderer';

const h = vi.hoisted(() => ({
  made: [] as Array<{ cb: ConeCallbacks; handle: Record<string, ReturnType<typeof vi.fn>> }>,
  fail: false,
}));

vi.mock('./coneRenderer', () => ({
  createConeRenderer: (_host: HTMLElement, cb: ConeCallbacks) => {
    if (h.fail) return { ok: false, reason: 'nogl', error: 'x' };
    const handle = {
      setModel: vi.fn(), setWorldline: vi.fn(), setView: vi.fn(), resize: vi.fn(), setTier: vi.fn(), entrance: vi.fn(), holdPlate: vi.fn(),
      toPlate: vi.fn((done: () => void) => done()), fromPlate: vi.fn((done: () => void) => done()), setSlow: vi.fn(), dispose: vi.fn(),
    };
    h.made.push({ cb, handle });
    return { ok: true, handle };
  },
}));

const model = modelOf(25);
const g = plateGeometry(1440, 840);
const props = (over: Partial<MemoryConeProps> = {}): MemoryConeProps => ({
  model, width: 1440, height: 840, selection: null, focusTopic: null, reducedMotion: false, entering: true, timed: true, worldline: 'steins_gate',
  marks: [], onEntered: vi.fn(), onSelect: vi.fn(), onOverview: vi.fn(), onUnavailable: vi.fn(), onContextLost: vi.fn(), onSlow: vi.fn(), ...over,
});
const latest = () => h.made[h.made.length - 1];

beforeEach(() => { h.made.length = 0; h.fail = false; });
afterEach(() => cleanup());

describe('MemoryCone', () => {
  it('hands the renderer the model and the plate circle it is fitted to, and plays the entrance once', () => {
    const p = props();
    const { rerender } = render(<MemoryCone {...p} />);
    expect(latest().handle.setModel).toHaveBeenCalledWith(model, { w: 1440, h: 840, cx: g.cx, cy: g.cy, R: g.radius });
    expect(latest().handle.entrance).toHaveBeenCalledTimes(1);
    expect(latest().handle.setWorldline).toHaveBeenCalledWith('steins_gate');
    rerender(<MemoryCone {...p} width={1200} height={800} />);
    expect(latest().handle.resize).toHaveBeenCalledWith(expect.objectContaining({ w: 1200, h: 800 }));
    expect(latest().handle.entrance).toHaveBeenCalledTimes(1);
    expect(latest().handle.setModel).toHaveBeenCalledTimes(1);
  });

  it('tells the page when the entrance is over', () => {
    const p = props();
    render(<MemoryCone {...p} />);
    expect(p.onEntered).not.toHaveBeenCalled();
    act(() => latest().cb.onEntering(true));
    expect(p.onEntered).not.toHaveBeenCalled();
    act(() => latest().cb.onEntering(false));
    expect(p.onEntered).toHaveBeenCalledTimes(1);
  });

  it('plays no entrance when motion is reduced: the camera is simply there', () => {
    const p = props({ reducedMotion: true });
    render(<MemoryCone {...p} />);
    expect(latest().handle.entrance).not.toHaveBeenCalled();
    expect(p.onEntered).toHaveBeenCalledTimes(1);
  });

  it('waits flat under the plate, without an entrance, when it is mounted for a switch', () => {
    render(<MemoryCone {...props({ startFlat: true })} />);
    expect(latest().handle.holdPlate).toHaveBeenCalledTimes(1);
    expect(latest().handle.entrance).not.toHaveBeenCalled();
  });

  it('passes selection, focus and marks to the renderer whenever they change', () => {
    const p = props();
    const { rerender } = render(<MemoryCone {...p} />);
    const star = model.stars[3];
    rerender(<MemoryCone {...p} selection={{ kind: 'star', id: star.id }} focusTopic="t1" marks={[star.id]} />);
    expect(latest().handle.setView).toHaveBeenLastCalledWith({ selection: { kind: 'star', id: star.id }, focusTopic: 't1', marks: [star.id], reducedMotion: false });
  });

  it('forwards the renderer\'s clicks and lost contexts', () => {
    const p = props();
    render(<MemoryCone {...p} />);
    latest().cb.onSelect({ kind: 'soul' });
    latest().cb.onOverview();
    latest().cb.onContextLost();
    expect(p.onSelect).toHaveBeenCalledWith({ kind: 'soul' });
    expect(p.onOverview).toHaveBeenCalled();
    expect(p.onContextLost).toHaveBeenCalled();
  });

  it('says so when no context can be made', () => {
    h.fail = true;
    const p = props();
    render(<MemoryCone {...p} />);
    expect(p.onUnavailable).toHaveBeenCalledTimes(1);
  });

  it('shows the note beside a hovered star: the sentence, its number, date and how often it was mentioned', () => {
    render(<MemoryCone {...props()} />);
    const star = model.stars.find((s) => s.evidenceCount > 1) ?? model.stars[2];
    act(() => latest().cb.onHover({ kind: 'star', id: star.id, x: 300, y: 200 }));
    const tip = screen.getByTestId('cone-hover');
    expect(tip).toHaveTextContent(star.label.slice(0, 10));
    expect(tip).toHaveTextContent(/No\.\d+ · \d\d\/\d\d/);
    act(() => latest().cb.onHover(null));
    expect(screen.queryByTestId('cone-hover')).toBeNull();
  });

  it('names a hovered ribbon "主题 · YYYY.MM · N 条" and says a click opens it', () => {
    const big = modelOf(2000);
    render(<MemoryCone {...props({ model: big })} />);
    const cell = [...big.cells.values()].find((c) => c.collapsedIds.length > 3)!;
    const group = big.groups.find((t) => t.id === cell.groupId)!;
    act(() => latest().cb.onHover({ kind: 'cell', key: cell.key, group: group.id, x: 300, y: 200 }));
    const tip = screen.getByTestId('cone-band-hover');
    expect(tip).toHaveTextContent(`${group.label} · ${cell.band.key.replace('-', '.')} · ${cell.starIds.length} 条`);
    expect(tip).toHaveTextContent('点一下展开');
  });

  it('captions the cone: the disc is today, and each ring further down is a month earlier', () => {
    render(<MemoryCone {...props()} />);
    expect(screen.getByTestId('cone-fig')).toHaveTextContent(`FIG. 1 — 自 ${ymd(model.origin!)} 起 · 圆盘是今天 · 往下每圈早一个月`);
    cleanup();
    render(<MemoryCone {...props({ model: modelOf(2000) })} />);
    expect(screen.getByTestId('cone-legend')).toHaveTextContent('一根刻线 = 一条收起来的记忆，横跨它那个月');
  });

  it('lowers the pixel ratio after one slow window and gives way to the plate after the second', () => {
    const p = props();
    render(<MemoryCone {...p} />);
    let at = performance.now() + 1600;
    const feed = (seconds: number, ms: number) => { for (let i = 0; i < (seconds * 1000) / ms; i += 1) { at += ms; latest().cb.onDrawn({ at, ms }); } };
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    feed(5.5, 40);
    expect(latest().handle.setTier).toHaveBeenCalledWith(1);
    expect(p.onSlow).not.toHaveBeenCalled();
    feed(5.5, 40);
    expect(p.onSlow).toHaveBeenCalledTimes(1);
  });

  it('does not time the frames of a cone the user asked for', () => {
    const p = props({ timed: false });
    render(<MemoryCone {...p} />);
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    let at = performance.now() + 1600;
    for (let i = 0; i < 400; i += 1) { at += 40; latest().cb.onDrawn({ at, ms: 40 }); }
    expect(latest().handle.setTier).not.toHaveBeenCalled();
    expect(p.onSlow).not.toHaveBeenCalled();
  });

  it('lets the page flatten and raise the cone, and lets go of the renderer when it leaves', () => {
    const ref = createRef<ConeControls>();
    const { unmount } = render(<MemoryCone ref={ref} {...props()} />);
    const done = vi.fn();
    ref.current!.toPlate(done);
    ref.current!.fromPlate(done);
    expect(done).toHaveBeenCalledTimes(2);
    expect(latest().handle.toPlate).toHaveBeenCalled();
    expect(latest().handle.fromPlate).toHaveBeenCalled();
    const handle = latest().handle;
    unmount();
    expect(handle.dispose).toHaveBeenCalledTimes(1);
  });
});
