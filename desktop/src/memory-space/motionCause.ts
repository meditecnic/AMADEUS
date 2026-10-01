import type { PresentationChangeCause } from './overviewMotion';

/** Frozen §4.2.2 rank: lower number = higher camera authority. */
export const MOTION_CAUSE_RANK: Record<PresentationChangeCause, number> = {
  selection: 0,
  reselect: 0,
  'post-reconcile-replacement': 0,
  'render-surface': 1,
  'auxiliary-surface': 2,
  'motion-preference': 3,
  disclosure: 4,
  viewport: 5,
};

export function pickHighestMotionCause(
  causes: readonly PresentationChangeCause[],
): PresentationChangeCause {
  if (!causes.length) return 'viewport';
  return causes.reduce((best, cause) => (
    MOTION_CAUSE_RANK[cause] < MOTION_CAUSE_RANK[best] ? cause : best
  ));
}

const SPLIT_ORDER: readonly PresentationChangeCause[] = [
  'viewport',
  'disclosure',
  'motion-preference',
  'auxiliary-surface',
  'render-surface',
];

export function splitOrderedCauses(
  notes: readonly PresentationChangeCause[],
): PresentationChangeCause[] {
  const tagged = new Set(notes);
  const ordered: PresentationChangeCause[] = [];
  for (const cause of SPLIT_ORDER) {
    if (tagged.has(cause)) ordered.push(cause);
  }
  if (tagged.has('post-reconcile-replacement')) ordered.push('post-reconcile-replacement');
  else if (tagged.has('reselect')) ordered.push('reselect');
  else if (tagged.has('selection')) ordered.push('selection');
  return ordered;
}

export type MotionPresentationSnapshot = {
  scopeKey: string;
  selectedId: string | null;
  expandedTopicIds: readonly string[];
  planIdentity: string;
  renderSurface: '3d' | 'list';
  auxiliary: string;
  reducedMotion: boolean;
  viewport: { width: number; height: number };
  insets: { top: number; right: number; bottom: number; left: number };
};

export type MotionArbitration =
  | { type: 'none' }
  | { type: 'scopeChanged' }
  | { type: 'presentationChanged'; cause: PresentationChangeCause };

/**
 * Single caller-side cause path. Explicit notes win for the selection family
 * (reselect vs selection vs post-reconcile). Field changes only fill missing tags.
 * The motion reducer still never infers cause by field diffs.
 */
export function arbitrateMotionDelivery(
  prev: MotionPresentationSnapshot | null,
  next: MotionPresentationSnapshot,
  notes: readonly PresentationChangeCause[],
  scopeChanged: boolean,
): MotionArbitration {
  if (scopeChanged || (prev && prev.scopeKey !== next.scopeKey)) {
    return { type: 'scopeChanged' };
  }
  const ordered = splitOrderedCauses(notes);
  if (!ordered.length) return { type: 'none' };
  return { type: 'presentationChanged', cause: pickHighestMotionCause(ordered) };
}
