import { accessibleNameById, type OverviewDisclosurePlan, type TopicGroup } from './overviewPresentation';
import type { ProjectionNode } from './types';

function topicChildren(
  group: TopicGroup | undefined,
  selectedId: string | null,
  focusedId: string | null | undefined,
): string[] {
  const located = [selectedId, focusedId].filter((id): id is string => {
    if (!id || !group) return false;
    return group.admittedChildIds.includes(id);
  });
  return [...new Set([
    ...(group?.representativeIds ?? []),
    ...(group?.admittedChildIds ?? []),
    ...located,
  ])];
}

function evidenceButton(
  id: string,
  node: Extract<ProjectionNode, { kind: 'fact' | 'experience' }>,
  selectedId: string | null,
  showCaption: boolean,
  onSelect: (id: string | null) => void,
  ariaLabel?: string,
) {
  return (
    <li key={id}>
      <button
        type="button"
        className={`memory-compatibility-evidence${selectedId === id ? ' is-selected' : ''}`}
        aria-current={selectedId === id ? 'true' : undefined}
        aria-label={ariaLabel ?? node.label}
        onClick={() => onSelect(id)}
        onFocus={(event) => event.currentTarget.scrollIntoView?.({ block: 'nearest' })}
      >
        {showCaption ? node.label : (node.kind === 'fact' ? '事实' : '经历')}
      </button>
    </li>
  );
}

export function CompatibilityView(props: {
  plan: OverviewDisclosurePlan;
  selectedId: string | null;
  focusedId?: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { plan } = props;
  const envelope = plan.envelope;
  const topics = envelope.nodes.filter((node) => node.kind === 'topic');
  const anchored = new Set(plan.anchoredTopicIds);
  const soulTopics = topics.filter((topic) => anchored.has(topic.projection_id));
  const orphanTopics = topics.filter((topic) => !anchored.has(topic.projection_id));
  const caption = new Set(plan.visualPlan.captionIds);
  const nodeById = new Map(envelope.nodes.map((node) => [node.projection_id, node]));
  const names = accessibleNameById(plan);

  return (
    <div
      className="memory-compatibility"
      data-testid="memory-compatibility"
      data-webgl="false"
    >
      <p className="memory-compatibility-label">兼容视图 / COMPATIBILITY VIEW</p>
      <div
        className="memory-compatibility-tree"
        data-testid="memory-compatibility-scroll"
        data-overflow="vertical"
        role="region"
        aria-label="记忆结构"
        style={{ minHeight: 0, maxHeight: '100%', overflowY: 'auto' }}
      >
        <button
          type="button"
          className={`memory-compatibility-hub${props.selectedId === envelope.center.projection_id ? ' is-selected' : ''}`}
          aria-current={props.selectedId === envelope.center.projection_id ? 'true' : undefined}
          onClick={() => props.onSelect(envelope.center.projection_id)}
        >
          SOUL
        </button>
        <ul className="memory-compatibility-topics" data-region="soul-topics">
          {soulTopics.map((topic) => {
            const group = plan.topicGroups.find((item) => item.topicProjectionId === topic.projection_id);
            const children = topicChildren(group, props.selectedId, props.focusedId);
            return (
              <li key={topic.projection_id}>
                <button
                  type="button"
                  className={`memory-compatibility-topic${props.selectedId === topic.projection_id ? ' is-selected' : ''}`}
                  aria-current={props.selectedId === topic.projection_id ? 'true' : undefined}
                  aria-expanded={children.length > 0}
                  aria-label={names.get(topic.projection_id) ?? topic.label}
                  onClick={() => props.onSelect(topic.projection_id)}
                >
                  {topic.label}
                  {group && group.localRemainder > 0 ? (
                    <span className="memory-compatibility-remainder"> · 另有 {group.localRemainder}</span>
                  ) : null}
                </button>
                {children.length ? (
                  <ul>
                    {children.map((id) => {
                      const node = nodeById.get(id);
                      if (!node || (node.kind !== 'fact' && node.kind !== 'experience')) return null;
                      return evidenceButton(id, node, props.selectedId, caption.has(id), props.onSelect, names.get(id));
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
        {orphanTopics.length || plan.ungroupedIds.length ? (
          <ul className="memory-compatibility-ungrouped" data-region="contextual" aria-label="未归组">
            {orphanTopics.map((topic) => {
              const group = plan.topicGroups.find((item) => item.topicProjectionId === topic.projection_id);
              const children = topicChildren(group, props.selectedId, props.focusedId);
              return (
                <li key={topic.projection_id}>
                  <button
                    type="button"
                    aria-current={props.selectedId === topic.projection_id ? 'true' : undefined}
                    aria-expanded={children.length > 0}
                    aria-label={names.get(topic.projection_id) ?? topic.label}
                    onClick={() => props.onSelect(topic.projection_id)}
                  >
                    {topic.label}
                  </button>
                  {children.length ? (
                    <ul>
                      {children.map((id) => {
                        const node = nodeById.get(id);
                        if (!node || (node.kind !== 'fact' && node.kind !== 'experience')) return null;
                        return evidenceButton(id, node, props.selectedId, caption.has(id), props.onSelect, names.get(id));
                      })}
                    </ul>
                  ) : null}
                </li>
              );
            })}
            {plan.ungroupedIds.map((id) => {
              const node = nodeById.get(id);
              if (!node) return null;
              const label = node.kind === 'continuity_hub' ? 'SOUL' : node.label;
              return (
                <li key={id}>
                  <button
                    type="button"
                    className={props.selectedId === id ? 'is-selected' : undefined}
                    aria-current={props.selectedId === id ? 'true' : undefined}
                    aria-label={names.get(id) ?? `未归组 ${label}`}
                    onClick={() => props.onSelect(id)}
                  >
                    {label}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
