import { evidenceParentId } from './graphPaths';
import { layoutProjection } from './layout';
import type { DisplayNode, MemoryProjection, ProjectionNode } from './types';

export const UNGROUPED_GROUP_ID = '__ungrouped__';

/** Implementation hypothesis: visible children per expanded Topic. */
export const REPRESENTATIVE_CHILD_LIMIT = 3;

export type DisclosureState = {
  expandedTopicIds?: readonly string[];
  selectedId?: string | null;
  focusedId?: string | null;
  query?: string;
};

export type PrimaryResultRow = {
  id: string;
  rank: number;
  kind: ProjectionNode['kind'];
  label: string;
  topicProjectionId: string | null;
};

export type TopicGroup = {
  topicProjectionId: string;
  label: string;
  childResultIds: string[];
  admittedChildIds: string[];
  representativeIds: string[];
  localRemainder: number;
  expanded: boolean;
};

export type SupportingRow = {
  id: string;
  kind: ProjectionNode['kind'];
  label: string;
  role: 'hub' | 'topic' | 'evidence';
  context: true;
};

export type OverviewDisclosurePlan = {
  envelope: MemoryProjection;
  valid: boolean;
  primaryResults: PrimaryResultRow[];
  malformedResultIds: string[];
  topicGroups: TopicGroup[];
  supportingContext: SupportingRow[];
  ungroupedIds: string[];
  anchoredTopicIds: string[];
  visualPlan: {
    captionIds: string[];
    visibleEvidenceIds: string[];
  };
  backendTruncation: {
    shown: MemoryProjection['shown'];
    eligible: MemoryProjection['eligible'];
    truncated: MemoryProjection['truncated'];
  };
  selection: { selectedId: string | null; focusedId: string | null };
};

function nodeLabel(node: ProjectionNode | undefined, fallback: string): string {
  if (!node) return fallback;
  if (node.kind === 'continuity_hub') return node.label_secondary || 'SOUL';
  return node.label;
}

function topicByHasTopic(
  nodeId: string,
  projection: MemoryProjection,
): Extract<ProjectionNode, { kind: 'topic' }> | null {
  const parentId = evidenceParentId(nodeId, projection.edges);
  if (!parentId) return null;
  const hit = projection.nodes.find((node) => node.kind === 'topic' && node.projection_id === parentId);
  return hit && hit.kind === 'topic' ? hit : null;
}

