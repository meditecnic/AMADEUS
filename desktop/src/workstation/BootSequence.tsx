import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { DraftWord } from './DraftWord';
import { DIVERGENCE } from './Nixie';

type Worldline = 'steins_gate' | 'beta';

/** Once per app session: AMADEUS is drafted large on construction lines, then flies into the header wordmark
 *  while the room assembles behind it. Click / any key skips; reduced motion skips entirely. */
export function BootSequence({ worldline, onDock, onDone }: { worldline: Worldline; onDock: () => void; onDone: () => void }) {
  const [phase, setPhase] = useState<'draft' | 'dock' | 'gone'>('draft');
  useEffect(() => { if (phase === 'dock') onDock(); }, [phase, onDock]);
  const wordRef = useRef<HTMLDivElement>(null);
  const [dock, setDock] = useState<React.CSSProperties>({});
  const doneRef = useRef(false);
  const finish = () => { if (doneRef.current) return; doneRef.current = true; setPhase('gone'); onDone(); };

  useLayoutEffect(() => {
    if (phase !== 'dock' || !wordRef.current) return;
    const target = document.querySelector('.ws2-wordmark')?.getBoundingClientRect();
    const from = wordRef.current.getBoundingClientRect();
    if (!target || !from.width) return;
    const s = target.width / from.width;
    setDock({ transform: `translate(${target.left - from.left}px, ${target.top - from.top}px) scale(${s})` });
  }, [phase]);

  useEffect(() => {
    const t1 = window.setTimeout(() => setPhase('dock'), 1500);
    const t2 = window.setTimeout(finish, 2250);
    const skip = () => finish();
    window.addEventListener('keydown', skip, true);
    return () => { window.clearTimeout(t1); window.clearTimeout(t2); window.removeEventListener('keydown', skip, true); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === 'gone') return null;
  return (
    <div className={`ws2-boot ${phase}`} onClick={finish} aria-hidden="true">
      <svg className="ws2-boot-grid" viewBox="0 0 100 100" preserveAspectRatio="none">
        <line x1="0" y1="50" x2="100" y2="50" />
        <line x1="50" y1="0" x2="50" y2="100" />
        <circle cx="50" cy="50" r="28" />
      </svg>
      <div className="ws2-boot-word" ref={wordRef} style={dock}>
        <DraftWord text="AMADEUS" step={110} />
      </div>
      <div className="ws2-boot-meta">
        <span>{worldline === 'beta' ? 'β' : 'STEINS;GATE'}</span>
        <i />
        <span>{DIVERGENCE[worldline]}%</span>
      </div>
    </div>
  );
}
