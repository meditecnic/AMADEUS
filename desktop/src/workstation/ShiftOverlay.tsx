import { DIVERGENCE, Nixie, WORLDLINE_NAME, useTunedValue } from './Nixie';
import type { Worldline } from './shiftTransaction';

/** Static CSS presentation of the shift. Timing and readiness belong to the shared transaction;
 *  this only shows what the controller has locked (the source until then). */
export function ShiftOverlay({ from, to, reading }: { from: Worldline; to: Worldline; reading: string }) {
  const tuned = useTunedValue(reading, { duration: 1100 });
  return (
    <div className="shift-fallback">
      <small>WORLDLINE · {WORLDLINE_NAME[from]} → {WORLDLINE_NAME[to]}</small>
      <Nixie value={tuned.text} state={tuned} label={reading} size="xl" title={`FIG.2 · ${WORLDLINE_NAME[from]} → ${WORLDLINE_NAME[to]}`} />
      <span className="shift-target">目标 · {DIVERGENCE[to]}</span>
      <div className="shift-boundary" aria-hidden="true" />
    </div>
  );
}
