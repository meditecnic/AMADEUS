// What the mock tells the chat about memory for one turn. Ids come from chartFixtures.ts (the sg "normal"
// plate), plus one id the plate does not have, so the "not on the plate" path can be clicked through.
// The rules key off the user's words and are listed in README.md.

type Row = { kind: 'fact' | 'experience'; id: string; label: string; created_at: string };

const FACTS = {
  f07: { kind: 'fact', id: 'f07', label: '你最近改喝拿铁了。', created_at: '2026-09-02T00:12:00+00:00' },
  f12: { kind: 'fact', id: 'f12', label: '你养了一只叫小黑的猫。', created_at: '2026-09-15T11:20:00+00:00' },
  f17: { kind: 'fact', id: 'f17', label: '你分享的题目定为测量误差。', created_at: '2026-11-01T13:26:00+00:00' },
  e1: { kind: 'experience', id: 'e1', label: '你去看了一场钟表的展览。', created_at: '2026-10-18T11:02:00+00:00' },
  e3: { kind: 'experience', id: 'e3', label: '你前一晚为了准备分享熬到三点。', created_at: '2026-11-01T04:10:00+00:00' },
  ghost: { kind: 'fact', id: 'mock-gone-1', label: '你提过的一件已经不在星图上的事。', created_at: '2026-07-01T00:00:00+00:00' },
} satisfies Record<string, Row>;

export type MockMemoryFrames = {
  referenced: Array<Row & { via: 'supplement' | 'episodic' | 'recall' }> | null;
  recorded: Array<Row & { change: 'create' | 'supersede' }> | null;
};

export function mockMemoryForTurn(content: string): MockMemoryFrames {
  const remember = content.includes('记住');
  const correct = content.includes('其实');
  const referenced = remember || correct || /小黑|猫|咖啡|参照/.test(content);
  const recorded: MockMemoryFrames['recorded'] = remember || correct ? [
    ...(remember ? [{ ...FACTS.f17, change: 'create' as const }, { ...FACTS.e3, change: 'create' as const }] : []),
    ...(correct ? [{ ...FACTS.f07, change: 'supersede' as const }] : []),
  ] : null;
  return {
    referenced: referenced ? [
      { ...FACTS.f12, via: 'supplement' },
      { ...FACTS.e1, via: 'episodic' },
      { ...FACTS.f07, via: 'recall' },
      { ...FACTS.ghost, via: 'recall' },
    ] : null,
    recorded,
  };
}
