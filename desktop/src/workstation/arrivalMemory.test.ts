import { describe, expect, it } from 'vitest';
import { ARRIVAL_WINDOW_MS, continuesTranscript, createArrivalMemory, sentenceArrivals } from './arrivalMemory';

function clocked() {
  let now = 1000;
  return { clock: () => now, advance: (ms: number) => { now += ms; } };
}

describe('arrival memory', () => {
  it('treats what a scope first shows as already read, even if it is still streaming', () => {
    const { clock } = clocked();
    const memory = createArrivalMemory(clock);
    memory.begin('s:sg:c1');
    expect(memory.arriving('m:a', true)).toBe(false);
    memory.begin('s:sg:c1');
    expect(memory.arriving('m:a', true)).toBe(false);
  });

  it('plays a live key once: inside its window only, never again after it', () => {
    const { clock, advance } = clocked();
    const memory = createArrivalMemory(clock);
    memory.begin('s:sg:c1');
    memory.begin('s:sg:c1');
    expect(memory.arriving('m:b', true)).toBe(true);
    advance(200);
    expect(memory.arriving('m:b', false)).toBe(true); // completion inside the window keeps the same running class
    advance(ARRIVAL_WINDOW_MS);
    expect(memory.arriving('m:b', true)).toBe(false);
  });

  it('reports when the last open arrival of a pass settles, so the marks can be dropped', () => {
    const { clock, advance } = clocked();
    const memory = createArrivalMemory(clock);
    memory.begin('s:sg:c1');
    expect(memory.settlesIn()).toBeNull();
    memory.begin('s:sg:c1');
    memory.arriving('m:a', true);
    advance(300);
    memory.arriving('m:b', true);
    expect(memory.settlesIn()).toBe(ARRIVAL_WINDOW_MS);
    advance(ARRIVAL_WINDOW_MS);
    memory.begin('s:sg:c1');
    expect(memory.arriving('m:a', false) || memory.arriving('m:b', false)).toBe(false);
    expect(memory.settlesIn()).toBeNull();
  });

  it('never animates content first seen outside a live stream (history, reloads)', () => {
    const { clock } = clocked();
    const memory = createArrivalMemory(clock);
    memory.begin('s:sg:c1');
    memory.begin('s:sg:c1');
    expect(memory.arriving('m:history', false)).toBe(false);
    expect(memory.arriving('m:history', true)).toBe(false);
  });

  it('forgets on a committed scope change but continues a draft that materializes into its conversation', () => {
    const { clock } = clocked();
    const memory = createArrivalMemory(clock);
    memory.begin('s:sg:draft');
    memory.begin('s:sg:draft');
    expect(memory.arriving('m:u', true)).toBe(true);
    memory.begin('s:sg:c9');
    expect(memory.arriving('m:u', true)).toBe(true);
    expect(memory.arriving('m:reply', true)).toBe(true);
    memory.begin('s:beta:c2');
    expect(memory.arriving('m:reply', true)).toBe(false);
    expect(continuesTranscript('s:sg:draft', 's:sg:c9')).toBe(true);
    expect(continuesTranscript('s:sg:c1', 's:sg:c9')).toBe(false);
    expect(continuesTranscript('s:sg:draft', 's:beta:c9')).toBe(false);
  });

  it('remembers Japanese and Chinese separately so a late subtitle does not replay its original', () => {
    const { clock, advance } = clocked();
    const memory = createArrivalMemory(clock);
    memory.begin('s:sg:c1');
    memory.begin('s:sg:c1');
    const first = sentenceArrivals(memory, 'm', [{ id: 0, ja: '一。', zh: '' }], true);
    expect([...first.ja]).toEqual([0]);
    expect([...first.zh]).toEqual([]);
    advance(ARRIVAL_WINDOW_MS + 1);
    const late = sentenceArrivals(memory, 'm', [{ id: 0, ja: '一。', zh: '一。' }, { id: 1, ja: '二。', zh: '二。' }], true);
    expect([...late.ja]).toEqual([1]);
    expect([...late.zh]).toEqual([0, 1]);
  });
});
