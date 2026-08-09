import { useEffect, useState } from 'react';
import { BootSplash } from './components/BootSplash';
import { SalieriLogin } from './components/SalieriLogin';
import { Workstation } from './components/Workstation';
import LogoTransition from './components/LogoTransition';
/* App.css is imported once from main.tsx (tokens.css → App.css) — sole cascade owner. */

type AppStep = 'splash' | 'login' | 'transition' | 'workstation';

function App() {
  const [step, setStep] = useState<AppStep>('splash');
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

  const handleSplashDone = () => {
    setStep('login');
  };

  const handleLoginSuccess = () => {
    setStep('transition');
  };

  const handleTransitionDone = () => {
    setStep('workstation');
  };

  const handleLogout = () => {
    setStep('login');
  };

  return (
    <main className="app-main-container">
      {step === 'splash' && <BootSplash onDone={handleSplashDone} />}
      
      {step === 'login' && <SalieriLogin onLoginSuccess={handleLoginSuccess} />}
      
      {step === 'transition' && <LogoTransition onComplete={handleTransitionDone} />}
      
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
