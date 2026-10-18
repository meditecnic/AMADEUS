import { useEffect, useState } from 'react';
import { SalieriLogin } from './components/SalieriLogin';
import { Workstation } from './components/Workstation';
import { EntrySequence } from './components/EntrySequence';
import { loadAssetPack } from './assetPack';
/* App.css is imported once from main.tsx (tokens.css → App.css) — sole cascade owner. */

type AppStep = 'probe' | 'login' | 'entry' | 'workstation';

function App() {
  const [step, setStep] = useState<AppStep>('probe');
  useEffect(() => {
    let cancelled = false;
    const ready = () => { if (!cancelled) setStep('login'); };
    void loadAssetPack().then(ready, ready);
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

  useEffect(() => {
    try {
      localStorage.setItem('amadeus_pc_worldline', worldline);
    } catch {
      // The selected line still remains valid for the current runtime.
    }
  }, [worldline]);

  const handleLoginSuccess = () => {
    setStep('entry');
  };

  const handleEntryDone = () => {
    setStep('workstation');
  };

  const handleLogout = () => {
    setStep('login');
  };

  return (
    <main className="app-main-container" data-step={step}>
      {step === 'probe' && <p role="status">正在检查本地资源…</p>}
      {step === 'login' && <SalieriLogin onLoginSuccess={handleLoginSuccess} />}
      {step === 'entry' && <EntrySequence onComplete={handleEntryDone} />}
      
      {step === 'workstation' && (
        <Workstation
          worldline={worldline}
          onWorldlineChange={setWorldline}
          onLogout={handleLogout}
        />
      )}
    </main>
  );
}

export default App;
