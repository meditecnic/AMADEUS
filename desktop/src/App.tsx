import { useState } from 'react';
import { BootSplash } from './components/BootSplash';
import { SalieriLogin } from './components/SalieriLogin';
import './App.css';

type AppStep = 'splash' | 'login' | 'workstation';

function App() {
  const [step, setStep] = useState<AppStep>('splash');

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
    <main className="app-main-container">
      {step === 'splash' && <BootSplash onDone={handleSplashDone} />}
      
      {step === 'login' && <SalieriLogin onLoginSuccess={handleLoginSuccess} />}
      
      {step === 'workstation' && (
        <div className="workstation-placeholder-panel">
          <div className="status-indicator">
            <span className="blink-dot orange-dot" />
            <span className="status-label">AMADEUS KURISU v3.0.0 (ONLINE)</span>
          </div>
          
          <div className="welcome-banner">
            <h1>ACCESS GRANTED</h1>
            <p>SYSTEM SECURITY: CLEAR</p>
            <p>Welcome back, LabMem No.004 memory module connected.</p>
          </div>

          <button className="system-logout-btn" onClick={handleLogout}>
            DISCONNECT SESSION
          </button>
        </div>
      )}
    </main>
  );
}

export default App;
