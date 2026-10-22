import { animate, motionValue, springValue } from 'motion/react';

/** Her voice as one signal. The player writes the raw RMS every audio frame; the scope and the mouth both read the
 *  same smoothed envelope, so the trace and the lips move together (and at frame rate rather than a 10 Hz state). */
export const voiceLevel = motionValue(0);
/** Critically damped follower (≈130 ms to settle): steady enough for lip frames, quick enough for syllables. */
export const voiceEnvelope = springValue(voiceLevel, { stiffness: 900, damping: 60, restDelta: 0.0005, restSpeed: 0.001 });

/** A small, decaying blip while the user types — she hears the keys. 0..1, decorative only. */
export const voiceHeard = motionValue(0);
let heardAnimation: { stop: () => void } | null = null;
export function hearKeystroke(strength = 0.5) {
  const from = Math.min(1, Math.max(voiceHeard.get(), 0) + strength);
  heardAnimation?.stop();
  heardAnimation = animate(voiceHeard, [from, 0], { duration: 0.6, ease: [0.2, 0, 0, 1] });
}

/** Mouth frame from the envelope (raw RMS units: 1 closed, 2 half, 3 open), with hysteresis so it does not chatter. */
export function mouthFrame(env: number, previous: 1 | 2 | 3): 1 | 2 | 3 {
  if (previous === 3) return env < 0.2 ? (env < 0.035 ? 1 : 2) : 3;
  if (previous === 2) return env > 0.25 ? 3 : env < 0.035 ? 1 : 2;
  return env > 0.25 ? 3 : env > 0.05 ? 2 : 1;
}
