import React, { useEffect, useRef, useState } from 'react';
import { apiUrl, isPackagedTauri } from '../apiBase';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { LoginRain } from './LoginRain';
import './SalieriLogin.css';

interface SalieriLoginProps {
  onLoginSuccess: () => void;
}

const USER_ID = 'Salieri';

export const SalieriLogin: React.FC<SalieriLoginProps> = ({ onLoginSuccess }) => {
  const reducedMotion = useReducedMotion();
  const [userId, setUserId] = useState(USER_ID);
  const [password, setPassword] = useState('');
  const [typing, setTyping] = useState(!reducedMotion);
  const [error, setError] = useState<{ message: string; field: 'user' | 'password' } | null>(null);
  const [glitching, setGlitching] = useState(false);
  const [settling, setSettling] = useState(false);
  const [buttonEngaged, setButtonEngaged] = useState(false);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [backendStatus, setBackendStatus] = useState<'starting' | 'ready' | 'timeout'>(isPackagedTauri ? 'starting' : 'ready');
  const userRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const pressedRef = useRef(false);
  const composingRef = useRef(false);
  const isTyping = typing && !reducedMotion;

  useEffect(() => {
    if (reducedMotion) { setTyping(false); setGlitching(false); setSettling(false); }
  }, [reducedMotion]);

  useEffect(() => () => requestRef.current?.abort(), []);

  useEffect(() => {
    if (!isPackagedTauri) return;
    let stopped = false;
    let retryTimer: number | undefined;
    const controller = new AbortController();
    const deadline = window.setTimeout(() => {
      stopped = true;
      controller.abort();
      setBackendStatus('timeout');
    }, 30000);

    const checkHealth = async () => {
      try {
        const response = await fetch(apiUrl('/health'), { signal: controller.signal });
        if (stopped) return;
        if (response.ok) {
          stopped = true;
          window.clearTimeout(deadline);
          setBackendStatus('ready');
          return;
        }
      } catch {
        // The packaged local backend may still be starting.
      }
      if (!stopped) retryTimer = window.setTimeout(() => { void checkHealth(); }, 1000);
    };

    void checkHealth();
    return () => {
      stopped = true;
      controller.abort();
      window.clearTimeout(deadline);
      window.clearTimeout(retryTimer);
    };
  }, []);

  const clearError = () => { setError(null); setGlitching(false); };
  const showAuthError = (message: string, field: 'user' | 'password', unstable = false) => {
    setTyping(false);
    setError({ message, field });
    setGlitching(unstable && !reducedMotion);
    (field === 'user' ? userRef : passwordRef).current?.focus();
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (backendStatus !== 'ready' || requestRef.current || composingRef.current) return;
    if (!userId) { showAuthError('请输入 USER ID', 'user'); return; }
    if (userId !== USER_ID) { showAuthError('无效的 USER ID', 'user'); return; }
    if (!password) { showAuthError('请输入访问密码', 'password'); return; }

    const controller = new AbortController();
    requestRef.current = controller;
    setIsAuthenticating(true);
    clearError();
    try {
      const response = await fetch(apiUrl('/api/auth/salieri'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      if (response.ok) {
        onLoginSuccess();
      } else {
        const data = await response.json().catch(() => ({}));
        if (!controller.signal.aborted) {
          showAuthError(typeof data.detail === 'string' ? data.detail : '访问被拒绝', 'password', true);
        }
      }
    } catch {
      if (!controller.signal.aborted) showAuthError('系统连接已断开', 'password', true);
    } finally {
      if (!controller.signal.aborted) {
        requestRef.current = null;
        setIsAuthenticating(false);
      }
    }
  };

  const releaseButton = () => {
    if (pressedRef.current && !reducedMotion) setSettling(true);
    pressedRef.current = false;
  };

  return (
    <section className="login-screen-container" data-theme="terminal" aria-label="Amadeus 系统接入">
      <div className="login-ambience" aria-hidden="true">
        <LoginRain reducedMotion={reducedMotion} paused={isTyping || glitching || isAuthenticating || buttonEngaged} />
        <div className="login-crt-surface" />
        <div className="login-grid-surface" />
        <div className="login-screen-edge" />
      </div>

      <div className="login-content-wrapper">
        <div className="login-form-center">
          <div className="amadeus-logo-container">
            <h1 className="amadeus-logo-text">AMADEUS</h1>
            <span className="login-logo-caption" lang="ja">人工知能システム</span>
          </div>

          <form
            onSubmit={handleSubmit}
            className="salieri-login-form"
            aria-busy={isAuthenticating}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            onKeyDown={event => {
              if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || composingRef.current)) {
                event.preventDefault();
              }
            }}
          >
            <div className="login-fields">
              <div className={`input-field-group${isTyping ? ' is-typing' : ''}`}>
                <label className="field-title" htmlFor="salieri-user">USER ID</label>
                <div className="login-input-shell">
                  <input
                    ref={userRef}
                    id="salieri-user"
                    name="username"
                    type="text"
                    value={userId}
                    onFocus={() => setTyping(false)}
                    onChange={event => { setTyping(false); setUserId(event.target.value); clearError(); }}
                    readOnly={isAuthenticating}
                    spellCheck={false}
                    autoComplete="username"
                    aria-invalid={error?.field === 'user'}
                    aria-describedby={error?.field === 'user' ? 'login-error' : undefined}
                  />
                  {isTyping && (
                    <span className="login-typewriter" aria-hidden="true">
                      {[...USER_ID].map((letter, index) => (
                        <span
                          key={index}
                          style={{ '--letter': index } as React.CSSProperties}
                          onAnimationEnd={index === USER_ID.length - 1 ? () => setTyping(false) : undefined}
                        >{letter}</span>
                      ))}
                    </span>
                  )}
                </div>
              </div>

              <div className="input-field-group">
                <label className="field-title" htmlFor="salieri-pwd">PASSWORD</label>
                <div className="login-input-shell">
                  <input
                    ref={passwordRef}
                    id="salieri-pwd"
                    name="password"
                    type="password"
                    value={password}
                    onChange={event => { setPassword(event.target.value); clearError(); }}
                    readOnly={isAuthenticating}
                    spellCheck={false}
                    autoComplete="current-password"
                    aria-invalid={error?.field === 'password'}
                    aria-describedby={error?.field === 'password' ? 'login-error' : undefined}
                  />
                </div>
              </div>
            </div>

            <div className="btn-container">
              <button
                type="submit"
                className={`decrypt-btn${settling ? ' is-settling' : ''}`}
                disabled={isAuthenticating || backendStatus !== 'ready'}
                title="CONNECT"
                aria-label="Decrypt and Connect"
                onPointerEnter={() => setButtonEngaged(true)}
                onPointerLeave={() => setButtonEngaged(false)}
                onFocus={() => setButtonEngaged(true)}
                onPointerDown={event => {
                  if (event.button !== 0) return;
                  pressedRef.current = true;
                  setSettling(false);
                  event.currentTarget.setPointerCapture?.(event.pointerId);
                }}
                onPointerUp={releaseButton}
                onPointerCancel={releaseButton}
                onLostPointerCapture={releaseButton}
                onKeyDown={event => {
                  if (event.key === ' ' || event.key === 'Enter') { pressedRef.current = true; setSettling(false); }
                }}
                onKeyUp={releaseButton}
                onBlur={() => { releaseButton(); setButtonEngaged(false); }}
              >
                <svg viewBox="0 0 80 80" className="connect-btn-svg" aria-hidden="true" onAnimationEnd={() => setSettling(false)}>
                  <circle cx="40" cy="40" r="33" className="btn-circle-track" />
                  <circle cx="40" cy="40" r="33" className="btn-circle-closed" />
                  <path d="M27 40H52M42 30L52 40L42 50" className="btn-arrow-icon" />
                </svg>
              </button>
            </div>

            <div className="login-feedback">
              {backendStatus !== 'ready' && (
                <div className="backend-status-banner" role="status">
                  {backendStatus === 'starting' ? '正在启动本地服务…' : '本地服务没有启动，请重新打开 Amadeus'}
                </div>
              )}
              {error && (
                <div
                  id="login-error"
                  className={`auth-error-banner${glitching ? ' is-glitching' : ''}`}
                  role="alert"
                  onAnimationEnd={() => setGlitching(false)}
                >{error.message}</div>
              )}
            </div>
          </form>
        </div>
      </div>

      <footer className="login-footer">
        <span>AMADEUS SYSTEM</span>
        <span>SYSTEM ACCESS</span>
      </footer>
    </section>
  );
};
