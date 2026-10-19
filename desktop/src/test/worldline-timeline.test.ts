import { describe, expect, it } from 'vitest';
import { evaluateShow, parseReading, standbyFrame, successEndFrame, T } from '../worldline-engine';

const readings = ['1.048596', '1.129848'];

describe.each([readings, [...readings].reverse()])('procedural timeline %s to %s', (from, to) => {
  const origin = parseReading(from)!;
  const target = parseReading(to)!;
  const context = { origin, target, outcome: 'success' as const, waitT: 0, resultT: 2.6 };

  it('locks digits in sequence and freezes every digit after the final lock', () => {
    const beforeLast = evaluateShow(T.HOLD0 - 0.01, context).readout;
    expect(beforeLast.locked.slice(0, 7).every(Boolean)).toBe(true);
    expect(beforeLast.locked[7]).toBe(false);
    const locked = evaluateShow(T.HOLD0, context).readout;
    const held = evaluateShow(8.5, context).readout;
    expect(locked.locked.every(Boolean)).toBe(true);
    expect(locked.shown).toBe(to);
    expect(held.digA).toEqual(locked.digA);
    expect(held.mix).toEqual(locked.mix);
  });

  it('never locks a pending gate and resumes delayed locks only after confirmation', () => {
    const waiting = evaluateShow(T.GATE, { ...context, outcome: null, waitT: 20 });
    expect(waiting.readout.locked.some(Boolean)).toBe(false);
    const atResult = evaluateShow(T.GATE, { ...context, resultT: T.GATE });
    expect(atResult.readout.locked.some(Boolean)).toBe(false);
    const afterResult = evaluateShow(T.GATE + 0.08, { ...context, resultT: T.GATE }).readout;
    expect(afterResult.locked[0]).toBe(true);
    expect(afterResult.locked[2]).toBe(false);
  });

  it('retains both terminal readings and a failure that never locks the target', () => {
    expect(standbyFrame(origin, false).readout.shown).toBe(from);
    expect(successEndFrame(target).readout.shown).toBe(to);
    const failed = evaluateShow(T.F_END, { ...context, outcome: 'fail' }).readout;
    expect(failed.shown).toBe(from);
    expect(failed.locked.some(Boolean)).toBe(false);
  });
});
