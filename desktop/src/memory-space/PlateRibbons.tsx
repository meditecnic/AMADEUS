import { memo } from 'react';

export type Ribbon = { id: string; kind: 'topic' | 'loose' | 'fragment'; d: string; spine: string };

export type PlateRibbonsProps = {
  ribbons: readonly Ribbon[];
  /** The group that is open (or easing open or shut): its ticks are drawn by the unfurl, not here. */
  openId: string | null;
  playing: boolean;
  delayMs: number;
  onPickGroup: (id: string) => void;
};

// One <path> of ticks and one wide transparent <path> to hit per group, however many records
// there are: a 2000-record plate has a few dozen elements here, not 2000.
export const PlateRibbons = memo(function PlateRibbons({ ribbons, openId, playing, delayMs, onPickGroup }: PlateRibbonsProps) {
  const closed = ribbons.filter((ribbon) => ribbon.id !== openId);
  return (
    <g className="p-ribbons" data-testid="plate-ribbons">
      {closed.map((ribbon) => (
        <path key={`t-${ribbon.id}`} className={`p-ribbon is-${ribbon.kind}`} data-group={ribbon.id} d={ribbon.d}
          style={playing ? { animationDelay: `${delayMs}ms` } : undefined} />
      ))}
      {closed.map((ribbon) => (
        <path key={`h-${ribbon.id}`} className="p-ribbon-hit" data-hit="band" data-group={ribbon.id} d={ribbon.spine}
          onClick={(event) => { event.stopPropagation(); onPickGroup(ribbon.id); }} />
      ))}
    </g>
  );
});
