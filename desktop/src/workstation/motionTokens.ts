/** Motion (the library) speaking the motion constitution in design-system/motion.css:
 *  the same SG ladder and easing, β = SG × 1.3 with its overshoot curve. */
export type WsWorldline = 'steins_gate' | 'beta';
type Step = 'instant' | 'quick' | 'base' | 'panel' | 'page';

const SG_MS: Record<Step, number> = { instant: 60, quick: 120, base: 200, panel: 320, page: 480 };
const EASE = {
  steins_gate: [0.2, 0, 0, 1] as [number, number, number, number],
  beta: [0.5, 0, 0.15, 1.12] as [number, number, number, number],
};

export function wsTransition(worldline: WsWorldline, step: Step = 'base') {
  const k = worldline === 'beta' ? 1.3 : 1;
  return { duration: (SG_MS[step] * k) / 1000, ease: EASE[worldline] };
}
