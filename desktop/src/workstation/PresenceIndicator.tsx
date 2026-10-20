export const PRESENCE_LABELS = {
  thinking: '思考中', speaking: '说话中', voicing: '准备语音',
  listening: '聆听中', connecting: '连接中', offline: '未连接',
};
export type PresenceState = keyof typeof PRESENCE_LABELS;

/** Shared with the silent dev preview. State remains owned by the caller. */
export function PresenceIndicator({ state }: { state: PresenceState }) {
  return <div className="ws2-presence" role="status" data-presence={state}><i aria-hidden="true" />{PRESENCE_LABELS[state]}</div>;
}
