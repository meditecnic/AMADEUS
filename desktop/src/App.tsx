import { useCallback, useEffect, useState } from 'react';
import { BootSplash, hasBootSplash } from './components/BootSplash';
import { SalieriLogin, type EntranceCue } from './components/SalieriLogin';
import { Workstation, type EntrancePhase } from './components/Workstation';
import { loadAssetPack } from './assetPack';
import { DataRecovery } from './workstation/DataRecovery';
import { dataStatus, type DataStatus } from './workstation/dataApi';
import { useReducedMotion } from './design-system/useReducedMotion';
/* App.css is imported once from main.tsx (tokens.css → App.css) — sole cascade owner. */

type AppStep = 'probe' | 'splash' | 'login' | 'workstation' | 'recovery';
/** Upper bound for the whole entrance; only reached if animation events never arrive. */
const ENTRANCE_FALLBACK_MS = 12000;

function App() {
  const [step, setStep] = useState<AppStep>('probe');
  const [fault, setFault] = useState<DataStatus | null>(null);
  useEffect(() => {
    let cancelled = false;
    // The original entrance animation is part of the pack; without it the app opens on login. A database that failed
    // its startup check (the backend then answers only its data routes) opens on the recovery sheet instead.
    void Promise.all([loadAssetPack(), dataStatus().catch(() => null)]).then(([on, data]) => {
      if (cancelled) return;
      if (data?.status === 'corrupt') {
        setFault(data);
        setStep('recovery');
        return;
      }
      setStep(on && hasBootSplash() ? 'splash' : 'login');
    });
    return () => { cancelled = true; };
  }, []);
  const [worldline, setWorldline] = useState<'steins_gate' | 'beta'>(() => {
    try {
      const stored = localStorage.getItem('amadeus_pc_worldline');
      return stored === 'beta' ? 'beta' : 'steins_gate';
    } catch {
      return 'steins_gate';
    }
  });
  const reducedMotion = useReducedMotion();
  // The login terminal stays mounted over the workstation while the entrance plays, then lets go.
  const [loginOverlay, setLoginOverlay] = useState(false);
  const [entrance, setEntrance] = useState<EntrancePhase>('none');
  const [easterEgg, setEasterEgg] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem('amadeus_pc_worldline', worldline);
    } catch {
      // The selected line still remains valid for the current runtime.
    }
  }, [worldline]);

  const handleSplashDone = () => {
    setStep('login');
  };

  const handleLoginSuccess = (result?: { easterEgg: boolean }) => {
    setEasterEgg(Boolean(result?.easterEgg));
    // The workstation mounts at once, held still behind the terminal; it plays exactly once per login.
    setLoginOverlay(true);
    setEntrance(reducedMotion ? 'none' : 'hold');
    setStep('workstation');
  };

  const handleEntranceCue = useCallback((cue: EntranceCue) => {
    if (cue === 'draft') setEntrance((e) => (e === 'hold' ? 'draft' : e));
    else if (cue === 'docked') setEntrance((e) => (e === 'hold' || e === 'draft' ? 'docked' : e));
    else if (cue === 'gone') setLoginOverlay(false);
    else {
      setLoginOverlay(false);
      setEntrance((e) => (e === 'none' ? e : 'done'));
    }
  }, []);

  const handleEntranceSettled = useCallback(() => {
    setEntrance((e) => (e === 'none' ? e : 'done'));
  }, []);

  const entering = entrance === 'hold' || entrance === 'draft' || entrance === 'docked';
  useEffect(() => {
    if (!entering) return;
    const id = window.setTimeout(() => {
      setLoginOverlay(false);
      setEntrance('done');
    }, ENTRANCE_FALLBACK_MS);
    return () => window.clearTimeout(id);
  }, [entering]);

  const handleLogout = () => {
    setLoginOverlay(false);
    setEntrance('none');
    setStep('login');
  };

  return (
    <main className="app-main-container">
      {step === 'splash' && <BootSplash onDone={handleSplashDone} />}

      {step === 'recovery' && fault && <DataRecovery status={fault} worldline={worldline} />}

      {step === 'workstation' && (
        <Workstation
          worldline={worldline}
          onWorldlineChange={setWorldline}
          onLogout={handleLogout}
          entrance={entrance}
          onEntranceSettled={handleEntranceSettled}
          easterEgg={easterEgg}
        />
      )}

      {(step === 'login' || loginOverlay) && (
        <SalieriLogin
          worldline={worldline}
          onLoginSuccess={handleLoginSuccess}
          onEntranceCue={handleEntranceCue}
        />
      )}
    </main>
  );
}

export default App;