export function deriveOverviewPresentation(
  projection: MemoryProjection,
  disclosure: DisclosureState = {},
): OverviewDisclosurePlan {
  const byId = new Map(projection.nodes.map((node) => [node.projection_id, node]));
  const expanded = new Set(disclosure.expandedTopicIds ?? []);
  const resultSet = new Set(projection.result_ids);

  const seenIds = new Set<string>();
  const duplicateIds = new Set<string>();
  for (const id of projection.result_ids) {
    if (seenIds.has(id)) duplicateIds.add(id);
    seenIds.add(id);
  }
  const malformedResultIds = projection.result_ids.filter((id) => {
    const node = byId.get(id);
    return !node || node.kind === 'continuity_hub' || duplicateIds.has(id);
  });
  if (malformedResultIds.length) {
    return {
      envelope: projection,
      valid: false,
      primaryResults: [],
      malformedResultIds,
      topicGroups: [],
      supportingContext: [],
      ungroupedIds: [],
      anchoredTopicIds: [],
      visualPlan: { captionIds: [], visibleEvidenceIds: [] },
      backendTruncation: {
        shown: projection.shown,
        eligible: projection.eligible,
        truncated: projection.truncated,
      },
      selection: {
        selectedId: disclosure.selectedId ?? null,
        focusedId: disclosure.focusedId ?? null,
      },
    };
  }

  const primaryResults: PrimaryResultRow[] = [];
  projection.result_ids.forEach((id, index) => {
    const node = byId.get(id);
    if (!node) return;
    const topic = topicByHasTopic(id, projection);
    primaryResults.push({
      id,
      rank: index + 1,
      kind: node.kind,
      label: nodeLabel(node, node.projection_id),
      topicProjectionId: topic?.projection_id ?? null,
    });
  });

  const centerId = projection.center.projection_id;
  const anchoredTopicIds = [...new Set(
    projection.edges
      .filter((edge) => {
        if (edge.kind !== 'hub_to_anchor') return false;
        return (edge.from === centerId && byId.get(edge.to)?.kind === 'topic')
          || (edge.to === centerId && byId.get(edge.from)?.kind === 'topic');
      })
      .map((edge) => (edge.from === centerId ? edge.to : edge.from)),
  )];

  const childrenByTopic = new Map<string, string[]>();
  const ungroupedIds: string[] = [];
  for (const node of projection.nodes) {
    if (node.kind !== 'fact' && node.kind !== 'experience') continue;
    const topic = topicByHasTopic(node.projection_id, projection);
    if (!topic) {
      ungroupedIds.push(node.projection_id);
      continue;
    }
    const list = childrenByTopic.get(topic.projection_id) ?? [];
    list.push(node.projection_id);
    childrenByTopic.set(topic.projection_id, list);
  }

  const topicGroups: TopicGroup[] = projection.nodes
    .filter((node): node is Extract<ProjectionNode, { kind: 'topic' }> => node.kind === 'topic')
    .map((topic) => {
      const admittedChildIds = childrenByTopic.get(topic.projection_id) ?? [];
      const childResultIds = projection.result_ids.filter((id) => admittedChildIds.includes(id));
      const isExpanded = expanded.has(topic.projection_id);
      const representativeIds = isExpanded
        ? admittedChildIds.slice(0, REPRESENTATIVE_CHILD_LIMIT)
        : [];
      const hidden = isExpanded
        ? Math.max(0, admittedChildIds.length - representativeIds.length)
        : admittedChildIds.length;
      return {
        topicProjectionId: topic.projection_id,
        label: topic.label,
        childResultIds,
        admittedChildIds,
        representativeIds,
        localRemainder: hidden,
        expanded: isExpanded,
      };
    });

  const supportingContext: SupportingRow[] = [];
  for (const node of projection.nodes) {
    if (resultSet.has(node.projection_id)) continue;
    const role = node.kind === 'continuity_hub' ? 'hub' : node.kind === 'topic' ? 'topic' : 'evidence';
    supportingContext.push({
      id: node.projection_id,
      kind: node.kind,
      label: nodeLabel(node, node.projection_id),
      role,
      context: true,
    });
  }

  const captionIds = [
    ...projection.nodes.filter((node) => node.kind === 'continuity_hub' || node.kind === 'topic').map((node) => node.projection_id),
    ...topicGroups.flatMap((group) => group.representativeIds),
  ];
  if (disclosure.selectedId) captionIds.push(disclosure.selectedId);
  if (disclosure.focusedId) captionIds.push(disclosure.focusedId);

  const query = disclosure.query?.trim() ?? '';
  const visibleEvidenceIds = [
    ...topicGroups.flatMap((group) => group.representativeIds),
  ];
  if (query) visibleEvidenceIds.push(...ungroupedIds);
  if (disclosure.selectedId) visibleEvidenceIds.push(disclosure.selectedId);
  if (disclosure.focusedId) visibleEvidenceIds.push(disclosure.focusedId);

  return {
    envelope: projection,
    valid: true,
    primaryResults,
    malformedResultIds,
    topicGroups,
    supportingContext,
    ungroupedIds,
    anchoredTopicIds,
    visualPlan: {
      captionIds: [...new Set(captionIds)],
      visibleEvidenceIds: [...new Set(visibleEvidenceIds)],
    },
    backendTruncation: {
      shown: projection.shown,
      eligible: projection.eligible,
      truncated: projection.truncated,
    },
    selection: {
      selectedId: disclosure.selectedId ?? null,
      focusedId: disclosure.focusedId ?? null,
    },
  };
}

