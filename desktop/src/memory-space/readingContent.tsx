import type { DisplayNode, ProjectionNode } from './types';

export type ReadingKind = '主题' | '事实' | '经历';

export type ReadingCard = {
  title: string;
  kind: ReadingKind;
  kindKey: 'topic' | 'fact' | 'experience';
  summary: string;
  provenance: {
    pinned: boolean | null;
    updatedAt: string | null;
    topicId: string | null;
    conversationId: string | null;
  };
};

const KIND_ZH: Record<'topic' | 'fact' | 'experience', ReadingKind> = {
  topic: '主题',
  fact: '事实',
  experience: '经历',
};

export function readingCardFromNode(node: DisplayNode | ProjectionNode): ReadingCard | null {
  const raw = 'node' in node ? node.node : node;
  if (raw.kind === 'continuity_hub') return null;
  if (raw.kind === 'topic') {
    return {
      title: raw.label,
      kind: KIND_ZH.topic,
      kindKey: 'topic',
      summary: `${raw.fact_count} 条事实 · ${raw.experience_count} 条经历`,
      provenance: {
        pinned: null,
        updatedAt: null,
        topicId: raw.topic_id,
        conversationId: null,
      },
    };
  }
  if (raw.kind === 'fact') {
    return {
      title: raw.label,
      kind: KIND_ZH.fact,
      kindKey: 'fact',
      summary: raw.label,
      provenance: {
        pinned: raw.is_pinned,
        updatedAt: raw.updated_at,
        topicId: raw.topic_id,
        conversationId: null,
      },
    };
  }
  return {
    title: raw.label,
    kind: KIND_ZH.experience,
    kindKey: 'experience',
    summary: raw.label,
    provenance: {
      pinned: raw.is_pinned,
      updatedAt: raw.updated_at,
      topicId: null,
      conversationId: raw.conversation_id,
    },
  };
}

export function ReadingFields({ card }: { card: ReadingCard }) {
  return (
    <>
      <p className="memory-reading-kind" data-testid="reading-kind">{card.kind}</p>
      <h2 data-testid="reading-title">{card.title}</h2>
      <p data-testid="reading-summary">{card.summary}</p>
      <dl className="memory-reading-provenance" data-testid="reading-provenance">
        {card.provenance.pinned !== null ? (
          <div><dt>置顶</dt><dd>{card.provenance.pinned ? '是' : '否'}</dd></div>
        ) : null}
        {card.provenance.updatedAt ? (
          <div><dt>更新</dt><dd>{card.provenance.updatedAt}</dd></div>
        ) : null}
        {card.provenance.topicId ? (
          <div><dt>主题</dt><dd>{card.provenance.topicId}</dd></div>
        ) : null}
        {card.provenance.conversationId ? (
          <div><dt>会话</dt><dd>{card.provenance.conversationId}</dd></div>
        ) : null}
      </dl>
    </>
  );
}
