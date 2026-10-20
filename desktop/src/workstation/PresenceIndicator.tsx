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

/** Stand-in for the asset-pack thinking spinner: SG steps like a stopwatch, β breathes like a filament. */
export function ThinkingMark() {
  return <span className="ws2-thinking-mark" aria-hidden="true"><b /><b /><b /></span>;
}
