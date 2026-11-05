// The opening shot (DIRECTION 15.5), as a pure function of time so the schedule can be tested.
// Times are in SG milliseconds; β plays the same shot 1.3 times slower (the caller divides real time by `k`).
import { PLATE_POSE, clamp, easeInOut, easeOut, lerp, type Pose } from './coneFrame';

const span = (t: number, a: number, b: number) => clamp((t - a) / (b - a), 0, 1);

export type EntranceFrame = {
  pose: Pose;
  /** `uReveal` of the structure and tick layers: the pen draws the rings, then the rest (0..2). */
  struct: number;
  /** Stars appear in the order they were written down. */
  stars: number;
  /** Connections first (one unit = one second), then the "new" crosses one after another. */
  world: number;
  rim: number;
  /** Words fade in once the cone has formed. */
  labels: number;
};

/** The plate for the first 650 ms, then the cone drops out of it while the camera comes down and the zoom opens up. */
export function entranceAt(t: number, target: Pose): EntranceFrame {
  const flat = easeInOut(span(t, 650, 1900));
  return {
    pose: {
      yaw: lerp(0, target.yaw, easeInOut(span(t, 650, 2100))),
      el: lerp(PLATE_POSE.el, target.el, flat),
      dist: lerp(1, target.dist, flat),
      fov: lerp(PLATE_POSE.fov, target.fov, easeOut(span(t, 700, 1900))),
      tx: 0, tz: 0,
      ty: lerp(0, target.ty, flat),
      flat: 1 - flat,
    },
    struct: span(t, 0, 900) + span(t, 900, 1700),
    stars: span(t, 150, 1100) * 1.05,
    world: (t - 1500) / 1000,
    rim: span(t, 600, 1500),
    labels: span(t, 1700, 2200),
  };
}

/** Logical length of the shot: 2.6 s, or longer if there are many "new" crosses to place one by one. */
export function entranceLength(worldReveal: number): number {
  return Math.max(2600, 1500 + worldReveal * 1000 + 100);
}

/** The world layer's reveal runs to 1.0 (connections, 1.5-2.5 s), or past the last "new" cross. */
export function worldRevealEnd(fresh: number): number {
  if (!fresh) return 1;
  const step = Math.min(0.42, 2.4 / fresh);
  return 1.2 + (fresh - 1) * step + step * 0.98 + 0.05;
}
