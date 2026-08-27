import {
  formatTimeHuman,
  TOPIC_UNAVAILABLE_NOTICE,
  topicAttributionForFact,
  UNGROUPED_NOTICE,
} from './readingDetails';
import type { DisplayNode, MemoryProjection, ProjectionNode } from './types';

export type ReadingKind = '主题' | '事实' | '经历';

// P1R-3 §7.1 / §5.2: the envelope card never duplicates the title as a
// second label, never prints raw ids or raw ISO in default slots, and takes
// Topic labels only from canonical envelope data. Payload-owned content
// (verified text, versions, provenance) is rendered by the evidence layers.
export type ReadingCard = {
  title: string;
  kind: ReadingKind;
  kindKey: 'topic' | 'fact' | 'experience';
  summary: string | null;
  provenance: {
    pinned: boolean | null;
    updatedAtHuman: string | null;
    updatedAtRaw: string | null;
    topicLabel: string | null;
    conversationIdRaw: string | null;
  };
};

const KIND_ZH: Record<'topic' | 'fact' | 'experience', ReadingKind> = {
  topic: '主题',
  fact: '事实',
  experience: '经历',
};

function factTopicLabel(
  node: Extract<ProjectionNode, { kind: 'fact' }>,
  envelope: MemoryProjection | null | undefined,
): string | null {
  if (!envelope) return null;
  const attribution = topicAttributionForFact(
    { projectionId: node.projection_id, topicId: node.topic_id },
    envelope,
  );
  if (attribution.kind === 'grouped') return attribution.label;
  if (attribution.kind === 'ungrouped') return UNGROUPED_NOTICE;
  return TOPIC_UNAVAILABLE_NOTICE;
}

export function readingCardFromNode(
  node: DisplayNode | ProjectionNode,
  envelope?: MemoryProjection | null,
): ReadingCard | null {
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
        updatedAtHuman: null,
        updatedAtRaw: null,
        topicLabel: null,
        conversationIdRaw: null,
      },
    };
  }
  if (raw.kind === 'fact') {
    return {
      title: raw.label,
      kind: KIND_ZH.fact,
      kindKey: 'fact',
      summary: null,
      provenance: {
        pinned: raw.is_pinned,
        updatedAtHuman: formatTimeHuman(raw.updated_at),
        updatedAtRaw: raw.updated_at,
        topicLabel: factTopicLabel(raw, envelope),
        conversationIdRaw: null,
      },
    };
  }
  return {
    title: raw.label,
    kind: KIND_ZH.experience,
    kindKey: 'experience',
    summary: null,
    provenance: {
      pinned: raw.is_pinned,
      updatedAtHuman: formatTimeHuman(raw.updated_at),
      updatedAtRaw: raw.updated_at,
      topicLabel: null,
      conversationIdRaw: raw.conversation_id,
    },
  };
}

export function ReadingProvenance({ provenance }: { provenance: ReadingCard['provenance'] }) {
  return (
    <dl className="memory-reading-provenance" data-testid="reading-provenance">
      {provenance.pinned !== null ? (
        <div><dt>置顶</dt><dd>{provenance.pinned ? '是' : '否'}</dd></div>
      ) : null}
      {provenance.updatedAtHuman ? (
        <div><dt>更新</dt><dd data-testid="reading-updated-human">{provenance.updatedAtHuman}</dd></div>
      ) : null}
      {provenance.topicLabel ? (
        <div><dt>归属</dt><dd data-testid="reading-topic-label">{provenance.topicLabel}</dd></div>
      ) : null}
    </dl>
  );
}

export function ReadingFields({ card }: { card: ReadingCard }) {
  return (
    <>
      <p className="memory-reading-kind" data-testid="reading-kind">{card.kind}</p>
      <h2 data-testid="reading-title">{card.title}</h2>
      {card.summary ? <p data-testid="reading-summary">{card.summary}</p> : null}
      <ReadingProvenance provenance={card.provenance} />
    </>
  );
}
