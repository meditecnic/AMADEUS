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
      void: 0x05080c,
      fog: 0x05080c,
      accent: 0xb4c6d4,
      signal: 0x7eafc8,
      topic: 0xc5d4de,
      evidence: 0x8fa3b0,
      belt: 0x7a93a6,
      soulGlass: 0x0c141c,
      soulEmissive: 0x4a6a82,
      soulLattice: 0xa8c0d0,
      lockup: 'rgba(236, 244, 250, 0.96)',
      lockupSoft: 'rgba(198, 216, 228, 0.78)',
      ambient: 0x1a2430,
      key: 0xb8d4e8,
      rim: 0xd8e6f0,
      bloom: 0.28,
    };
  }
  return {
    void: 0x0a0908,
    fog: 0x0a0908,
    accent: 0xc9a06a,
    signal: 0xd4b07a,
    topic: 0xd4b07a,
    evidence: 0xb89870,
    belt: 0xa88858,
    soulGlass: 0x161210,
    soulEmissive: 0x6a4820,
    soulLattice: 0xd4b07a,
    lockup: 'rgba(255, 236, 210, 0.98)',
    lockupSoft: 'rgba(232, 208, 168, 0.8)',
    ambient: 0x221810,
    key: 0xffc978,
    rim: 0xffe6c0,
    bloom: 0.36,
  };
}
