import { useEffect, useState } from 'react';
import { dataStatus } from './dataApi';

const SEEN = 'amadeus_restore_failure_seen';

/** A restore that failed at startup (and was rolled back) is said once, between the log and the composer, until read. */
export function RestoreNotice({ onOpen }: { onOpen: () => void }) {
  const [detail, setDetail] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    dataStatus().then((status) => {
      if (!live || status.status !== 'restore_failed') return;
      const key = status.detail ?? '';
      try { if (localStorage.getItem(SEEN) === key) return; } catch { /* show it */ }
      setDetail(key);
    }, () => undefined);
    return () => { live = false; };
  }, []);
  if (detail === null) return null;
  const dismiss = () => {
    try { localStorage.setItem(SEEN, detail); } catch { /* it shows again next time */ }
    setDetail(null);
  };
  return (
    <div className="ws2-link-state is-lost" role="status">
      <span>上次的恢复没有做完，数据还是恢复前的样子。</span>
      <span className="ws2-notice-ways">
        <button type="button" onClick={onOpen}>查看备份</button>
        <button type="button" onClick={dismiss}>知道了</button>
      </span>
    </div>
  );
}
