import { useState } from 'react';
import { BootSplash } from './components/BootSplash';
import { SalieriLogin } from './components/SalieriLogin';
import { Workstation } from './components/Workstation';
import './App.css';

type AppStep = 'splash' | 'login' | 'workstation';

function App() {
  const [step, setStep] = useState<AppStep>('splash');
  const [worldline, setWorldline] = useState<'steins_gate' | 'beta'>('steins_gate');

  const handleSplashDone = () => {
    setStep('login');
  };

  const handleLoginSuccess = () => {
    setStep('workstation');
  };

  const handleLogout = () => {
    setStep('login');
  };

  return (
    <main className={`app-main-container ${worldline === 'beta' ? 'worldline-beta' : ''}`}>
      {step === 'splash' && <BootSplash onDone={handleSplashDone} />}
      
      {step === 'login' && <SalieriLogin onLoginSuccess={handleLoginSuccess} />}
      
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
