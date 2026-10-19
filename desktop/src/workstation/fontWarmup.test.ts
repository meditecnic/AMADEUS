import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fontFaceKey, rememberWorkstationFonts, warmWorkstationFonts } from './fontWarmup';

type Face = { family: string; weight: string; style: string; unicodeRange: string; status: FontFaceLoadStatus; load: ReturnType<typeof vi.fn> };
const face = (family: string, weight: string, unicodeRange: string, status: FontFaceLoadStatus = 'unloaded'): Face => ({
  family, weight, style: 'normal', unicodeRange, status, load: vi.fn(() => Promise.resolve()),
});

let set: Face[] = [];
const loadText = vi.fn((_font: string, _text?: string) => Promise.resolve([]));

describe('workstation font warm-up', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    set = [];
    loadText.mockClear();
    Object.defineProperty(document, 'fonts', {
      configurable: true,
      value: { forEach: (cb: (f: Face) => void) => set.forEach(cb), load: loadText },
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    delete (document as { fonts?: unknown }).fonts;
  });

  it('keys a face by family, weight, style and its unicode-range subset', () => {
    const a = fontFaceKey(face('"Noto Sans SC"', '400', 'U+4e00-4e3f'));
    expect(a).toMatch(/^Noto Sans SC\|400\|normal\|[0-9a-z]+$/);
    expect(fontFaceKey(face('Noto Sans SC', '400', 'U+4e40-4e7f'))).not.toBe(a);
    expect(fontFaceKey(face('Oxanium', '500', 'U+0-10FFFF'))).toBe('Oxanium|500|normal|*');
  });

  it('remembers the faces the workstation drew, most recent first', () => {
    set = [face('Oxanium', '500', 'U+0-10FFFF', 'loaded'), face('Noto Sans SC', '400', 'U+4e00-4e3f', 'unloaded')];
    rememberWorkstationFonts();
    expect(JSON.parse(localStorage.getItem('amadeus_pc_ws_fonts')!)).toEqual(['Oxanium|500|normal|*']);
    set[1].status = 'loaded';
    rememberWorkstationFonts();
    const list = JSON.parse(localStorage.getItem('amadeus_pc_ws_fonts')!);
    expect(list).toHaveLength(2);
    expect(new Set(list).size).toBe(2);
  });

  it('on the login screen loads exactly the remembered faces, in idle time, and can be cancelled', async () => {
    const used = face('Noto Sans SC', '400', 'U+4e00-4e3f');
    const other = face('Noto Sans SC', '400', 'U+9f00-9fff');
    set = [used, other];
    localStorage.setItem('amadeus_pc_ws_fonts', JSON.stringify([fontFaceKey(used)]));
    warmWorkstationFonts(400);
    expect(used.load).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(800);
    expect(used.load).toHaveBeenCalledTimes(1);
    expect(other.load).not.toHaveBeenCalled();
    expect(loadText).not.toHaveBeenCalled();

    const late = face('Oxanium', '500', 'U+0-10FFFF');
    set = [late];
    localStorage.setItem('amadeus_pc_ws_fonts', JSON.stringify([fontFaceKey(late)]));
    const cancel = warmWorkstationFonts(400);
    cancel();
    await vi.advanceTimersByTimeAsync(800);
    expect(late.load).not.toHaveBeenCalled();
  });

  it('falls back to a short seed before the workstation has reported its faces', async () => {
    set = [face('LXGW Neo XiHei', '400', 'U+0-10FFFF')];
    warmWorkstationFonts(0);
    await vi.advanceTimersByTimeAsync(2000);
    expect(loadText).toHaveBeenCalledWith('400 16px "LXGW Neo XiHei"', expect.any(String));
    expect(loadText.mock.calls.some(([font, text]) => font.includes('900') && text === '牧瀬紅莉栖')).toBe(true);
  });

  it('does nothing where the Font Loading API is missing', () => {
    delete (document as { fonts?: unknown }).fonts;
    expect(() => warmWorkstationFonts()()).not.toThrow();
    expect(() => rememberWorkstationFonts()).not.toThrow();
  });
});
