import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { PresenceIndicator, PRESENCE_LABELS, type PresenceState } from '../workstation/PresenceIndicator';
import '../design-system/motion.css';
import '../design-system/tokens.css';
import '../workstation/workstation.css';

/** Isolated, silent fixture. No Workstation, socket, audio or production state overrides. */
function StatusPreview() {
  const [state, setState] = useState<PresenceState>('speaking');
  const [worldline, setWorldline] = useState('sg');
  return <main className="ws2" data-wl={worldline} style={{ display: 'block', padding: 32 }}>
    <p>无声状态预览，仅展示共享状态组件，不代表音频播放。</p>
    <div role="group" aria-label="状态预览">
      {(Object.keys(PRESENCE_LABELS) as PresenceState[]).map(value =>
        <button type="button" key={value} onClick={() => setState(value)} aria-pressed={state === value}>{PRESENCE_LABELS[value]}</button>)}
      <button type="button" onClick={() => setWorldline(line => line === 'sg' ? 'beta' : 'sg')}>SG / β</button>
    </div>
    <section className={`presence-${state}`} style={{ marginTop: 32 }}>
      <PresenceIndicator key={worldline} state={state} />
    </section>
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><StatusPreview /></React.StrictMode>);
