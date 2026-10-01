import type { ProviderMetadata } from '../components/ProviderControls';
import type { ChatCheckState } from './ConnectionCheck';
import { ProviderMark } from './ProviderMark';

export type ConnectionLamp = 'ok' | 'manual' | 'idle' | 'checking' | 'bad' | 'key' | 'pending' | 'off';

const LAMP_LABEL: Record<ConnectionLamp, string> = {
  ok: '可用',
  manual: '手动启用',
  idle: '未测试',
  checking: '测试中',
  bad: '测试失败',
  key: '缺密钥',
  pending: '待测试',
  off: '已停用',
};

export function connectionLamp(row: ProviderMetadata, check: ChatCheckState | undefined): ConnectionLamp {
  if (row.enabled === false) return 'off';
  if (row.credential_required && !row.configured) return 'key';
  if (row.selector_admission === 'pending') return 'pending';
  if (check?.status === 'checking') return 'checking';
  if (check?.status === 'failed') return 'bad';
  if (check?.status === 'ok') return 'ok';
  return row.selector_admission === 'manual' ? 'manual' : 'idle';
}

interface ConnectionListProps {
  providers: ProviderMetadata[];
  activeProviderId: string;
  checkFor: (row: ProviderMetadata) => ChatCheckState | undefined;
  /** A running turn keeps its provider. */
  locked: boolean;
  onSelect: (providerId: string) => void;
}

export function ConnectionList({ providers, activeProviderId, checkFor, locked, onSelect }: ConnectionListProps) {
  const rows = providers
    .filter((row) => row.enabled !== false || row.id === activeProviderId)
    .sort((a, b) => Number(b.id === activeProviderId) - Number(a.id === activeProviderId));
  return (
    <nav className="ws2-conn-list" aria-label="接入列表">
      <div className="ws2-conn-list-title">接入</div>
      <ul>
        {rows.map((row) => {
          const active = row.id === activeProviderId;
          const lamp = connectionLamp(row, checkFor(row));
          // Pending connections stay out of use until their first check passes or they are enabled by hand.
          const selectable = !active && !locked && lamp !== 'pending' && lamp !== 'off';
          return (
            <li key={row.id}>
              <button
                type="button"
                className={`ws2-conn-row${active ? ' is-active' : ''}`}
                aria-current={active ? 'true' : undefined}
                aria-label={active ? `${row.display_name}，当前接入，${LAMP_LABEL[lamp]}` : `设为当前接入：${row.display_name}，${LAMP_LABEL[lamp]}`}
                disabled={!selectable}
                title={!active && locked ? '本轮回复结束后才能换接入' : undefined}
                onClick={() => onSelect(row.id)}
              >
                <ProviderMark provider={row} />
                <span className="ws2-conn-name">{row.display_name}</span>
                <span className={`ws2-conn-lamp is-${lamp}`}>
                  <i aria-hidden="true" />
                  {LAMP_LABEL[lamp]}
                </span>
                {active ? <span className="ws2-conn-tag">当前</span> : <span className="ws2-conn-use" aria-hidden="true">设为当前</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
