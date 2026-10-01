import { describe, expect, it } from 'vitest';

import { arbitrateMotionDelivery, pickHighestMotionCause, splitOrderedCauses } from './motionCause';
import type { MotionPresentationSnapshot } from './motionCause';

const base: MotionPresentationSnapshot = {
  scopeKey: 's|steins_gate|okabe',
  selectedId: null,
  expandedTopicIds: [],
  planIdentity: 't0|',
  renderSurface: '3d',
  auxiliary: 'none',
  reducedMotion: false,
  viewport: { width: 1280, height: 800 },
  insets: { top: 64, right: 16, bottom: 16, left: 16 },
};

describe('motion cause arbitration', () => {
  it('picks the frozen highest-authority cause', () => {
    expect(pickHighestMotionCause(['viewport', 'selection', 'auxiliary-surface'])).toBe('selection');
    expect(pickHighestMotionCause(['disclosure', 'render-surface'])).toBe('render-surface');
    expect(pickHighestMotionCause(['viewport'])).toBe('viewport');
  });

  it('keeps scopeChanged exclusive', () => {
    expect(arbitrateMotionDelivery(base, { ...base, scopeKey: 's|steins_gate|self' }, ['selection'], false)).toEqual({
      type: 'scopeChanged',
    });
    expect(arbitrateMotionDelivery(base, { ...base, selectedId: 'topic:t1' }, ['selection'], true)).toEqual({
      type: 'scopeChanged',
    });
  });

  it('resolves selection + auxiliary + viewport to selection', () => {
    const next: MotionPresentationSnapshot = {
      ...base,
      selectedId: 'topic:t1',
      auxiliary: 'list',
      viewport: { width: 504, height: 900 },
    };
    expect(arbitrateMotionDelivery(base, next, ['selection', 'auxiliary-surface', 'viewport'], false)).toEqual({
      type: 'presentationChanged',
      cause: 'selection',
    });
  });

  it('does not let a later viewport tag beat an explicit reselect', () => {
    expect(arbitrateMotionDelivery(base, { ...base, insets: { ...base.insets, top: 80 } }, ['reselect', 'viewport'], false)).toEqual({
      type: 'presentationChanged',
      cause: 'reselect',
    });
  });

  it('uses post-reconcile-replacement when that note is present', () => {
    expect(arbitrateMotionDelivery(
      base,
      { ...base, selectedId: 'hub', planIdentity: 't1|fact:2' },
      ['post-reconcile-replacement', 'disclosure'],
      false,
    )).toEqual({
      type: 'presentationChanged',
      cause: 'post-reconcile-replacement',
    });
  });

  it('stale viewport field diffs cannot overwrite an unflushed selection note', () => {
    const selected: MotionPresentationSnapshot = { ...base, selectedId: 'topic:t1' };
    expect(arbitrateMotionDelivery(
      selected,
      { ...selected, viewport: { width: 900, height: 700 }, insets: { ...base.insets, bottom: 320 } },
      ['selection'],
      false,
    )).toEqual({ type: 'presentationChanged', cause: 'selection' });
  });

  it('a later viewport-only flush is viewport after selection notes were already consumed', () => {
    expect(arbitrateMotionDelivery(
      { ...base, selectedId: 'topic:t1' },
      { ...base, selectedId: 'topic:t1', viewport: { width: 900, height: 700 } },
      ['viewport'],
      false,
    )).toEqual({ type: 'presentationChanged', cause: 'viewport' });
  });

  it('does not invent a cause from field diffs without an explicit UI note', () => {
    expect(arbitrateMotionDelivery(
      base,
      { ...base, selectedId: 'topic:t1', viewport: { width: 900, height: 700 }, reducedMotion: true },
      [],
      false,
    )).toEqual({ type: 'none' });
  });

  it('keeps frozen split order so a bundled note list cannot drop disclosure', () => {
    expect(splitOrderedCauses(['selection', 'motion-preference', 'disclosure'])).toEqual([
      'disclosure',
      'motion-preference',
      'selection',
    ]);
  });
});
