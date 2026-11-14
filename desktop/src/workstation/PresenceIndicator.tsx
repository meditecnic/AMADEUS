import { f1, gearOutline } from './drafting';

export const PRESENCE_LABELS = {
  thinking: '思考中', speaking: '说话中', voicing: '准备语音',
  listening: '聆听中', connecting: '连接中', offline: '未连接',
};
export type PresenceState = keyof typeof PRESENCE_LABELS;

/** States that are waiting on something real; only these may carry the lamp's small loop. */
const PENDING: ReadonlySet<PresenceState> = new Set(['connecting', 'voicing']);

/**
 * Shared with the silent dev preview. State remains owned by the caller: the label
 * switches in the same render (never stale), and only its short entrance is presentation.
 */
export function PresenceIndicator({ state }: { state: PresenceState }) {
  return <div className="ws2-presence" role="status" data-presence={state}>
    <i aria-hidden="true" />
    <span key={state} className="ws2-presence-label">{PRESENCE_LABELS[state]}</span>
    {PENDING.has(state) && <span className="ws2-presence-ticks" aria-hidden="true"><b /><b /><b /></span>}
  </div>;
}

/*
 * Stand-in for the asset pack's thinking ring, drawn from each sheet's own figure:
 *  - SG: two wheels of the clock movement in mesh. 12 and 7 teeth: one turn of the large wheel is twelve teeth of
 *    the small one, 12/7 of a turn, so the loop is seamless.
 *  - β: the Lorenz attractor in small, a bright stretch running along it.
 * Both are drawn in on arrival; under reduced motion they stand still (log.css).
 */
const GEAR_A = { at: [11, 12.5], n: 12, r: 8 };
const GEAR_B = (() => {
  const r = (GEAR_A.r * 7) / 12, d = GEAR_A.r + r, a = (-25 * Math.PI) / 180;
  return { at: [f1(GEAR_A.at[0] + d * Math.cos(a)), f1(GEAR_A.at[1] + d * Math.sin(a))], n: 7, r };
})();
const WHEEL_A = gearOutline(GEAR_A.n, GEAR_A.r);
const WHEEL_B = gearOutline(GEAR_B.n, GEAR_B.r);
/* a tooth of the large wheel points at the small one (-25°); the small wheel turns a gap towards it */
const MESH_B = f1(155 - 360 / 7 / 2 - 2 * (360 / 7));
/* nine time units of the attractor, taken after the orbit has settled and while it crosses between the lobes */
const SMALL_ORBIT = (() => {
  const f = ([x, y, z]: number[]) => [10 * (y - x), x * (28 - z) - y, x * y - (8 / 3) * z];
  let p = [1, 1, 1];
  let d = '';
  for (let i = 0; i < 2500; i++) {
    const k1 = f(p), k2 = f(p.map((v, j) => v + 0.005 * k1[j])), k3 = f(p.map((v, j) => v + 0.005 * k2[j])), k4 = f(p.map((v, j) => v + 0.01 * k3[j]));
    p = p.map((v, j) => v + (0.01 / 6) * (k1[j] + 2 * k2[j] + 2 * k3[j] + k4[j]));
    if (i >= 1600) d += `${i === 1600 ? 'M' : 'L'}${f1(p[0] * 0.62)} ${f1(-(p[2] - 25.5) * 0.62)}`;
  }
  return d;
})();

export function ThinkingMark({ worldline = 'steins_gate' }: { worldline?: 'steins_gate' | 'beta' }) {
  if (worldline === 'beta') {
    return (
      <svg className="ws2-thinking-mark is-orbit" viewBox="0 0 32 24" aria-hidden="true">
        <g transform="translate(16 12.5)">
          <path className="orbit" pathLength={100} d={SMALL_ORBIT} />
          <path className="tracer" pathLength={100} d={SMALL_ORBIT} />
        </g>
      </svg>
    );
  }
  return (
    <svg className="ws2-thinking-mark is-gears" viewBox="0 0 32 24" aria-hidden="true">
      <g transform={`translate(${GEAR_A.at[0]} ${GEAR_A.at[1]}) rotate(-25)`}>
        <g className="turn-a"><path className="gear" pathLength={1} d={WHEEL_A} /><circle r={2.4} /><circle className="pin" r={0.8} /></g>
      </g>
      <g transform={`translate(${GEAR_B.at[0]} ${GEAR_B.at[1]}) rotate(${MESH_B})`}>
        <g className="turn-b"><path className="gear" pathLength={1} d={WHEEL_B} /><circle r={1.5} /></g>
      </g>
    </svg>
  );
}