export function outlineIds(plan: OverviewDisclosurePlan): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  const push = (id: string) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    ids.push(id);
  };
  for (const row of plan.primaryResults) push(row.id);
  for (const row of plan.supportingContext) push(row.id);
  return ids;
}

export function visibleOutlineIds(
  plan: OverviewDisclosurePlan,
  browse: 'ranked' | 'grouped',
  openGroupIds: readonly string[],
): string[] {
  if (!plan.valid) return [];
  if (browse === 'ranked') return outlineIds(plan);
  const open = new Set(openGroupIds);
  const ids: string[] = [];
  const seen = new Set<string>();
  const push = (id: string) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    ids.push(id);
  };
  const grouped = new Set(plan.topicGroups.flatMap((group) => group.childResultIds));
  for (const group of plan.topicGroups) {
    if (!group.childResultIds.length || !open.has(group.topicProjectionId)) continue;
    for (const id of group.childResultIds) push(id);
  }
  if (open.has(UNGROUPED_GROUP_ID)) {
    for (const row of plan.primaryResults) {
      if (!grouped.has(row.id)) push(row.id);
    }
  }
  for (const row of plan.supportingContext) push(row.id);
  return ids;
}

export function displayNodesFromPlan(plan: OverviewDisclosurePlan): DisplayNode[] {
  if (!plan.valid) return [];
  const allowed = new Set(plan.visualPlan.visibleEvidenceIds);
  if (plan.selection.selectedId) allowed.add(plan.selection.selectedId);
  if (plan.selection.focusedId) allowed.add(plan.selection.focusedId);
  return layoutProjection(plan.envelope).filter((node) => {
    if (node.kind === 'continuity_hub' || node.kind === 'topic') return true;
    return allowed.has(node.id);
  });
}

function kindNameOf(kind: ProjectionNode['kind']): string {
  if (kind === 'fact') return '事实';
  if (kind === 'experience') return '经历';
  if (kind === 'topic') return '主题';
  return 'SOUL';
}

function nodeDisplayLabel(node: ProjectionNode): string {
  return node.kind === 'continuity_hub' ? 'SOUL' : node.label;
}

export function accessibleNameById(plan: OverviewDisclosurePlan): Map<string, string> {
  const names = new Map<string, string>();
  const kindLabelIds = new Map<string, string[]>();
  for (const node of plan.envelope.nodes) {
    const label = nodeDisplayLabel(node);
    const key = `${node.kind}\0${label}`;
    const list = kindLabelIds.get(key) ?? [];
    list.push(node.projection_id);
    kindLabelIds.set(key, list);
  }
  const ungrouped = new Set(plan.ungroupedIds);
  const supporting = new Set(plan.supportingContext.map((row) => row.id));
  const ungroupedCounts = new Map<string, number>();
  const supportCounts = new Map<string, number>();
  for (const node of plan.envelope.nodes) {
    const id = node.projection_id;
    const label = nodeDisplayLabel(node);
    const key = `${node.kind}\0${label}`;
    const peers = kindLabelIds.get(key) ?? [id];
    const collide = peers.length > 1;
    const ordinal = peers.indexOf(id) + 1;
    const kindName = kindNameOf(node.kind);
    if (ungrouped.has(id) && (node.kind === 'fact' || node.kind === 'experience')) {
      const local = (ungroupedCounts.get(key) ?? 0) + 1;
      ungroupedCounts.set(key, local);
      names.set(id, `未归组 ${kindName} ${label} ${local}`);
      continue;
    }
    if (supporting.has(id)) {
      const local = (supportCounts.get(key) ?? 0) + 1;
      supportCounts.set(key, local);
      names.set(
        id,
        collide ? `上下文 ${kindName} ${label} ${local}` : `上下文 ${label}`,
      );
      continue;
    }
    names.set(id, collide ? `${kindName} ${label} ${ordinal}` : label);
  }
  return names;
}

export function ungroupedNameById(plan: OverviewDisclosurePlan): Map<string, string> {
  return accessibleNameById(plan);
}
