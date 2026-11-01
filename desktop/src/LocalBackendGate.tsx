import { useEffect, useState, type ReactNode } from 'react';
import { initializeLocalBackend } from './apiBase';

export function LocalBackendGate({ children }: { children: ReactNode }) {
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<'pending' | 'ready' | 'error'>('pending');
  useEffect(() => {
    let cancelled = false;
    void initializeLocalBackend().then(
      () => { if (!cancelled) setStatus('ready'); },
      () => { if (!cancelled) setStatus('error'); },
    );
    return () => { cancelled = true; };
  }, [attempt]);
  if (status === 'ready') return children;
  if (status === 'error') return (
    <p role="alert">无法连接本地服务。<button onClick={() => {
      setStatus('pending');
      setAttempt(value => value + 1);
    }}>重试</button></p>
  );
  return <p role="status">正在连接本地服务…</p>;
}
