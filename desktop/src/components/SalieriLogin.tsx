import { apiUrl, isPackagedTauri } from '../apiBase';
import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
import './SalieriLogin.css';
import { audioService } from '../services/AudioService';
import { audioPlayer } from '../hooks/useAudioPlayer';
import { useReducedMotion } from '../design-system/useReducedMotion';
import { LightIris, type IrisAttention, type IrisHandle } from './login/LightIris';
import { Astrolabe } from './login/Astrolabe';

type Worldline = 'steins_gate' | 'beta';
/** Beats of the entrance the app listens for: the iris opens on the room, the room wakes, the terminal is gone. */
export type EntranceCue = 'light' | 'run' | 'gone' | 'skip';

interface SalieriLoginProps {
  onLoginSuccess: () => void;
  worldline?: Worldline;
  onEntranceCue?: (cue: EntranceCue) => void;
}

/** Stage geometry (1920 × 1080 design space). */
const STAGE_W = 1920;
const STAGE_H = 1080;
const LENS_X = 960;
const LENS_Y = 410;

/** Slashed binary columns, seeded so they never reshuffle between renders. */
function binaryColumn(seed: number, rows: number, min: number, spread: number) {
  let s = seed;
  const bit = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) >> 16) & 1;
  return Array.from({ length: rows }, (_, i) => '/' + Array.from({ length: min + ((i * 7 + seed) % spread) }, bit).join(''));
}
const BIN_LEFT = binaryColumn(0x51a1, 18, 12, 7);
const BIN_RIGHT = binaryColumn(0x2f1d, 18, 18, 11);

const DAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const pad = (n: number) => String(n).padStart(2, '0');
function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())} ${DAYS[now.getDay()]}  ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

