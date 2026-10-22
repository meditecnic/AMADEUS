import { model } from './fixtures';

const models = [model, ...Array.from({ length: 11 }, (_, index) => {
  const id = index === 10
    ? 'mock-research-bilingual-context-extended-reasoning-preview-2026-10'
    : `mock-research-${String(index + 1).padStart(2, '0')}`;
  return { ...model, id, request_id: id, display_name: `本地观测模型 ${index + 1} · 双语推理与长上下文预览`, source: 'dynamic' };
})];

const history = (line: string, session: number, turns: number) => Array.from({ length: turns }, (_, turn) => [
  { role: 'user', content: `${line} / 会话 ${session} / 记录 ${turn + 1}：请核对这一次的观测与实验条件。` },
  {
    role: 'assistant',
    content: `${line}の観測${turn + 1}よ。条件を揃えてから、記録と仮説を一つずつ照合しましょう。結論を急ぐ必要はないわ。`,
    translation: `${line}的第 ${turn + 1} 条观测。先统一实验条件，再逐项核对记录与假设，暂时不必急着得出结论。`,
  },
]).flat();

/** Bounded, deterministic data for overflow, cascade caps, distinct histories and long labels. */
export const workstationFixture = {
  models,
  conversations: ['steins_gate', 'beta'].flatMap((worldline) => Array.from({ length: 16 }, (_, index) => ({
    id: `workstation-${worldline}-${index + 1}`,
    worldline,
    title: index === 15 ? `${worldline} · 长标题观测档案：多次实验条件、双语记录与下一轮模型核对` : `${worldline} · 观测档案 ${index + 1}`,
    identity_mode: index % 2 ? 'self' : 'okabe',
    model_id: models[index % models.length].id,
    messages: history(worldline, index + 1, index === 0 ? 24 : 2),
  }))),
  dialogue: {
    sentences: Array.from({ length: 30 }, (_, index) => ({
      ja: `観測${index + 1}。読み返す時間も実験の一部よ。記録を確かめながら、次の条件を揃えましょう。`,
      zh: index === 4 ? '' : `第 ${index + 1} 条观测。回头阅读也是实验的一部分，请边核对记录边准备下一组条件。`,
      emotion: index % 2 ? 'intellectual' : 'neutral',
      delayMs: index === 0 ? 1000 : 450,
    })),
    completionDelayMs: 400,
  },
};
