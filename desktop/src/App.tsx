import { useCallback, useEffect, useState } from 'react';
import { Workstation } from './components/Workstation';
import { useReducedMotion } from './design-system/useReducedMotion';
import { HotBoot } from './hotwire/HotBoot';
import { HotLogin } from './hotwire/HotLogin';
import { ScorchDefs } from './hotwire/ScorchDefs';
import { Wipe } from './hotwire/Wipe';
import { toneOf } from './hotwire/tone';
/* App.css is imported once from main.tsx (tokens.css → App.css) — sole cascade owner. */

type AppStep = 'boot' | 'login' | 'workstation';

/**
 * Boot → login → workstation (design/references.md §2.6). The boot is self-made and always plays (it waits
 * for the local service anyway); login hands over through a slab wipe, the workstation mounts under it.
 */
function App() {
  const [step, setStep] = useState<AppStep>('boot');
  const [worldline, setWorldline] = useState<'steins_gate' | 'beta'>(() => {
    try {
      const stored = localStorage.getItem('amadeus_pc_worldline');
      return stored === 'beta' ? 'beta' : 'steins_gate';
    } catch {
      return 'steins_gate';
    }
  });
  const reducedMotion = useReducedMotion();
  const [wipe, setWipe] = useState<{ to: AppStep; word?: string } | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem('amadeus_pc_worldline', worldline);
    } catch {
      // The selected line still remains valid for the current runtime.
    }
  }, [worldline]);

  const handleLoginSuccess = useCallback(({ easterEgg }: { easterEgg: boolean }) => {
    setWipe({ to: 'workstation', word: easterEgg ? 'Der Alte würfelt nicht.' : undefined });
  }, []);

  const handleLogout = () => {
    setWipe(null);
    setStep('login');
  };

  return (
    <main className="app-main-container">
      <ScorchDefs />
      {step === 'boot' && <HotBoot worldline={worldline} onDone={() => setWipe((w) => w ?? { to: 'login' })} />}

      {step === 'workstation' && (
        <Workstation
          worldline={worldline}
          onWorldlineChange={setWorldline}
          onLogout={handleLogout}
          entrance="none"
        />
      )}

      {step === 'login' && <HotLogin worldline={worldline} onLoginSuccess={handleLoginSuccess} />}

      {wipe && (
        <Wipe
          tone={toneOf(worldline)}
          reduced={reducedMotion}
          word={wipe.word}
          onCovered={() => setStep(wipe.to)}
          onDone={() => setWipe(null)}
        />
      )}
    </main>
  );
}

export default App;
