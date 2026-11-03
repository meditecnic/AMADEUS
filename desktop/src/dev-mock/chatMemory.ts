// What the mock tells the chat about memory for one turn. Ids come from chartFixtures.ts (the "normal" plate
// of the turn's worldline), plus one id no plate has, so the "not on the plate" path can be clicked through.
// The rules key off the user's words and are listed in README.md.

type Row = { kind: 'fact' | 'experience'; id: string; label: string; created_at: string };
type Rows = { supplement: Row; episodic: Row; recall: Row; created: [Row, Row]; superseded: Row };

const GONE: Row = { kind: 'fact', id: 'mock-gone-1', label: '你提过的一件已经不在星图上的事。', created_at: '2026-07-01T00:00:00+00:00' };

const SG: Rows = {
  supplement: { kind: 'fact', id: 'f12', label: '你养了一只叫小黑的猫。', created_at: '2026-09-15T11:20:00+00:00' },
  episodic: { kind: 'experience', id: 'e1', label: '你去看了一场钟表的展览。', created_at: '2026-10-18T11:02:00+00:00' },
  recall: { kind: 'fact', id: 'f07', label: '你最近改喝拿铁了。', created_at: '2026-09-02T00:12:00+00:00' },
  created: [
    { kind: 'fact', id: 'f17', label: '你分享的题目定为测量误差。', created_at: '2026-11-01T13:26:00+00:00' },
    { kind: 'experience', id: 'e3', label: '你前一晚为了准备分享熬到三点。', created_at: '2026-11-01T04:10:00+00:00' },
  ],
  superseded: { kind: 'fact', id: 'f07', label: '你最近改喝拿铁了。', created_at: '2026-09-02T00:12:00+00:00' },
};

const BETA: Rows = {
  supplement: { kind: 'fact', id: 'g01', label: '你喜欢深夜沿着河边散步。', created_at: '2026-08-28T15:40:00+00:00' },
  episodic: { kind: 'experience', id: 'x3', label: '你昨天在河边看到了萤火虫。', created_at: '2026-09-19T14:05:00+00:00' },
  recall: { kind: 'fact', id: 'g09', label: '你一周带三天饭。', created_at: '2026-09-07T23:50:00+00:00' },
  created: [
    { kind: 'fact', id: 'g18', label: '你想去海边拍日出。', created_at: '2026-11-01T14:20:00+00:00' },
    { kind: 'experience', id: 'x4', label: '你今天做的咖喱有点咸。', created_at: '2026-11-02T11:10:00+00:00' },
  ],
  superseded: { kind: 'fact', id: 'g09', label: '你一周带三天饭。', created_at: '2026-09-07T23:50:00+00:00' },
};

export type MockMemoryFrames = {
  referenced: Array<Row & { via: 'supplement' | 'episodic' | 'recall' }> | null;
  recorded: Array<Row & { change: 'create' | 'supersede' }> | null;
};

export function mockMemoryForTurn(content: string, worldline: string): MockMemoryFrames {
  const rows = worldline === 'beta' ? BETA : SG;
  const remember = content.includes('记住');
  const correct = content.includes('其实');
  const referenced = remember || correct || /小黑|猫|咖啡|参照/.test(content);
  const recorded: MockMemoryFrames['recorded'] = remember || correct ? [
    ...(remember ? rows.created.map((row) => ({ ...row, change: 'create' as const })) : []),
    ...(correct ? [{ ...rows.superseded, change: 'supersede' as const }] : []),
  ] : null;
  return {
    referenced: referenced ? [
      { ...rows.supplement, via: 'supplement' },
      { ...rows.episodic, via: 'episodic' },
      { ...rows.recall, via: 'recall' },
      { ...GONE, via: 'recall' },
    ] : null,
    recorded,
  };
}