export const SalieriLogin: React.FC<SalieriLoginProps> = ({ onLoginSuccess, worldline = 'steins_gate', onEntranceCue }) => {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  // Which field the error belongs to; the message text is localized or a raw backend detail.
  const [errorField, setErrorField] = useState<'user' | 'password' | null>(null);
  const [errorSeq, setErrorSeq] = useState(0);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [backendStatus, setBackendStatus] = useState<'starting' | 'ready' | 'timeout'>(isPackagedTauri ? 'starting' : 'ready');
  const [focusField, setFocusField] = useState<'user' | 'password' | null>(null);
  const [capsLock, setCapsLock] = useState(false);
  const [exiting, setExiting] = useState(false);
  const reduced = useReducedMotion();
  const clock = useClock();

  const rootRef = useRef<HTMLDivElement>(null);
  const lensRef = useRef<IrisHandle>(null);
  const userRowRef = useRef<HTMLDivElement>(null);
  const pwdRowRef = useRef<HTMLDivElement>(null);
  const pwdRef = useRef<HTMLInputElement>(null);
  const errTimeoutRef = useRef<number | null>(null);
  const succeededRef = useRef(false);
  const cueRef = useRef(onEntranceCue);
  cueRef.current = onEntranceCue;
  const goneRef = useRef(false);
  const cue = (c: EntranceCue) => {
    if (goneRef.current) return;
    if (c === 'gone' || c === 'skip') goneRef.current = true;
    cueRef.current?.(c);
  };

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
        // 本地服务启动期间连接可能尚未建立，按间隔重试。
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

  // 统一错误设置与 3s 自动清除并复原函数
  const showAuthError = (msg: string, field: 'user' | 'password') => {
    if (errTimeoutRef.current) {
      window.clearTimeout(errTimeoutRef.current);
    }
    setErrorMsg(msg);
    setErrorField(field);
    setErrorSeq((n) => n + 1);
    lensRef.current?.flinch();
    const row = field === 'user' ? userRowRef.current : pwdRowRef.current;
    if (!reduced && typeof row?.animate === 'function') {
      // A damped refusal: the slot is pushed back and settles, it does not wobble forever.
      row.animate(
        [{ transform: 'none' }, { transform: 'translateX(-9px)' }, { transform: 'translateX(7px)' }, { transform: 'translateX(-4px)' }, { transform: 'translateX(2px)' }, { transform: 'none' }],
        { duration: 420, easing: 'cubic-bezier(.36, .07, .19, .97)' },
      );
    }
    errTimeoutRef.current = window.setTimeout(() => {
      setErrorMsg('');
      setErrorField(null);
      errTimeoutRef.current = null;
    }, 3000);
  };

  // 卸载组件时清理定时器，防内存泄漏
  useEffect(() => {
    return () => {
      if (errTimeoutRef.current) {
        window.clearTimeout(errTimeoutRef.current);
      }
    };
  }, []);

  // Login-screen BGM. After a successful login the workstation owns the channel, so leaving must not silence it.
  useEffect(() => {
    audioService.playBGM('/audio/bgm/Messenger main theme.ogg');
    return () => {
      if (!succeededRef.current) audioService.stopBGM();
    };
  }, []);

  // 1920 × 1080 stage, scaled to fit; the lens centre is exported in viewport pixels for the entrance.
  useLayoutEffect(() => {
    const handleResize = () => {
      const el = rootRef.current;
      if (!el) return;
      const w = window.innerWidth;
      const h = window.innerHeight;
      const s = Math.min(w / STAGE_W, h / STAGE_H);
      const ox = (w - STAGE_W * s) / 2;
      const oy = (h - STAGE_H * s) / 2;
      el.style.setProperty('--s', String(s));
      el.style.setProperty('--ox', `${ox}px`);
      el.style.setProperty('--oy', `${oy}px`);
      el.style.setProperty('--cx', `${ox + LENS_X * s}px`);
      el.style.setProperty('--cy', `${oy + LENS_Y * s}px`);
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // USER ID 打字机自动录入（等输入栏升起后开始）；每个字符都让瞳孔轻轻一动。写完后把光标交给密码栏。
  useEffect(() => {
    let index = 0;
    const targetText = 'Salieri';
    let current = '';
    let typeTimer: number | undefined;
    setUserId('');

    const startTimer = window.setTimeout(() => { typeTimer = window.setInterval(() => {
      if (index < targetText.length) {
        current += targetText.charAt(index);
        setUserId(current);
        lensRef.current?.twitch();
        index++;
      } else {
        window.clearInterval(typeTimer);
        const active = document.activeElement;
        if (!active || active === document.body) pwdRef.current?.focus();
      }
    }, 50); }, reduced ? 0 : 1050);

    return () => { window.clearTimeout(startTimer); window.clearInterval(typeTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Entrance: any deliberate key or click skips straight into the workstation.
  useEffect(() => {
    if (!exiting || reduced) return;
    const skip = (e: KeyboardEvent) => {
      if (e.repeat || e.isComposing) return;
      cue('skip');
    };
    window.addEventListener('keydown', skip, true);
    return () => window.removeEventListener('keydown', skip, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exiting, reduced]);

  // Reduced motion: a short opacity fade only, with a fallback in case transitionend never arrives.
  useEffect(() => {
    if (!exiting || !reduced) return;
    const id = window.setTimeout(() => cue('gone'), 400);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exiting, reduced]);

  const handleInitAudio = () => {
    audioPlayer.init();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (backendStatus !== 'ready' || exiting) return;
    handleInitAudio();
    if (!userId) {
      showAuthError('请输入 USER ID', 'user');
      return;
    }
    if (userId !== 'Salieri') {
      showAuthError('无效的 USER ID', 'user');
      return;
    }
    if (!password) {
      showAuthError('请输入访问密码', 'password');
      return;
    }

    setIsAuthenticating(true);
    if (errTimeoutRef.current) {
      window.clearTimeout(errTimeoutRef.current);
      errTimeoutRef.current = null;
    }
    setErrorMsg('');
    setErrorField(null);

    let accepted = false;
    try {
      const response = await fetch(apiUrl('/api/auth/salieri'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ password }),
      });

      if (response.ok) {
        accepted = true;
        audioService.playSFX('/audio/sfx/连接成功，进入页面2.ogg');
        audioService.stopBGM();
        succeededRef.current = true;
        (document.activeElement as HTMLElement | null)?.blur?.();
        const el = rootRef.current;
        if (el) {
          const root = document.documentElement.style;
          root.setProperty('--amadeus-entry-x', el.style.getPropertyValue('--cx') || '50%');
          root.setProperty('--amadeus-entry-y', el.style.getPropertyValue('--cy') || '37%');
        }
        setExiting(true);
        onLoginSuccess();
      } else {
        const errorData = await response.json().catch(() => ({}));
        showAuthError(errorData.detail || '访问被拒绝', 'password');
      }
    } catch (error) {
      showAuthError('系统连接已断开', 'password');
    } finally {
      if (!accepted) setIsAuthenticating(false);
    }
  };

  const attention: IrisAttention = isAuthenticating ? 'busy' : focusField === 'password' ? 'password' : focusField === 'user' ? 'user' : 'idle';
  const line = worldline === 'beta' ? 'beta' : 'sg';
  const busy = isAuthenticating && !exiting;

  return (
    <div
      ref={rootRef}
      className={`sl-root${exiting ? ' exiting' : ''}${reduced ? ' reduced' : ''}${focusField ? ` focus-${focusField}` : ''}`}
      data-line={line}
      onPointerDown={() => { if (exiting && !reduced) cue('skip'); }}
      onAnimationStart={(e) => { if (e.animationName === 'sl-dilate') cue('light'); }}
      onAnimationEnd={(e) => {
        if (e.animationName === 'sl-cue-run') cue('run');
        else if (e.animationName === 'sl-dolly') cue('gone');
      }}
      onTransitionEnd={(e) => {
        if (exiting && reduced && e.target === e.currentTarget && e.propertyName === 'opacity') cue('gone');
      }}
    >
      <div className="sl-rig">
        <div className="sl-masked">
          <div className="sl-room" aria-hidden="true" />
          <div className="sl-stage sl-stage-back" aria-hidden="true">
            <div className="sl-grid" />
            <svg className="sl-axes" viewBox={`0 0 ${STAGE_W} ${STAGE_H}`}>
              <path d={`M520 ${LENS_Y}H1400M${LENS_X} 112V1010`} className="axis" />
              {Array.from({ length: 21 }, (_, i) => (
                <path key={i} d={`M${560 + i * 40} ${LENS_Y - (i % 5 === 0 ? 7 : 3)}v${i % 5 === 0 ? 14 : 6}`} className="tick" />
              ))}
              <text x="520" y={LENS_Y - 14} className="lab">A</text>
              <text x="1400" y={LENS_Y - 14} className="lab end">A′</text>
              <text x={LENS_X + 12} y="124" className="lab">B</text>
              <text x={LENS_X + 12} y="1006" className="lab">B′</text>
              <path d="M136 92h28M150 78v28M1756 1000h28M1770 986v28" className="cross" />
            </svg>
            <div className="sl-bin left"><div className="sl-bin-scroll">{[...BIN_LEFT, ...BIN_LEFT].map((r, i) => <span key={i}>{r}</span>)}</div></div>
            <div className="sl-bin right"><div className="sl-bin-scroll">{[...BIN_RIGHT, ...BIN_RIGHT].map((r, i) => <span key={i}>{r}</span>)}</div><i className="sl-readhead" /></div>
          </div>
          <div className="sl-chrome" aria-hidden="true">
            <div className="sl-frame"><i /><i /><i /><i /></div>
            <div className="sl-strip top">
              <span className="sys"><b>AMADEUS</b> 观测终端 · OBSERVATION TERMINAL</span>
              <span className="clock">{clock}</span>
            </div>
            <div className="sl-strip bottom">
              <span>LOCAL LINK · 记忆仅保存在这台设备上</span>
              <span className="hint"><kbd>Enter ↵</kbd> 连接</span>
            </div>
          </div>
          <div className="sl-vignette" aria-hidden="true" />
        </div>

        <LightIris ref={lensRef} driver={rootRef} attention={attention} exiting={exiting && !reduced} reduced={reduced} worldline={worldline} />

        <div className="sl-stage sl-stage-front">
          <div className="sl-astro-wrap"><Astrolabe worldline={worldline} /></div>
          <h1 className="sl-word" aria-label="AMADEUS">
            {'AMADEUS'.split('').map((ch, i) => <span key={i} aria-hidden="true" style={{ '--i': i } as React.CSSProperties}>{ch}</span>)}
          </h1>

          <form onSubmit={handleSubmit} className="sl-form salieri-login-form" noValidate>
            <div ref={userRowRef} className={`sl-row user${errorField === 'user' ? ' is-error' : ''}`}>
              <label className="sl-label" htmlFor="salieri-user">USER ID</label>
              <div className="sl-slot">
                <input
                  id="salieri-user"
                  type="text"
                  className="sl-input user-id-input"
                  value={userId}
                  onChange={(e) => { setUserId(e.target.value); lensRef.current?.twitch(); }}
                  onFocus={() => setFocusField('user')}
                  onBlur={() => setFocusField((f) => (f === 'user' ? null : f))}
                  disabled={isAuthenticating}
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={errorField === 'user' || undefined}
                />
                <i className="sl-corners" aria-hidden="true" />
              </div>
            </div>

            <div ref={pwdRowRef} className={`sl-row pwd${errorField === 'password' ? ' is-error' : ''}`}>
              <label className="sl-label" htmlFor="salieri-pwd">PASSWORD</label>
              <div className="sl-slot">
                <input
                  ref={pwdRef}
                  id="salieri-pwd"
                  type="password"
                  className="sl-input password-input"
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); lensRef.current?.twitch(); }}
                  onFocus={() => setFocusField('password')}
                  onBlur={() => { setFocusField((f) => (f === 'password' ? null : f)); setCapsLock(false); }}
                  onKeyDown={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                  onKeyUp={(e) => setCapsLock(e.getModifierState?.('CapsLock') ?? false)}
                  disabled={isAuthenticating}
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={errorField === 'password' || undefined}
                />
                {capsLock && <span className="sl-caps" aria-hidden="true">CAPS</span>}
                <i className="sl-corners" aria-hidden="true" />
              </div>
              <button
                type="submit"
                className={`decrypt-btn sl-go${busy ? ' is-busy' : ''}${exiting ? ' is-ok' : ''}`}
                disabled={isAuthenticating || backendStatus !== 'ready'}
                title="CONNECT"
                aria-label="Decrypt and Connect"
              >
                <svg viewBox="0 0 64 64" aria-hidden="true">
                  <circle cx="32" cy="32" r="27.5" className="ring" />
                  <g className="ticks">
                    {Array.from({ length: 24 }, (_, i) => <path key={i} d={`M32 .5v${i % 6 === 0 ? 3.6 : 2}`} transform={`rotate(${i * 15} 32 32)`} />)}
                  </g>
                  <circle cx="32" cy="32" r="27.5" className="arc" pathLength={1} />
                  <path d="M24.5 32h14M33 26.5l5.5 5.5-5.5 5.5" className="arrow" />
                </svg>
              </button>
            </div>

            <div className="sl-msg-line">
              {backendStatus !== 'ready' && (
                <p className="sl-msg backend-status-banner" role="status">
                  <i aria-hidden="true" />{backendStatus === 'starting' ? '正在启动本地服务…' : '本地服务没有启动，请重新打开 Amadeus'}
                </p>
              )}
              {errorMsg && (
                <p key={errorSeq} className="sl-msg is-error" role="alert">
                  <b aria-hidden="true">REJECTED</b>{errorMsg}
                </p>
              )}
            </div>
          </form>
        </div>
      </div>
      <i className="sl-cue" aria-hidden="true" />
    </div>
  );
};
