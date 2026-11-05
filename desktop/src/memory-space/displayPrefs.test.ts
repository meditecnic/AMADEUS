import { describe, expect, it } from 'vitest';

import {
  CHOICE_KEY, SLOW_KEY, SLOW_TTL_MS, clearSlow, machineKey, readChoice, readSlowUntil, writeChoice, writeSlow,
} from './displayPrefs';

function memory(): Pick<Storage, 'getItem' | 'setItem'> & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => { data.set(k, v); } };
}

describe('display preferences', () => {
  it('remembers the pick, and ignores anything else in the slot', () => {
    const s = memory();
    expect(readChoice(s)).toBeNull();
    writeChoice('plate', s);
    expect(readChoice(s)).toBe('plate');
    writeChoice('3d', s);
    expect(readChoice(s)).toBe('3d');
    s.data.set(CHOICE_KEY, 'cone');
    expect(readChoice(s)).toBeNull();
  });

  it('keeps the slow note for 7 days, per machine and renderer', () => {
    const s = memory();
    const now = 1_000_000;
    writeSlow('gpu-a|1920x1080|16', now, s);
    expect(readSlowUntil('gpu-a|1920x1080|16', now + SLOW_TTL_MS - 1, s)).toBe(now + SLOW_TTL_MS);
    expect(readSlowUntil('gpu-a|1920x1080|16', now + SLOW_TTL_MS, s)).toBeNull();
    expect(readSlowUntil('gpu-b|1920x1080|16', now, s)).toBeNull();
    expect(SLOW_TTL_MS).toBe(7 * 24 * 3600 * 1000);
  });

  it('"switch back to 3D" clears the note for that machine only', () => {
    const s = memory();
    writeSlow('a', 10, s);
    writeSlow('b', 10, s);
    clearSlow('a', s);
    expect(readSlowUntil('a', 11, s)).toBeNull();
    expect(readSlowUntil('b', 11, s)).not.toBeNull();
  });

  it('drops expired notes when it writes, and survives garbage', () => {
    const s = memory();
    writeSlow('old', 0, s);
    writeSlow('new', SLOW_TTL_MS + 5, s);
    expect(JSON.parse(s.data.get(SLOW_KEY)!)).not.toHaveProperty('old');
    s.data.set(SLOW_KEY, '{not json');
    expect(readSlowUntil('new', 1, s)).toBeNull();
    s.data.set(SLOW_KEY, JSON.stringify({ a: 'soon', b: 5 }));
    expect(readSlowUntil('a', 1, s)).toBeNull();
    expect(readSlowUntil('b', 1, s)).toBe(5);
  });

  it('works without any storage', () => {
    expect(readChoice(null)).toBeNull();
    expect(() => writeChoice('3d', null)).not.toThrow();
    expect(readSlowUntil('x', 1, null)).toBeNull();
  });

  it('names a machine by its renderer, screen and core count', () => {
    expect(machineKey('GPU')).toMatch(/^GPU\|/);
    expect(machineKey(null)).toMatch(/^unknown\|/);
  });
});
