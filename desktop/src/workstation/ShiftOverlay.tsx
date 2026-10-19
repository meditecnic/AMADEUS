import { DIVERGENCE } from './Nixie';
import type { Worldline } from './shiftTransaction';

/** Static CSS presentation. Timing and readiness belong to the shared transaction. */
export function ShiftOverlay({ from, to }: { from: Worldline; to: Worldline }) {
  return (
    <div className="shift-fallback">
      <small>WORLDLINE · {from === 'beta' ? 'β → STEINS;GATE' : 'STEINS;GATE → β'}</small>
      <div className="shift-reading" data-shift-reading>{DIVERGENCE[from]}</div>
      <span>目标 · {DIVERGENCE[to]}</span>
      <div className="shift-boundary" aria-hidden="true" />
    </div>
  );
}
