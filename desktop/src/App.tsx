import { useCallback, useEffect, useState } from 'react';
import { BootSplash } from './components/BootSplash';
import { SalieriLogin, type EntranceCue } from './components/SalieriLogin';
import { Workstation, type EntrancePhase } from './components/Workstation';
import { loadAssetPack } from './assetPack';
import { useReducedMotion } from './design-system/useReducedMotion';
/* App.css is imported once from main.tsx (tokens.css → App.css) — sole cascade owner. */

type AppStep = 'probe' | 'splash' | 'login' | 'workstation';
/** Upper bound for the whole entrance; only reached if animation events never arrive. */
const ENTRANCE_FALLBACK_MS = 12000;

function App() {
  const [step, setStep] = useState<AppStep>('probe');
  useEffect(() => {
    let cancelled = false;
    // The original entrance animation is part of the pack; without it the app opens on login.
    void loadAssetPack().then((on) => { if (!cancelled) setStep(on ? 'splash' : 'login'); });
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

  const handleLoginSuccess = () => {
    // The workstation mounts at once, held still behind the terminal; it plays exactly once per login.
    setLoginOverlay(true);
    setEntrance(reducedMotion ? 'none' : 'hold');
    setStep('workstation');
  };

  const handleEntranceCue = useCallback((cue: EntranceCue) => {
    if (cue === 'light') setEntrance((e) => (e === 'hold' ? 'light' : e));
    else if (cue === 'run') setEntrance((e) => (e === 'hold' || e === 'light' ? 'run' : e));
    else if (cue === 'gone') setLoginOverlay(false);
    else {
      setLoginOverlay(false);
      setEntrance((e) => (e === 'none' ? e : 'done'));
    }
  }, []);

  const handleEntranceSettled = useCallback(() => {
    setEntrance((e) => (e === 'none' ? e : 'done'));
  }, []);

  const entering = entrance === 'hold' || entrance === 'light' || entrance === 'run';
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

      {step === 'workstation' && (
        <Workstation
          worldline={worldline}
          onWorldlineChange={setWorldline}
          onLogout={handleLogout}
          entrance={entrance}
          onEntranceSettled={handleEntranceSettled}
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
