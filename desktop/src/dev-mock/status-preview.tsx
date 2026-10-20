import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { PresenceIndicator, PRESENCE_LABELS, ThinkingMark, type PresenceState } from '../workstation/PresenceIndicator';
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
    {/* Same stage classes and presenter as Workstation, at a fixed preview size. */}
    <section className={`ws2-stage presence-${state}`} style={{ marginTop: 32, width: 520, height: 340, ['--ws-head' as string]: '0px' }}>
      <div className="ws2-matrix" aria-hidden="true" />
      <div className="ws2-plate">
        <PresenceIndicator key={worldline} state={state} />
      </div>
    </section>
    {state === 'thinking' && <div className="ws2-thinking" style={{ marginTop: 16 }}><ThinkingMark /><span className="ws2-thinking-label">THINKING</span></div>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><StatusPreview /></React.StrictMode>);
