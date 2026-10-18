import React, { useEffect, useRef, useState } from 'react';
import { apiUrl, isPackagedTauri } from '../apiBase';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { LoginRain } from './LoginRain';
import './SalieriLogin.css';

interface SalieriLoginProps {
  onLoginSuccess: () => void;
}

const USER_ID = 'Salieri';

// Original instrument dial: an iris aperture inside a ticked copper ring.
const DIAL_TICKS = Array.from({ length: 48 }, (_, index) => index * 7.5);
const DIAL_BLADES = Array.from({ length: 6 }, (_, index) => index * 60);

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

  const signalActive = isAuthenticating || buttonEngaged || glitching;

  return (
    <section className="login-screen-container" data-theme="terminal" aria-label="Amadeus 系统接入">
      <div className="login-ambience" aria-hidden="true">
        <div className="login-grid" />
        <LoginRain reducedMotion={reducedMotion} paused={isTyping || glitching || isAuthenticating || buttonEngaged} />
        <div className="login-azimuth" />
        <div className="login-scanlines" />
        <div className="login-vignette" />
      </div>
      <div className="login-bezel" aria-hidden="true"><i /><i /><i /><i /></div>

      <div className="login-console">
        <svg className={`login-dial${signalActive ? ' is-live' : ''}`} viewBox="0 0 120 120" aria-hidden="true">
          <circle className="dial-plate" cx="60" cy="60" r="52" />
          <circle className="dial-ring" cx="60" cy="60" r="45" />
          <g className="dial-ticks">
            {DIAL_TICKS.map((angle, index) => (
              <line
                key={angle}
                x1="60" y1={index % 4 === 0 ? 8 : 11} x2="60" y2="16"
                transform={`rotate(${angle} 60 60)`}
                className={index % 4 === 0 ? 'is-major' : undefined}
              />
            ))}
          </g>
          <g className="dial-blades">
            {DIAL_BLADES.map(angle => (
              <path key={angle} d="M60 60 L60 26 A34 34 0 0 1 89 43 Z" transform={`rotate(${angle + 15} 60 60)`} />
            ))}
          </g>
          <g className="dial-cross">
            <path d="M20 60H40M80 60H100M60 20V34M60 86V100" />
            <rect x="57" y="57" width="6" height="6" transform="rotate(45 60 60)" />
          </g>
        </svg>

        <span className="login-axis" aria-hidden="true" />
        <div className="login-wordmark">
          <h1 className="login-wordmark-text">AMADEUS</h1>
          <span className="login-wordmark-ja" lang="ja">人工知能システム</span>
        </div>
        <span className="login-axis login-axis-lower" aria-hidden="true" />

        <form
          onSubmit={handleSubmit}
          className="login-terminal-form"
          aria-busy={isAuthenticating}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; }}
          onKeyDown={event => {
            if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || composingRef.current)) {
              event.preventDefault();
            }
          }}
        >
          <div className="login-plate">
            <div className="login-plate-head" aria-hidden="true">
              <span className="login-plate-ticks"><i /><i /><i /><i /></span>
              <span className="login-plate-title">ACCESS TERMINAL</span>
              <span className="login-plate-mode">{signalActive ? 'LINK' : 'IDLE'}</span>
            </div>

            <label className="field-title" htmlFor="salieri-user">USER ID</label>
            <div className={`login-input-shell${isTyping ? ' is-typing' : ''}`}>
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
            <div className="login-knob-slot">
              <button
                type="submit"
                className={`decrypt-btn${settling ? ' is-settling' : ''}${isAuthenticating ? ' is-linking' : ''}`}
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
                  <g className="btn-knurl">
                    {DIAL_TICKS.map((angle, index) => (
                      <line key={angle} x1="40" y1="9" x2="40" y2="14" transform={`rotate(${angle} 40 40)`} className={index % 4 === 0 ? 'is-major' : undefined} />
                    ))}
                  </g>
                  <circle cx="40" cy="40" r="25" className="btn-circle-track" />
                  <circle cx="40" cy="40" r="25" className="btn-circle-closed" />
                  <path d="M30 40H50M43 32.5L51 40L43 47.5" className="btn-arrow-icon" />
                </svg>
              </button>
            </div>
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

      <footer className="login-footer">
        <span>AMADEUS SYSTEM</span>
        <span>SYSTEM ACCESS</span>
      </footer>
    </section>
  );
};
