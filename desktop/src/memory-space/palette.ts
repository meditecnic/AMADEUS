import type { Worldline } from './types';

export type OrreryPalette = {
  void: number;
  fog: number;
  accent: number;
  signal: number;
  topic: number;
  evidence: number;
  belt: number;
  soulGlass: number;
  soulEmissive: number;
  soulLattice: number;
  lockup: string;
  lockupSoft: string;
  ambient: number;
  key: number;
  rim: number;
  bloom: number;
};

export function worldlinePalette(worldline: Worldline): OrreryPalette {
  if (worldline === 'steins_gate') {
    return {
      void: 0x080b0e,
      fog: 0x0c1218,
      accent: 0xa9cfdd,
      signal: 0xa9cfdd,
      topic: 0x7fa8b7,
      evidence: 0x5f7f8c,
      belt: 0x214b5d,
      soulGlass: 0x071118,
      soulEmissive: 0x214b5d,
      soulLattice: 0xa9cfdd,
      lockup: 'rgba(236, 244, 250, 0.96)',
      lockupSoft: 'rgba(198, 216, 228, 0.78)',
      ambient: 0x0b202a,
      key: 0xa9cfdd,
      rim: 0xdceef4,
      bloom: 0.18,
    };
  }
  return {
    void: 0x0c0a08,
    fog: 0x14100e,
    accent: 0xbd784c,
    signal: 0xbd784c,
    topic: 0xbd784c,
    evidence: 0x8e5a3f,
    belt: 0x5d3322,
    soulGlass: 0x1a100b,
    soulEmissive: 0x5d3322,
    soulLattice: 0xbd784c,
    lockup: 'rgba(255, 236, 210, 0.98)',
    lockupSoft: 'rgba(232, 208, 168, 0.8)',
    ambient: 0x1a100b,
    key: 0xbd784c,
    rim: 0xe5b17e,
    bloom: 0.2,
  };
}
