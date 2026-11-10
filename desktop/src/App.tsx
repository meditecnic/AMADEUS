import { startTransition, useCallback, useEffect, useState } from 'react';
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
/** The workstation is drawn ahead under the login once the login has drawn itself in (its intro runs 3.4 s). */
const AHEAD_AFTER_MS = 3600;
const AHEAD_AFTER_REDUCED_MS = 400;
/** ...and not while a key went down this recently. */
const TYPING_PAUSE_MS = 500;

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
  // The workstation has drawn its first frames behind the terminal; the pen's sweep waits for this.
  const [stageReady, setStageReady] = useState(false);
  const handleStageReady = useCallback(() => setStageReady(true), []);
  // While the password is typed the workstation is mounted under the login, locked (U 11-10): its layout, paint,
  // pictures, layers and the GPU's shaders are all made then, so accepting the password only unlocks it and the pen
  // can sweep almost at once. Locked, it reads nothing, connects nothing and plays nothing (Workstation `locked`).
  // Each visit gets a fresh one (`ahead` is the key): the one a logout leaves is unmounted with what it held.
  const [ahead, setAhead] = useState(0);
  const [aheadMounted, setAheadMounted] = useState(false);
  /** This visit's workstation has been drawn under the login: the pen can sweep as soon as the password is in. */
  const [prepared, setPrepared] = useState(false);
  const handlePrepared = useCallback(() => setPrepared(true), []);
  useEffect(() => {
    if (step !== 'login' || aheadMounted) return undefined;
    let idle = 0;
    let timer = 0;
    // Drawing it holds the page for a moment (longer on the first run after an install, while the GPU builds its
    // shaders): never under the user's fingers, so it waits for a pause in the typing.
    let lastKey = 0;
    const onKey = () => { lastKey = performance.now(); };
    window.addEventListener('keydown', onKey, true);
    const attempt = () => {
      const typing = performance.now() - lastKey < TYPING_PAUSE_MS;
      if (typing) { timer = window.setTimeout(attempt, TYPING_PAUSE_MS); return; }
      // A transition: React builds it in slices between the login's frames; a login that comes first wins.
      const mount = () => startTransition(() => setAheadMounted(true));
      if (typeof window.requestIdleCallback === 'function') idle = window.requestIdleCallback(mount, { timeout: 1500 });
      else mount();
    };
    timer = window.setTimeout(attempt, reducedMotion ? AHEAD_AFTER_REDUCED_MS : AHEAD_AFTER_MS);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      window.clearTimeout(timer);
      if (idle) window.cancelIdleCallback(idle);
    };
  }, [step, aheadMounted, reducedMotion]);

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
    setStageReady(false);
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
    // The last visit's "drawn" must not reach the next login: it would sweep before this visit's workstation exists,
    // and the workstation would then play its entrance a second time over itself (U 11-10).
    setStageReady(false);
    // This visit's workstation goes with what it held; the next one is drawn ahead under the login again.
    setAheadMounted(false);
    setPrepared(false);
    setAhead((n) => n + 1);
    setStep('login');
  };

  return (
    <main className="app-main-container">
      {step === 'splash' && <BootSplash onDone={handleSplashDone} />}

      {step === 'recovery' && fault && <DataRecovery status={fault} worldline={worldline} />}

      {(step === 'workstation' || (step === 'login' && aheadMounted)) && (
        <Workstation
          key={ahead}
          worldline={worldline}
          onWorldlineChange={setWorldline}
          onLogout={handleLogout}
          entrance={step === 'workstation' ? entrance : 'locked'}
          onEntranceSettled={handleEntranceSettled}
          onStageReady={handleStageReady}
          onPrepared={handlePrepared}
          easterEgg={easterEgg}
        />
      )}

      {(step === 'login' || loginOverlay) && (
        <SalieriLogin
          worldline={worldline}
          onLoginSuccess={handleLoginSuccess}
          onEntranceCue={handleEntranceCue}
          stageReady={stageReady}
          stagePrepared={prepared && aheadMounted}
        />
      )}
    </main>
  );
}

export default App;
